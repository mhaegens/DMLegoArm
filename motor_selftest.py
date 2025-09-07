from __future__ import annotations

import os
import time
from typing import Dict

# ---------------------------
# Hardware abstraction layer
# ---------------------------

USE_FAKE = os.getenv("USE_FAKE_MOTORS", "0") == "1"

try:
    if not USE_FAKE:
        from buildhat import Motor  # type: ignore
    else:
        raise ImportError("Using fake motors by env override")
except Exception:
    class Motor:  # fake motor for dev/testing
        def __init__(self, port: str):
            self.port = port
            self._pos = 0.0
        def run_for_degrees(self, degrees: float, speed: int = 50, blocking: bool = True):
            self._pos += degrees
            if blocking:
                time.sleep(min(abs(degrees) / 360.0, 0.2))
        def run_for_rotations(self, rotations: float, speed: int = 50, blocking: bool = True):
            self.run_for_degrees(rotations * 360.0, speed, blocking)
        def stop(self):
            pass
        def get_position(self):
            return self._pos

PORTS = ["A", "B", "C", "D"]


def test_motor(port: str) -> Dict[str, float]:
    m = Motor(port)
    start = m.get_position()
    m.run_for_degrees(90, speed=50)
    mid = m.get_position()
    m.run_for_degrees(-90, speed=50)
    end = m.get_position()
    ok = abs(end - start) < 1e-6
    return {"start": start, "mid": mid, "end": end, "ok": ok}


def main():
    print("Running quick motor self-test")
    all_ok = True
    for p in PORTS:
        try:
            res = test_motor(p)
            status = "OK" if res["ok"] else "OFFSET"
            print(f"Port {p}: {status} start={res['start']:.1f} mid={res['mid']:.1f} end={res['end']:.1f}")
            all_ok &= res["ok"]
        except Exception as e:
            all_ok = False
            print(f"Port {p}: ERROR {e}")
    if all_ok:
        print("All motors responded as expected")
    else:
        print("One or more motors failed the test")


if __name__ == "__main__":
    main()
