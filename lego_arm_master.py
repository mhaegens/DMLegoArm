from __future__ import annotations

import os
import time
import uuid
import json
import threading
import queue
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse
from typing import Dict, Optional, Literal, Union
import mimetypes
import re

# location of bundled web UI
WEB_DIR = os.path.join(os.path.dirname(__file__), "web")

from processes import PROCESS_MAP

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
        # Track last movement direction per motor: -1, 0, 1
        self._last_dir: Dict[str, int] = {j: 0 for j in self.motors}
        # Named points per joint (e.g., "closed", "home").  Populated after
        # calibration and persisted in ``arm_calibration.json``.
        self.points: Dict[str, Dict[str, float]] = {j: {} for j in self.motors}
        try:
            with open(self._calib_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.backlash.update({j: float(data.get("backlash", {}).get(j, 0.0)) for j in self.motors})
            self._last_dir.update({j: int(data.get("last_dir", {}).get(j, 0)) for j in self.motors})
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
        # Calibration points captured from the UI.  ``calibrated`` becomes
        # True only after all points are recorded and limits/home are derived.
        self.calibration_points: Dict[str, Dict[str, float]] = {}
        self.calibrated: bool = False
        self.lock = threading.Lock()
        self.stop_event = threading.Event()

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
                json.dump({"backlash": self.backlash,
                           "last_dir": self._last_dir,
                           "points": self.points}, f)
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

    def move(self, mode: Literal["relative", "absolute"], joints: Dict[str, Union[float, str]], speed: int,
             timeout_s: Optional[float] = None, units: Literal["degrees", "rotations"] = "degrees"):
        start = time.time()
        with self.lock:
            if self.stop_event.is_set():
                self.stop_event.clear()
                raise InterruptedError("Movement interrupted")
            targets: Dict[str, float] = {}
            for j, raw in joints.items():
                if isinstance(raw, str):
                    target = self.resolve_point(j, raw)
                else:
                    deg = float(raw) * (360.0 if units == "rotations" else 1.0)
                    target = self.current_abs[j] + deg if mode == "relative" else deg
                targets[j] = self.clamp(j, target)
            plan = {j: tgt - self.current_abs[j] for j, tgt in targets.items()}
            for j, delta in plan.items():
                if self.stop_event.is_set():
                    self.stop_event.clear()
                    raise InterruptedError("Movement interrupted")
                if abs(delta) < 1e-6:
                    continue
                dir_now = 1 if delta > 0 else -1
                run_delta = delta
                backlash = self.backlash.get(j, 0.0)
                if backlash and self._last_dir[j] != 0 and dir_now != self._last_dir[j]:
                    run_delta += backlash * dir_now
                m = self.motors[j]
                m.run_for_degrees(run_delta, speed=speed, blocking=True)
                if self.stop_event.is_set():
                    self.stop_event.clear()
                    raise InterruptedError("Movement interrupted")
                self.current_abs[j] += delta
                self._last_dir[j] = dir_now
                if timeout_s and time.time() - start > timeout_s:
                    raise TimeoutError("Movement timed out")
            self.stop_event.clear()
            self.save_calibration()
        return {"new_abs": self.current_abs.copy()}

    # ----- Calibration helpers -----
    def record_calibration_point(self, name: str) -> dict:
        """Store the current absolute positions under ``name``."""
        with self.lock:
            self.calibration_points[str(name)] = self.current_abs.copy()
            return {"points": self.calibration_points.copy()}

    def finalize_calibration(self) -> dict:
        """Derive joint limits and home pose from recorded points and move."""
        required = {"p1", "p2", "p3", "p4"}
        with self.lock:
            if not required.issubset(self.calibration_points):
                missing = required - set(self.calibration_points)
                raise ValueError(f"Missing calibration points: {', '.join(sorted(missing))}")
            p1 = self.calibration_points["p1"]
            p2 = self.calibration_points["p2"]
            p3 = self.calibration_points["p3"]
            p4 = self.calibration_points["p4"]
            self.limits = {
                "A": (min(p1["A"], p4["A"]), max(p1["A"], p4["A"])),
                "B": (min(p1["B"], p4["B"]), max(p1["B"], p4["B"])),
                "C": (min(p1["C"], p4["C"]), max(p1["C"], p4["C"])),
                "D": (
                    min(p1["D"], p2["D"], p3["D"], p4["D"]),
                    max(p1["D"], p2["D"], p3["D"], p4["D"]),
                ),
            }
            home = {"A": p4["A"], "B": p1["B"], "C": p4["C"], "D": p1["D"]}
            self.calibration_points["home"] = home
            self.points = {
                "A": {"closed": p1["A"], "open": p4["A"]},
                "B": {"min": p1["B"], "max": p4["B"]},
                "C": {"min": p1["C"], "max": p4["C"]},
                "D": {"home": p1["D"], "assembly": p2["D"], "quality": p3["D"]},
            }
            self.calibrated = True
            self.save_calibration()
        # Move to the derived home position outside the lock
        self.move("absolute", home, speed=40)
        return {"limits": self.limits.copy(), "home": home, "points": self.points.copy()}

    def calibration_status(self) -> dict:
        with self.lock:
            return {
                "points": self.calibration_points.copy(),
                "calibrated": self.calibrated,
                "named_points": {j: pts.copy() for j, pts in self.points.items()},
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
        return self.move("absolute", poses[name], speed=speed)

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
        result = None
        for p in steps:
            result = self.goto_pose(p, speed)
        grip = {"pick": -30, "place": 30}[action]
        result = self.move("relative", {"A": grip}, speed)
        return result

    def state(self) -> dict:
        return {
            "abs_degrees": self.current_abs.copy(),
            "limits": self.limits.copy(),
            "motors": list(self.motors.keys()),
            "backlash": self.backlash.copy(),
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
        print("evdev not available; gamepad disabled")
        return
    path = device_path
    if not path:
        devices = list_devices()
        if not devices:
            print("No gamepad device found")
            return
        path = devices[0]
    try:
        dev = InputDevice(path)
    except Exception as e:
        print(f"Failed to open gamepad {path}: {e}")
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
        try:
            arm.move("relative", {joint: deg}, speed)
        except Exception:
            continue


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
            if kind == "move":
                res = arm.move(
                    req.get("mode", "relative"),
                    req["joints"],
                    int(req.get("speed", 60)),
                    req.get("timeout_s"),
                    req.get("units", "degrees"),
                )
            elif kind == "pose":
                res = arm.goto_pose(req["name"], int(req.get("speed", 60)))
            elif kind == "pickplace":
                res = arm.pickplace(req["location"], req["action"], int(req.get("speed", 60)))
            else:
                raise ValueError(f"Unknown op type {kind}")
            op["result"] = res
            op["status"] = "succeeded"
        except Exception as e:
            op["error"] = {"code": "EXECUTION_ERROR", "message": str(e)}
            op["status"] = "failed"
        finally:
            op["finished_at"] = time.time()
            with ops_lock:
                ops[op["id"]] = op
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
                    "POST /v1/arm/move",
                    "POST /v1/arm/pose",
                    "POST /v1/arm/stop",
                    "POST /v1/arm/coast",
                    "POST /v1/arm/pickplace",
                    "POST /v1/arm/backlash",
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
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/v1/processes/") or path in ("/v1/arm/move", "/v1/arm/pose", "/v1/arm/pickplace", "/v1/arm/stop", "/v1/arm/coast", "/v1/arm/backlash", "/v1/arm/calibration"):
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
                proc = PROCESS_MAP.get(name)
                if not proc:
                    return json_response(self, {"ok": False, "error": {"code": "UNKNOWN_PROCESS", "message": "Unknown process"}}, 404)
                res = proc(arm)
                resp = {"ok": True, "data": res}
                idem_store(self, resp)
                return json_response(self, resp)

            if path == "/v1/arm/move":
                mode = body.get("mode", "relative")
                joints = body.get("joints") or {}
                speed = int(body.get("speed", 60))
                timeout_s = body.get("timeout_s", 30)
                units = body.get("units", "degrees")
                async_exec = bool(body.get("async_exec", False))
                if not isinstance(joints, dict) or not joints:
                    return json_response(self, {"ok": False, "error": {"code": "BAD_MOVE", "message": "Provide joints map"}}, 400)
                if async_exec:
                    op = {
                        "id": str(uuid.uuid4()),
                        "type": "move",
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
                res = arm.move(mode, joints, speed, timeout_s, units)
                resp = {"ok": True, "data": res}
                idem_store(self, resp)
                return json_response(self, resp)

            if path == "/v1/arm/pose":
                name = body.get("name")
                speed = int(body.get("speed", 60))
                async_exec = bool(body.get("async_exec", False))
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
                async_exec = bool(body.get("async_exec", False))
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

            if path == "/v1/arm/calibration":
                if body.get("point"):
                    res = arm.record_calibration_point(str(body.get("point")))
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
                return json_response(self, {"ok": False, "error": {"code": "BAD_CALIB", "message": "Provide point or finalize"}}, 400)

            return json_response(self, {"ok": False, "error": {"code": "NOT_FOUND", "message": "Unknown path"}}, 404)
        except TimeoutError as te:
            return json_response(self, {"ok": False, "error": {"code": "TIMEOUT", "message": str(te)}}, 408)
        except Exception as e:
            return json_response(self, {"ok": False, "error": {"code": "SERVER_ERROR", "message": str(e)}}, 500)

# ---------------------------
# Entrypoint
# ---------------------------

def run_server():
    port = int(os.getenv("PORT", "8000"))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"LEGO Arm REST listening on http://0.0.0.0:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == "__main__":
    run_server()
