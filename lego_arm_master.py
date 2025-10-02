from __future__ import annotations

import os
import time
import uuid
import json
import threading
import queue
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import urlparse
from typing import Dict, Optional, Literal, Union
import mimetypes
import re
import logging
from logging.handlers import RotatingFileHandler

# location of bundled web UI
WEB_DIR = os.path.join(os.path.dirname(__file__), "web")

from processes import PROCESS_MAP

# Configure rotating file logger in the same directory as this script so logs
# stay beside the code regardless of the working directory.
logger = logging.getLogger("lego_arm")
logger.setLevel(logging.INFO)
_log_path = os.path.join(os.path.dirname(__file__), "lego_arm_master.log")
_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s:%(lineno)d %(message)s")
try:  # pragma: no cover - filesystem may be read-only
    _handler = RotatingFileHandler(_log_path, maxBytes=1_000_000, backupCount=3)
    _handler.setFormatter(_formatter)
    logger.addHandler(_handler)
except Exception as e:  # pragma: no cover - logging setup failure
    _fallback = logging.StreamHandler()
    _fallback.setFormatter(_formatter)
    logger.addHandler(_fallback)
    logger.error("Failed to initialize file logger: %s", e)

# Optional Bluetooth gamepad support (requires `evdev`)
try:  # pragma: no cover - optional dependency
    from evdev import InputDevice, list_devices, ecodes  # type: ignore
except Exception:  # pragma: no cover - device might not exist
    InputDevice = None  # type: ignore
    list_devices = None  # type: ignore
    ecodes = None  # type: ignore

# ---------------------------
# Hardware abstraction layer
# ---------------------------

USE_FAKE = os.getenv("USE_FAKE_MOTORS", "0") == "1"

try:
    if not USE_FAKE:
        # Build HAT library (installed on Raspberry Pi)
        from buildhat import Motor  # type: ignore
    else:
        raise ImportError("Using fake motors by env override")
except Exception:
    class Motor:  # fake motor for dev
        def __init__(self, port: str):
            self.port = port
            self._pos = 0.0  # pseudo degrees
            self.stop_action = "brake"
        def run_for_degrees(self, degrees: float, speed: int = 50, blocking: bool = True):
            self._pos += degrees
            if blocking:
                time.sleep(min(abs(degrees) / 360.0, 0.2))
        def run_for_rotations(self, rotations: float, speed: int = 50, blocking: bool = True):
            self.run_for_degrees(rotations * 360.0, speed, blocking)
        def stop(self):
            pass
        def float(self):
            pass
        def set_default_stop_action(self, action: str):
            self.stop_action = action
        def get_degrees(self):
            return self._pos

# ---------------------------
# Controller
# ---------------------------

class ArmController:
    def __init__(self):
        self.motors: Dict[str, Motor] = {
            "A": Motor("A"),  # gripper
            "B": Motor("B"),  # wrist
            "C": Motor("C"),  # elbow
            "D": Motor("D"),  # rotation
        }
        self.current_abs: Dict[str, float] = {k: 0.0 for k in self.motors}
        # Backlash compensation (degrees) for direction changes per motor.  The
        # values persist in ``arm_calibration.json`` and may be overridden at
        # startup via env vars ``ARM_BACKLASH_A``..``D``.  Useful when gears
        # have slack and initial motion doesn't move the joint.
        self._calib_path = os.path.join(os.path.dirname(__file__), "arm_calibration.json")
        self.backlash: Dict[str, float] = {j: 0.0 for j in self.motors}
        # Degrees the motor must rotate for one full joint rotation. Defaults to
        # 360° but can be tuned per motor via the admin UI.
        self.rotation_deg: Dict[str, float] = {j: 360.0 for j in self.motors}
        # Empirical degrees-per-second scale factor per motor.  Used only to
        # estimate generous timeout budgets when callers opt-in.
        self.speed_deg_per_sec: Dict[str, float] = {j: 6.0 for j in self.motors}
        # Track last movement direction per motor: -1, 0, 1
        self._last_dir: Dict[str, int] = {j: 0 for j in self.motors}
        # Named points per joint (e.g., "closed", "home").  Populated after
        # calibration and persisted in ``arm_calibration.json``.
        self.points: Dict[str, Dict[str, float]] = {j: {} for j in self.motors}
        self._last_move_summary: dict = {
            "units": None,
            "commanded": {},
            "converted_degrees": {},
            "speed": 0,
            "timeout_s": None,
            "elapsed_s": 0.0,
            "final_error_deg": {},
            "finalized": False,
            "finalize_deadband_deg": 0.0,
            "finalize_corrections": {},
            "timeout": False,
        }
        try:
            with open(self._calib_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.backlash.update({j: float(data.get("backlash", {}).get(j, 0.0)) for j in self.motors})
            self.rotation_deg.update({j: float(data.get("rotation", {}).get(j, 360.0)) for j in self.motors})
            self._last_dir.update({j: int(data.get("last_dir", {}).get(j, 0)) for j in self.motors})
            scale = data.get("speed_scale", {})
            for j in self.motors:
                try:
                    self.speed_deg_per_sec[j] = float(scale.get(j, self.speed_deg_per_sec[j]))
                except (TypeError, ValueError):
                    pass
            pts = data.get("points", {})
            for j, mp in pts.items():
                if j in self.points:
                    try:
                        self.points[j] = {k: float(v) for k, v in mp.items()}
                    except Exception:
                        pass
        except FileNotFoundError:
            pass
        except Exception:
            pass
        for j in self.motors:
            env = os.getenv(f"ARM_BACKLASH_{j}")
            if env is not None:
                try:
                    self.backlash[j] = float(env)
                except ValueError:
                    pass
        for j in self.rotation_deg:
            if not isinstance(self.rotation_deg[j], (int, float)) or self.rotation_deg[j] <= 0:
                self.rotation_deg[j] = 360.0
        self.save_calibration()
        # Limit definitions for each joint.  When a joint has ``None`` limits it
        # can rotate freely without clamping.  Previously the controller always
        # enforced +/-180 or +/-360 degree limits which prevented rotations
        # beyond those values.  Setting ``None`` for all joints effectively
        # removes those limits and allows unrestricted movement while still
        # keeping the ability to define limits in the future if desired.
        self.limits: Dict[str, Optional[tuple[float, float]]] = {
            "A": None,
            "B": None,
            "C": None,
            "D": None,
        }
        # Calibration status. Named points are captured per joint via the UI and
        # persisted. ``calibrated`` becomes True once all required points are
        # recorded and limits/home are derived.
        self.calibrated: bool = False
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self._busy_owner: Optional[int] = None
        self._busy_count = 0

    def clamp(self, joint: str, value: float) -> float:
        limits = self.limits.get(joint)
        if limits is None:
            return value
        lo, hi = limits
        return max(lo, min(hi, value))

    def stop_all(self):
        self.stop_event.set()
        for m in self.motors.values():
            try:
                m.stop()
            except Exception:
                pass

    def _acquire_busy(self) -> bool:
        tid = threading.get_ident()
        with self.lock:
            if self._busy_owner not in (None, tid):
                return False
            self._busy_owner = tid
            self._busy_count += 1
            return True

    def _release_busy(self) -> None:
        tid = threading.get_ident()
        with self.lock:
            if self._busy_owner == tid:
                self._busy_count -= 1
                if self._busy_count <= 0:
                    self._busy_owner = None
                    self._busy_count = 0

    def coast(self, motors: Optional[list[str]] = None, enable: bool = True):
        targets = motors or list(self.motors.keys())
        with self.lock:
            for j in targets:
                m = self.motors.get(j)
                if not m:
                    continue
                action = "coast" if enable else "brake"
                if hasattr(m, "set_default_stop_action"):
                    try:
                        m.set_default_stop_action(action)
                    except Exception:
                        pass
                if enable and hasattr(m, "float"):
                    try:
                        m.float()
                        continue
                    except Exception:
                        pass
                try:
                    m.stop()
                except Exception:
                    pass
                if not enable:
                    getter = getattr(m, "get_degrees", None) or getattr(m, "get_position", None)
                    if getter:
                        try:
                            self.current_abs[j] = float(getter())
                        except Exception:
                            pass
        return {"motors": targets, "coast": enable}

    def save_calibration(self) -> None:
        try:
            with open(self._calib_path, "w", encoding="utf-8") as f:
                json.dump({
                    "backlash": self.backlash,
                    "rotation": self.rotation_deg,
                    "last_dir": self._last_dir,
                    "speed_scale": self.speed_deg_per_sec,
                    "points": self.points,
                }, f)
        except Exception:
            pass

    def set_backlash(self, values: Dict[str, float]) -> dict:
        with self.lock:
            for j, v in values.items():
                if j in self.motors:
                    try:
                        self.backlash[j] = float(v)
                    except (TypeError, ValueError):
                        continue
            self.save_calibration()
            return {"backlash": self.backlash.copy()}

    def set_rotation(self, values: Dict[str, float]) -> dict:
        with self.lock:
            for j, v in values.items():
                if j in self.motors:
                    try:
                        self.rotation_deg[j] = float(v)
                    except (TypeError, ValueError):
                        continue
            self.save_calibration()
            return {"rotation": self.rotation_deg.copy()}

    def last_direction(self) -> dict:
        return self._last_dir.copy()

    def resolve_point(self, joint: str, value: str) -> float:
        """Return absolute degrees for a named point expression.

        ``value`` may be a point name like ``"closed"`` or an expression
        ``"closed - 10"``.  Names are case-insensitive and may contain spaces
        which will be normalized to underscores.
        """
        m = re.fullmatch(r"\s*([A-Za-z_ ]+?)(?:\s*([+-])\s*([0-9]+(?:\.[0-9]+)?))?\s*", value)
        if not m:
            raise ValueError(f"Invalid point expression '{value}'")
        base = m.group(1).strip().lower().replace(" ", "_")
        offset = float(m.group(3)) if m.group(3) else 0.0
        if m.group(2) == '-':
            offset = -offset
        base_val = self.points.get(joint, {}).get(base)
        if base_val is None:
            raise ValueError(f"Unknown point '{base}' for joint {joint}")
        return base_val + offset

    def move(
        self,
        mode: Literal["relative", "absolute"],
        joints: Dict[str, Union[float, str]],
        speed: int = 60,
        units: Literal["rotations", "degrees"] = "degrees",
        timeout_s: Optional[float] = None,
        finalize: bool = True,
        finalize_deadband_deg: float = 2.0,
    ):
        """Move the requested joints using deterministic, unit-aware semantics."""

        mode = str(mode).lower()
        if mode not in {"relative", "absolute"}:
            raise ValueError("mode must be 'relative' or 'absolute'")
        units = str(units).lower()
        if units not in {"rotations", "degrees"}:
            raise ValueError("units must be 'rotations' or 'degrees'")
        speed = int(speed)
        timeout_val = None if timeout_s is None else float(timeout_s)

        logger.info(
            "Move command mode=%s units=%s speed=%s timeout=%s finalize=%s joints=%s",
            mode,
            units,
            speed,
            timeout_val,
            finalize,
            joints,
        )

        if not self._acquire_busy():
            raise RuntimeError("BUSY")

        start_time = time.time()
        timeout_triggered = False

        try:
            with self.lock:
                if self.stop_event.is_set():
                    self.stop_event.clear()
                    raise InterruptedError("Movement interrupted")

                commanded: Dict[str, Union[float, str]] = {}
                converted: Dict[str, float] = {}
                targets: Dict[str, float] = {}
                start_positions: Dict[str, float] = {}
                motors_used: Dict[str, Motor] = {}

                for joint, raw in joints.items():
                    if joint not in self.motors:
                        raise ValueError(f"Unknown joint '{joint}'")
                    current = self.current_abs[joint]
                    start_positions[joint] = current
                    motors_used[joint] = self.motors[joint]

                    if isinstance(raw, str):
                        target = self.resolve_point(joint, raw)
                        commanded[joint] = raw
                    else:
                        value = float(raw)
                        commanded[joint] = value
                        if units == "rotations":
                            value_deg = value * self.rotation_deg.get(joint, 360.0)
                            target = current + value_deg if mode == "relative" else value_deg
                        else:  # degrees
                            if mode == "relative":
                                value_deg = value
                                target = current + value_deg
                            else:
                                value_deg = value
                                target = value_deg
                    target = self.clamp(joint, target)
                    converted[joint] = target - current
                    targets[joint] = target

                expected_per_joint: Dict[str, float] = {}
                for joint, delta in converted.items():
                    if abs(delta) < 1e-6:
                        expected_per_joint[joint] = 0.0
                        continue
                    deg_per_s = max(1.0, abs(self.speed_deg_per_sec.get(joint, 6.0) * max(speed, 1)))
                    expected_per_joint[joint] = abs(delta) / deg_per_s

                deadline = None
                if timeout_val is not None:
                    total_expected = max(expected_per_joint.values() or [0.0])
                    recommended = 3.0 + 1.2 * total_expected
                    if timeout_val < recommended:
                        logger.warning(
                            "timeout %.2fs shorter than recommended %.2fs (expected %.2fs)",
                            timeout_val,
                            recommended,
                            total_expected,
                        )
                    deadline = start_time + timeout_val

                for joint, target in targets.items():
                    delta = target - start_positions[joint]
                    if abs(delta) < 1e-6:
                        continue
                    direction = 1 if delta > 0 else -1
                    backlash = self.backlash.get(joint, 0.0)
                    run_delta = delta
                    if direction != 0 and direction != self._last_dir[joint]:
                        run_delta += backlash * direction
                    motor = motors_used[joint]
                    max_chunk = 12000.0
                    remaining = run_delta
                    chunks = []
                    while abs(remaining) > max_chunk:
                        chunk = max_chunk if remaining > 0 else -max_chunk
                        chunks.append(chunk)
                        remaining -= chunk
                    chunks.append(remaining)
                    for idx, chunk in enumerate(chunks, 1):
                        if self.stop_event.is_set():
                            self.stop_event.clear()
                            raise InterruptedError("Movement interrupted")
                        if deadline is not None and time.time() > deadline:
                            timeout_triggered = True
                            try:
                                motor.stop()
                            except Exception:
                                pass
                            self._last_dir[joint] = direction
                            self.stop_event.clear()
                            raise TimeoutError(
                                f"Movement timed out after {timeout_val:.2f}s"
                            )
                        logger.info(
                            "Moving joint %s by %.2f degrees at speed %d (%d/%d)",
                            joint,
                            chunk,
                            speed,
                            idx,
                            len(chunks),
                        )
                        motor.run_for_degrees(chunk, speed=speed, blocking=True)
                    self._last_dir[joint] = direction

                self.stop_event.clear()

                final_positions: Dict[str, float] = {}
                final_errors: Dict[str, float] = {}
                finalize_corrections: Dict[str, float] = {}

                def read_position(mtr: Motor) -> Optional[float]:
                    getter = getattr(mtr, "get_degrees", None) or getattr(mtr, "get_position", None)
                    if getter:
                        try:
                            return float(getter())
                        except Exception:
                            return None
                    return None

                for joint, target in targets.items():
                    motor = motors_used[joint]
                    actual = read_position(motor)
                    if actual is None:
                        actual = target
                    error = target - actual
                    correction = 0.0
                    if finalize and abs(error) > finalize_deadband_deg:
                        correction = error
                        corr_speed = max(20, min(60, max(1, speed // 2)))
                        logger.info(
                            "Finalizing joint %s with %.2f° correction at speed %d",
                            joint,
                            correction,
                            corr_speed,
                        )
                        motor.run_for_degrees(correction, speed=corr_speed, blocking=True)
                        actual_after = read_position(motor)
                        if actual_after is not None:
                            actual = actual_after
                        error = target - actual
                    final_positions[joint] = actual
                    final_errors[joint] = error
                    finalize_corrections[joint] = correction
                    self.current_abs[joint] = actual

                self.save_calibration()

            elapsed = time.time() - start_time
            summary = {
                "new_abs": self.current_abs.copy(),
                "units": units,
                "commanded": commanded,
                "converted_degrees": converted,
                "speed": speed,
                "timeout_s": timeout_val,
                "elapsed_s": elapsed,
                "final_error_deg": final_errors,
                "finalized": finalize,
                "finalize_deadband_deg": finalize_deadband_deg,
                "finalize_corrections": finalize_corrections,
                "timeout": timeout_triggered,
            }
            self._last_move_summary = summary
            logger.info(
                "Move complete elapsed=%.3fs converted=%s final_err=%s finalize_corr=%s",
                elapsed,
                converted,
                final_errors,
                finalize_corrections,
            )
            return summary
        finally:
            self._release_busy()

    def last_move_summary(self) -> dict:
        with self.lock:
            summary = self._last_move_summary.copy()
            for key in ("new_abs", "commanded", "converted_degrees", "final_error_deg", "finalize_corrections"):
                if key in summary and isinstance(summary[key], dict):
                    summary[key] = summary[key].copy()
            return summary

    # ----- Calibration helpers -----
    def record_named_point(self, joint: str, name: str) -> dict:
        """Store the current position of ``joint`` under ``name``."""
        with self.lock:
            if joint not in self.motors:
                raise ValueError(f"Unknown joint '{joint}'")
            norm = name.strip().lower().replace(" ", "_")
            self.points[joint][norm] = self.current_abs[joint]
            self.save_calibration()
            return {"points": {j: pts.copy() for j, pts in self.points.items()}}

    def reset_calibration(self) -> dict:
        """Clear recorded calibration points, reset limits and mark arm uncalibrated."""
        with self.lock:
            self.points = {j: {} for j in self.motors}
            # Remove any soft limits so joints can move freely until finalized
            self.limits = {j: None for j in self.motors}
            self.calibrated = False
            self.save_calibration()
            return self.calibration_status()

    def finalize_calibration(self) -> dict:
        """Derive joint limits and home pose from recorded named points and move."""
        if not self._acquire_busy():
            raise RuntimeError("BUSY")
        try:
            required = {
                "A": {"open", "closed"},
                "B": {"min", "pick", "max"},
                "C": {"min", "pick", "max"},
                "D": {"assembly", "neutral", "quality"},
            }
            with self.lock:
                for j, names in required.items():
                    missing = [n for n in names if n not in self.points.get(j, {})]
                    if missing:
                        raise ValueError(
                            f"Missing points for joint {j}: {', '.join(sorted(missing))}"
                        )
                pts = self.points
                self.limits = {
                    "A": (
                        min(pts["A"]["open"], pts["A"]["closed"]),
                        max(pts["A"]["open"], pts["A"]["closed"]),
                    ),
                    "B": (
                        min(pts["B"]["min"], pts["B"]["max"]),
                        max(pts["B"]["min"], pts["B"]["max"]),
                    ),
                    "C": (
                        min(pts["C"]["min"], pts["C"]["max"]),
                        max(pts["C"]["min"], pts["C"]["max"]),
                    ),
                    "D": (
                        min(
                            pts["D"]["assembly"],
                            pts["D"]["neutral"],
                            pts["D"]["quality"],
                        ),
                        max(
                            pts["D"]["assembly"],
                            pts["D"]["neutral"],
                            pts["D"]["quality"],
                        ),
                    ),
                }
                home = {
                    "A": pts["A"]["open"],
                    "B": pts["B"]["min"],
                    "C": pts["C"]["max"],
                    "D": pts["D"]["neutral"],
                }
                self.calibrated = True
                self.save_calibration()
            # Move to the derived home position outside the lock
            self.move("absolute", home, speed=40)
            return {
                "limits": self.limits.copy(),
                "home": home,
                "points": {j: pts.copy() for j, pts in self.points.items()},
            }
        finally:
            self._release_busy()

    def calibration_status(self) -> dict:
        with self.lock:
            return {
                "points": {j: pts.copy() for j, pts in self.points.items()},
                "calibrated": self.calibrated,
            }

    def goto_pose(self, name: str, speed: int):
        poses = {
            "home": {"A": 0, "B": 0, "C": 0, "D": 0},
            "pick_left": {"D": -60, "B": -20, "C": 30, "A": 20},
            "pick_right": {"D": 60, "B": -20, "C": 30, "A": 20},
            "place_left": {"D": -60, "B": 10, "C": -10, "A": -5},
            "place_right": {"D": 60, "B": 10, "C": -10, "A": -5},
        }
        if name not in poses:
            raise ValueError(f"Unknown pose '{name}'")
        if not self._acquire_busy():
            raise RuntimeError("BUSY")
        try:
            return self.move("absolute", poses[name], speed=speed)
        finally:
            self._release_busy()

    def pickplace(self, location: str, action: str, speed: int):
        seq_pose = {
            ("left", "pick"): ["pick_left", "home"],
            ("left", "place"): ["place_left", "home"],
            ("right", "pick"): ["pick_right", "home"],
            ("right", "place"): ["place_right", "home"],
            ("center", "pick"): ["home"],
            ("center", "place"): ["home"],
        }
        steps = seq_pose.get((location, action))
        if not steps:
            raise ValueError("Unsupported pick/place combination")
        if not self._acquire_busy():
            raise RuntimeError("BUSY")
        try:
            result = None
            for p in steps:
                result = self.goto_pose(p, speed)
            grip = {"pick": -30, "place": 30}[action]
            result = self.move("relative", {"A": grip}, speed)
            return result
        finally:
            self._release_busy()

    def resolve_pose(self, pose: Dict[str, Union[str, float]]) -> Dict[str, float]:
        abs_pose: Dict[str, float] = {}
        for j, v in pose.items():
            if isinstance(v, str):
                abs_pose[j] = self.resolve_point(j, v)
            else:
                abs_pose[j] = float(v)
        return abs_pose

    def verify_at(self, target: Dict[str, float], tol_map: Optional[Dict[str, float]] = None) -> tuple[bool, Dict[str, float]]:
        tol = {"A": 2.0, "B": 3.0, "C": 3.0, "D": 3.0}
        if tol_map:
            tol.update(tol_map)
        errs = {j: abs(self.current_abs.get(j, 0.0) - target.get(j, 0.0)) for j in target}
        ok = all(errs[j] <= tol[j] for j in target)
        return ok, errs

    def recover_to_home(self, speed: int = 30, timeout_s: float = 90.0):
        if not self._acquire_busy():
            raise RuntimeError("BUSY")
        try:
            self.stop_all()
            time.sleep(0.2)
            self.coast(enable=False)
            with self.lock:
                for j, m in self.motors.items():
                    getter = getattr(m, "get_degrees", None) or getattr(m, "get_position", None)
                    if getter:
                        try:
                            self.current_abs[j] = float(getter())
                        except Exception:
                            pass
                if self.calibrated and "D" in self.points and "neutral" in self.points["D"]:
                    home = {
                        "A": self.points["A"].get("open", self.current_abs.get("A", 0.0)),
                        "B": self.points["B"].get("min", self.current_abs.get("B", 0.0)),
                        "C": self.points["C"].get("max", self.current_abs.get("C", 0.0)),
                        "D": self.points["D"].get("neutral", self.current_abs.get("D", 0.0)),
                    }
                else:
                    home = {"A": 0, "B": 0, "C": 0, "D": 0}
            self.stop_event.clear()
            return self.move("absolute", home, speed=speed, timeout_s=timeout_s)
        finally:
            self._release_busy()

    def state(self) -> dict:
        return {
            "abs_degrees": self.current_abs.copy(),
            "limits": self.limits.copy(),
            "motors": list(self.motors.keys()),
            "backlash": self.backlash.copy(),
            "rotation": self.rotation_deg.copy(),
            "calibrated": self.calibrated,
            "points": {j: pts.copy() for j, pts in self.points.items()},
        }

arm = ArmController()
# ---------------------------
# Bluetooth gamepad control
# ---------------------------

def _gamepad_loop(device_path: Optional[str] = None) -> None:  # pragma: no cover - hardware dependent
    """Read joystick events and translate into motor movements."""
    if InputDevice is None or list_devices is None or ecodes is None:
        logger.info("evdev not available; gamepad disabled")
        return
    path = device_path
    if not path:
        devices = list_devices()
        if not devices:
            logger.info("No gamepad device found")
            return
        path = devices[0]
    try:
        dev = InputDevice(path)
    except Exception as e:
        logger.error("Failed to open gamepad %s: %s", path, e)
        return
    axis_map = {
        ecodes.ABS_X: "D",  # base rotation
        ecodes.ABS_Y: "C",  # elbow
        ecodes.ABS_RX: "B",  # wrist
        ecodes.ABS_RY: "A",  # gripper
    }
    abs_ranges: Dict[int, tuple[int, int]] = {}
    for code in axis_map:
        try:
            info = dev.absinfo(code)
            abs_ranges[code] = (info.min, info.max)
        except Exception:
            abs_ranges[code] = (-32768, 32767)
    for event in dev.read_loop():
        if event.type != ecodes.EV_ABS or event.code not in axis_map:
            continue
        lo, hi = abs_ranges[event.code]
        mid = (lo + hi) / 2.0
        span = (hi - lo) / 2.0 or 1.0
        norm = (event.value - mid) / span
        if abs(norm) < 0.1:
            continue
        joint = axis_map[event.code]
        deg = norm * 5.0  # small step per event
        speed = max(10, int(abs(norm) * 100))
        if not arm._acquire_busy():
            continue
        try:
            arm.move("relative", {joint: deg}, speed=speed, units="degrees")
        except Exception:
            pass
        finally:
            arm._release_busy()


def _start_gamepad_thread() -> threading.Thread | None:
    device = os.getenv("GAMEPAD_DEVICE")
    t = threading.Thread(target=_gamepad_loop, args=(device,), daemon=True)
    t.start()
    return t


if os.getenv("ENABLE_GAMEPAD", "0") == "1":
    _start_gamepad_thread()
# ---------------------------
# Ops & Idempotency
# ---------------------------

API_KEY = os.getenv("API_KEY", "change-me")
ALLOW_NO_AUTH_LOCAL = os.getenv("ALLOW_NO_AUTH_LOCAL", "0") == "1"

IDEMPOTENCY_CACHE_TTL = 60 * 5
_idem_cache: Dict[str, tuple[float, dict]] = {}
_idem_lock = threading.Lock()

op_queue: "queue.Queue[dict]" = queue.Queue()
ops: Dict[str, dict] = {}
ops_lock = threading.Lock()


def worker():
    while True:
        op = op_queue.get()
        if op is None:
            break
        try:
            op["status"] = "running"
            op["started_at"] = time.time()
            kind = op["type"]
            req = op["request"]
            logger.info("Starting operation %s of type %s", op.get("id"), kind)
            if kind == "move":
                deadband = req.get("finalize_deadband_deg", 2.0)
                try:
                    deadband_val = float(deadband)
                except (TypeError, ValueError):
                    deadband_val = 2.0
                res = arm.move(
                    req.get("mode", "relative"),
                    req["joints"],
                    speed=int(req.get("speed", 60)),
                    units=req.get("units", "degrees"),
                    timeout_s=req.get("timeout_s"),
                    finalize=req.get("finalize", True),
                    finalize_deadband_deg=deadband_val,
                )
            elif kind == "pose":
                res = arm.goto_pose(req["name"], int(req.get("speed", 60)))
            elif kind == "pickplace":
                res = arm.pickplace(req["location"], req["action"], int(req.get("speed", 60)))
            elif kind == "process":
                name = req["name"]
                proc = PROCESS_MAP[name]
                res = proc(arm)
            else:
                raise ValueError(f"Unknown op type {kind}")
            op["result"] = res
            op["status"] = "succeeded"
            logger.info("Operation %s succeeded", op.get("id"))
        except Exception as e:
            op["error"] = {"code": "EXECUTION_ERROR", "message": str(e)}
            op["status"] = "failed"
            logger.error("Operation %s failed: %s", op.get("id"), e)
        finally:
            op["finished_at"] = time.time()
            with ops_lock:
                ops[op["id"]] = op
            logger.info(
                "Finished operation %s with status %s", op.get("id"), op["status"]
            )
        op_queue.task_done()

worker_thread = threading.Thread(target=worker, daemon=True)
worker_thread.start()

# ---------------------------
# HTTP utils
# ---------------------------

def json_response(handler: BaseHTTPRequestHandler, payload: dict, status: int = 200):
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    # CORS
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "*")
    handler.send_header("Access-Control-Allow-Headers", "*")
    handler.end_headers()
    try:
        handler.wfile.write(body)
    except BrokenPipeError:
        # Client closed connection before we could reply; ignore
        pass


def parse_json(handler: BaseHTTPRequestHandler):
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return {}
    data = handler.rfile.read(length)
    try:
        return json.loads(data.decode("utf-8"))
    except Exception:
        return {}


def auth_ok(handler: BaseHTTPRequestHandler) -> Optional[tuple[dict, int]]:
    # allow localhost without key if configured
    if ALLOW_NO_AUTH_LOCAL and handler.client_address[0] in {"127.0.0.1", "::1"}:
        return None
    key = handler.headers.get("x-api-key")
    if not key:
        return ({"ok": False, "error": {"code": "NO_API_KEY", "message": "Provide x-api-key"}}, 401)
    if key != API_KEY:
        return ({"ok": False, "error": {"code": "BAD_API_KEY", "message": "Invalid x-api-key"}}, 401)
    return None


def idem_get(handler: BaseHTTPRequestHandler):
    key = handler.headers.get("X-Idempotency-Key")
    if not key:
        return None
    now = time.time()
    with _idem_lock:
        expired = [k for k, (t, _) in _idem_cache.items() if now - t > IDEMPOTENCY_CACHE_TTL]
        for k in expired:
            _idem_cache.pop(k, None)
        if key in _idem_cache:
            return _idem_cache[key][1]
    return None


def idem_store(handler: BaseHTTPRequestHandler, payload: dict):
    key = handler.headers.get("X-Idempotency-Key")
    if not key:
        return
    with _idem_lock:
        _idem_cache[key] = (time.time(), payload)

# ---------------------------
# Request handler
# ---------------------------

class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*, x-api-key, content-type, X-Idempotency-Key")
        self.end_headers()

    def do_GET(self):
        logger.info("GET %s from %s", self.path, self.client_address[0])
        parsed = urlparse(self.path)
        path = parsed.path
        if path in {"/", "/index.html", "/ui"}:
            return self.serve_ui()
        if path == "/v1/health":
            return json_response(self, {"ok": True, "data": {"status": "ok", "time": time.time()}})
        if path == "/v1/inventory":
            if (resp := auth_ok(self)):
                return json_response(self, resp[0], resp[1])
            process_eps = [f"POST /v1/processes/{name}" for name in PROCESS_MAP]
            data = {
                "endpoints": [
                    "GET /v1/health",
                    "GET /v1/inventory",
                    "GET /v1/arm/state",
                    "GET /v1/arm/backlash",
                    "GET /v1/arm/rotation",
                    "POST /v1/arm/move",
                    "POST /v1/arm/pose",
                    "POST /v1/arm/stop",
                    "POST /v1/arm/coast",
                    "POST /v1/arm/pickplace",
                    "POST /v1/arm/backlash",
                    "POST /v1/arm/rotation",
                    "POST /v1/arm/recover",
                    "GET /v1/operations/{id}",
                ] + process_eps,
                "poses": ["home", "pick_left", "pick_right", "place_left", "place_right"],
                "processes": list(PROCESS_MAP.keys()),
                "motors": list(arm.motors.keys()),
            }
            return json_response(self, {"ok": True, "data": data})
        if path == "/v1/arm/state":
            if (resp := auth_ok(self)):
                return json_response(self, resp[0], resp[1])
            return json_response(self, {"ok": True, "data": arm.state()})
        if path == "/v1/arm/backlash":
            if (resp := auth_ok(self)):
                return json_response(self, resp[0], resp[1])
            return json_response(self, {"ok": True, "data": {"backlash": arm.backlash.copy(), "last_dir": arm.last_direction()}})
        if path == "/v1/arm/rotation":
            if (resp := auth_ok(self)):
                return json_response(self, resp[0], resp[1])
            return json_response(self, {"ok": True, "data": {"rotation": arm.rotation_deg.copy()}})
        if path == "/v1/arm/last_move":
            if (resp := auth_ok(self)):
                return json_response(self, resp[0], resp[1])
            return json_response(self, {"ok": True, "data": arm.last_move_summary()})
        if path == "/v1/arm/calibration":
            if (resp := auth_ok(self)):
                return json_response(self, resp[0], resp[1])
            return json_response(self, {"ok": True, "data": arm.calibration_status()})
        if path.startswith("/v1/operations/"):
            if (resp := auth_ok(self)):
                return json_response(self, resp[0], resp[1])
            op_id = path.split("/v1/operations/")[-1]
            with ops_lock:
                op = ops.get(op_id)
            if not op:
                return json_response(self, {"ok": False, "error": {"code": "NOT_FOUND", "message": "Unknown operation id"}}, 404)
            return json_response(self, {"ok": True, "data": op})
        # attempt to serve static files from WEB_DIR
        if not path.startswith("/v1/"):
            rel_path = os.path.normpath(path.lstrip("/"))
            if rel_path and not rel_path.startswith(".."):
                static_path = os.path.join(WEB_DIR, rel_path)
                if os.path.isfile(static_path):
                    return self.serve_static(static_path)
        return json_response(self, {"ok": False, "error": {"code": "NOT_FOUND", "message": "Unknown path"}}, 404)

    def serve_ui(self):
        try:
            with open(os.path.join(WEB_DIR, "index.html"), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass
        except FileNotFoundError:
            json_response(self, {"ok": False, "error": {"code": "UI_MISSING", "message": "UI not found"}}, 500)

    def serve_static(self, filepath: str):
        try:
            with open(filepath, "rb") as f:
                body = f.read()
            ctype, _ = mimetypes.guess_type(filepath)
            self.send_response(200)
            self.send_header("Content-Type", ctype or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass
        except FileNotFoundError:
            return json_response(self, {"ok": False, "error": {"code": "NOT_FOUND", "message": "Unknown path"}}, 404)

    def do_POST(self):
        logger.info("POST %s from %s", self.path, self.client_address[0])
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/v1/processes/") or path in ("/v1/arm/move", "/v1/arm/pose", "/v1/arm/pickplace", "/v1/arm/stop", "/v1/arm/coast", "/v1/arm/backlash", "/v1/arm/calibration", "/v1/arm/recover", "/v1/arm/rotation"):
            if (resp := auth_ok(self)):
                return json_response(self, resp[0], resp[1])
        if path == "/v1/arm/stop":
            arm.stop_all()
            body = parse_json(self)
            return json_response(self, {"ok": True, "data": {"stopped": True, "reason": body.get("reason")}})

        if not arm.calibrated and (path.startswith("/v1/processes/") or path in ("/v1/arm/pose", "/v1/arm/pickplace")):
            return json_response(self, {"ok": False, "error": {"code": "NOT_CALIBRATED", "message": "Calibration required"}}, 400)

        cached = idem_get(self)
        if cached:
            return json_response(self, cached)

        try:
            body = parse_json(self)
            if path.startswith("/v1/processes/"):
                name = path.split("/v1/processes/")[-1]
                if name not in PROCESS_MAP:
                    return json_response(self, {"ok": False, "error": {"code": "UNKNOWN_PROCESS", "message": "Unknown process"}}, 404)
                op = {
                    "id": str(uuid.uuid4()),
                    "type": "process",
                    "status": "queued",
                    "submitted_at": time.time(),
                    "request": {"name": name},
                }
                with ops_lock:
                    ops[op["id"]] = op
                op_queue.put(op)
                resp = {"ok": True, "data": {"operation_id": op["id"], "status": op["status"]}}
                idem_store(self, resp)
                return json_response(self, resp)

            if path == "/v1/arm/move":
                mode = body.get("mode", "relative")
                joints = body.get("joints") or {}
                speed = int(body.get("speed", 60))
                timeout_s = body.get("timeout_s")
                units_present = "units" in body
                units = body.get("units") or "degrees"
                finalize = body.get("finalize", True)
                finalize_deadband = body.get("finalize_deadband_deg", 2.0)
                async_exec = bool(body.get("async_exec", True))
                if not isinstance(joints, dict) or not joints:
                    return json_response(self, {"ok": False, "error": {"code": "BAD_MOVE", "message": "Provide joints map"}}, 400)
                units = str(units).lower()
                if units not in {"degrees", "rotations"}:
                    return json_response(self, {"ok": False, "error": {"code": "BAD_UNITS", "message": "units must be 'degrees' or 'rotations'"}}, 400)
                if not units_present:
                    logger.warning("Move request missing units; defaulting to degrees")
                if timeout_s is not None:
                    try:
                        timeout_s = float(timeout_s)
                    except (TypeError, ValueError):
                        return json_response(self, {"ok": False, "error": {"code": "BAD_TIMEOUT", "message": "timeout_s must be a number"}}, 400)
                try:
                    finalize_deadband_val = float(finalize_deadband)
                except (TypeError, ValueError):
                    return json_response(self, {"ok": False, "error": {"code": "BAD_FINALIZE", "message": "finalize_deadband_deg must be numeric"}}, 400)
                request_payload = {
                    "mode": mode,
                    "joints": joints,
                    "speed": speed,
                    "units": units,
                    "finalize": bool(finalize),
                    "finalize_deadband_deg": finalize_deadband_val,
                }
                if timeout_s is not None:
                    request_payload["timeout_s"] = timeout_s
                if async_exec:
                    op = {
                        "id": str(uuid.uuid4()),
                        "type": "move",
                        "status": "queued",
                        "submitted_at": time.time(),
                        "request": request_payload,
                    }
                    with ops_lock:
                        ops[op["id"]] = op
                    op_queue.put(op)
                    resp = {"ok": True, "data": {"operation_id": op["id"], "status": op["status"]}}
                    idem_store(self, resp)
                    return json_response(self, resp)
                res = arm.move(
                    mode,
                    joints,
                    speed=speed,
                    units=units,
                    timeout_s=timeout_s,
                    finalize=bool(finalize),
                    finalize_deadband_deg=finalize_deadband_val,
                )
                resp = {"ok": True, "data": res}
                idem_store(self, resp)
                return json_response(self, resp)

            if path == "/v1/arm/pose":
                name = body.get("name")
                speed = int(body.get("speed", 60))
                async_exec = bool(body.get("async_exec", True))
                if not name:
                    return json_response(self, {"ok": False, "error": {"code": "BAD_POSE", "message": "Provide pose name"}}, 400)
                if async_exec:
                    op = {
                        "id": str(uuid.uuid4()),
                        "type": "pose",
                        "status": "queued",
                        "submitted_at": time.time(),
                        "request": body,
                    }
                    with ops_lock:
                        ops[op["id"]] = op
                    op_queue.put(op)
                    resp = {"ok": True, "data": {"operation_id": op["id"], "status": op["status"]}}
                    idem_store(self, resp)
                    return json_response(self, resp)
                res = arm.goto_pose(name, speed)
                resp = {"ok": True, "data": res}
                idem_store(self, resp)
                return json_response(self, resp)

            if path == "/v1/arm/coast":
                motors = body.get("motors")
                if motors is not None and not isinstance(motors, list):
                    return json_response(self, {"ok": False, "error": {"code": "BAD_COAST", "message": "motors must be list"}}, 400)
                enable = bool(body.get("enable", True))
                res = arm.coast(motors, enable)
                resp = {"ok": True, "data": res}
                idem_store(self, resp)
                return json_response(self, resp)

            if path == "/v1/arm/pickplace":
                location = body.get("location", "center")
                action = body.get("action")
                speed = int(body.get("speed", 60))
                async_exec = bool(body.get("async_exec", True))
                if action not in {"pick", "place"}:
                    return json_response(self, {"ok": False, "error": {"code": "BAD_PICKPLACE", "message": "action must be 'pick' or 'place'"}}, 400)
                if async_exec:
                    op = {
                        "id": str(uuid.uuid4()),
                        "type": "pickplace",
                        "status": "queued",
                        "submitted_at": time.time(),
                        "request": body,
                    }
                    with ops_lock:
                        ops[op["id"]] = op
                    op_queue.put(op)
                    resp = {"ok": True, "data": {"operation_id": op["id"], "status": op["status"]}}
                    idem_store(self, resp)
                    return json_response(self, resp)
                res = arm.pickplace(location, action, speed)
                resp = {"ok": True, "data": res}
                idem_store(self, resp)
                return json_response(self, resp)

            if path == "/v1/arm/backlash":
                vals = body.get("backlash") if isinstance(body, dict) else None
                if vals is None:
                    vals = body
                if not isinstance(vals, dict):
                    return json_response(self, {"ok": False, "error": {"code": "BAD_BACKLASH", "message": "Provide backlash map"}}, 400)
                res = arm.set_backlash(vals)
                res["last_dir"] = arm.last_direction()
                resp = {"ok": True, "data": res}
                idem_store(self, resp)
                return json_response(self, resp)

            if path == "/v1/arm/rotation":
                vals = body.get("rotation") if isinstance(body, dict) else None
                if vals is None:
                    vals = body
                if not isinstance(vals, dict):
                    return json_response(self, {"ok": False, "error": {"code": "BAD_ROTATION", "message": "Provide rotation map"}}, 400)
                res = arm.set_rotation(vals)
                resp = {"ok": True, "data": res}
                idem_store(self, resp)
                return json_response(self, resp)

            if path == "/v1/arm/calibration":
                if body.get("reset"):
                    res = arm.reset_calibration()
                    resp = {"ok": True, "data": res}
                    idem_store(self, resp)
                    return json_response(self, resp)
                if body.get("finalize"):
                    try:
                        res = arm.finalize_calibration()
                        resp = {"ok": True, "data": res}
                        idem_store(self, resp)
                        return json_response(self, resp)
                    except Exception as e:
                        return json_response(self, {"ok": False, "error": {"code": "CALIB_ERROR", "message": str(e)}}, 400)
                if body.get("joint") and body.get("name"):
                    res = arm.record_named_point(str(body["joint"]), str(body["name"]))
                    resp = {"ok": True, "data": res}
                    idem_store(self, resp)
                    return json_response(self, resp)
                return json_response(
                    self,
                    {
                        "ok": False,
                        "error": {"code": "BAD_CALIB", "message": "Provide joint/name or finalize"},
                    },
                    400,
                )

            if path == "/v1/arm/recover":
                speed = int(body.get("speed", 30))
                timeout_s = body.get("timeout_s", 90.0)
                res = arm.recover_to_home(speed=speed, timeout_s=timeout_s)
                resp = {"ok": True, "data": res}
                idem_store(self, resp)
                return json_response(self, resp)

            return json_response(self, {"ok": False, "error": {"code": "NOT_FOUND", "message": "Unknown path"}}, 404)
        except RuntimeError as e:
            if str(e) == "BUSY":
                return json_response(self, {"ok": False, "error": {"code": "BUSY", "message": "Arm is executing another command"}}, 423)
            raise
        except TimeoutError as te:
            logger.error("Timeout handling POST %s: %s", path, te)
            return json_response(self, {"ok": False, "error": {"code": "TIMEOUT", "message": str(te)}}, 408)
        except Exception:
            logger.exception("Error handling POST %s", path)
            return json_response(self, {"ok": False, "error": {"code": "SERVER_ERROR", "message": "Internal server error"}}, 500)

# ---------------------------
# Entrypoint
# ---------------------------

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class DualStackThreadingHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self):
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (AttributeError, OSError):
            pass
        return super().server_bind()


def _create_server(host: str, port: int) -> HTTPServer:
    host = (host or "").strip()
    wants_dual = host in ("", "0.0.0.0", "::")

    if host and ":" in host:
        return DualStackThreadingHTTPServer((host, port), Handler)

    if wants_dual and socket.has_ipv6:
        try:
            return DualStackThreadingHTTPServer(("::", port), Handler)
        except OSError:
            logger.warning("IPv6 dual-stack bind failed, falling back to IPv4", exc_info=True)

    bind_host = host or "0.0.0.0"
    return ThreadingHTTPServer((bind_host, port), Handler)


def run_server():
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    server = _create_server(host, port)
    server_address = server.server_address
    if isinstance(server_address, tuple):
        if len(server_address) >= 2:
            display_host, display_port = server_address[0], server_address[1]
        else:
            display_host, display_port = server_address[0], port
    else:
        display_host, display_port = str(server_address), port
    host_for_log = f"[{display_host}]" if ":" in display_host else display_host
    logger.info("LEGO Arm REST listening on http://%s:%s", host_for_log, display_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server interrupted, shutting down")
    finally:
        server.server_close()

if __name__ == "__main__":
    run_server()
