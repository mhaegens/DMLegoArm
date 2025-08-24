from __future__ import annotations

import os
import time
import uuid
import json
import threading
import queue
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse
from typing import Dict, Optional, Literal

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
        def run_for_degrees(self, degrees: float, speed: int = 50, blocking: bool = True):
            self._pos += degrees
            if blocking:
                time.sleep(min(abs(degrees) / 360.0, 0.2))
        def stop(self):
            pass
        def get_degrees(self):
            return self._pos

# ---------------------------
# Controller
# ---------------------------

class ArmController:
    def __init__(self):
        self.motors: Dict[str, Motor] = {
            "A": Motor("A"),  # base
            "B": Motor("B"),  # shoulder
            "C": Motor("C"),  # elbow
            "D": Motor("D"),  # gripper
        }
        self.current_abs: Dict[str, float] = {k: 0.0 for k in self.motors}
        self.limits = {
            "A": (-360.0, 360.0),
            "B": (-180.0, 180.0),
            "C": (-180.0, 180.0),
            "D": (-90.0, 90.0),
        }
        self.lock = threading.Lock()

    def clamp(self, joint: str, value: float) -> float:
        lo, hi = self.limits[joint]
        return max(lo, min(hi, value))

    def stop_all(self):
        for m in self.motors.values():
            m.stop()

    def move(self, mode: Literal["relative", "absolute"], joints: Dict[str, float], speed: int, timeout_s: Optional[float] = None):
        start = time.time()
        with self.lock:
            if mode == "relative":
                plan = {j: self.clamp(j, self.current_abs[j] + deg) - self.current_abs[j] for j, deg in joints.items()}
            else:
                plan = {j: self.clamp(j, deg) - self.current_abs[j] for j, deg in joints.items()}
            for j, delta in plan.items():
                if abs(delta) < 1e-6:
                    continue
                self.motors[j].run_for_degrees(delta, speed=speed, blocking=True)
                self.current_abs[j] += delta
                if timeout_s and time.time() - start > timeout_s:
                    raise TimeoutError("Movement timed out")
        return {"new_abs": self.current_abs.copy()}

    def goto_pose(self, name: str, speed: int):
        poses = {
            "home": {"A": 0, "B": 0, "C": 0, "D": 0},
            "pick_left": {"A": -60, "B": -20, "C": 30, "D": 20},
            "pick_right": {"A": 60, "B": -20, "C": 30, "D": 20},
            "place_left": {"A": -60, "B": 10, "C": -10, "D": -5},
            "place_right": {"A": 60, "B": 10, "C": -10, "D": -5},
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
        result = self.move("relative", {"D": grip}, speed)
        return result

    def state(self) -> dict:
        return {
            "abs_degrees": self.current_abs.copy(),
            "limits": self.limits.copy(),
            "motors": list(self.motors.keys()),
        }

arm = ArmController()
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
                res = arm.move(req.get("mode", "relative"), req["joints"], int(req.get("speed", 60)), req.get("timeout_s"))
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
    handler.wfile.write(body)


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
        if path == "/v1/health":
            return json_response(self, {"ok": True, "data": {"status": "ok", "time": time.time()}})
        if path == "/v1/inventory":
            if (resp := auth_ok(self)):
                return json_response(self, resp[0], resp[1])
            data = {
                "endpoints": [
                    "GET /v1/health",
                    "GET /v1/inventory",
                    "GET /v1/arm/state",
                    "POST /v1/arm/move",
                    "POST /v1/arm/pose",
                    "POST /v1/arm/stop",
                    "POST /v1/arm/pickplace",
                    "GET /v1/operations/{id}",
                ],
                "poses": ["home", "pick_left", "pick_right", "place_left", "place_right"],
                "motors": list(arm.motors.keys()),
            }
            return json_response(self, {"ok": True, "data": data})
        if path == "/v1/arm/state":
            if (resp := auth_ok(self)):
                return json_response(self, resp[0], resp[1])
            return json_response(self, {"ok": True, "data": arm.state()})
        if path.startswith("/v1/operations/"):
            if (resp := auth_ok(self)):
                return json_response(self, resp[0], resp[1])
            op_id = path.split("/v1/operations/")[-1]
            with ops_lock:
                op = ops.get(op_id)
            if not op:
                return json_response(self, {"ok": False, "error": {"code": "NOT_FOUND", "message": "Unknown operation id"}}, 404)
            return json_response(self, {"ok": True, "data": op})
        return json_response(self, {"ok": False, "error": {"code": "NOT_FOUND", "message": "Unknown path"}}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/v1/arm/move", "/v1/arm/pose", "/v1/arm/pickplace", "/v1/arm/stop"):
            if (resp := auth_ok(self)):
                return json_response(self, resp[0], resp[1])
        if path == "/v1/arm/stop":
            arm.stop_all()
            body = parse_json(self)
            return json_response(self, {"ok": True, "data": {"stopped": True, "reason": body.get("reason")}})

        cached = idem_get(self)
        if cached:
            return json_response(self, cached)

        try:
            body = parse_json(self)
            if path == "/v1/arm/move":
                mode = body.get("mode", "relative")
                joints = body.get("joints") or {}
                speed = int(body.get("speed", 60))
                timeout_s = body.get("timeout_s", 30)
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
                res = arm.move(mode, joints, speed, timeout_s)
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
