import os
import unittest

os.environ.setdefault("USE_FAKE_MOTORS", "1")

from lego_arm_master import ArmController  # noqa: E402


class DriftMotor:
    """Motor stub that undershoots the first command to trigger finalize."""

    def __init__(self, port: str):
        self.port = port
        self._pos = 0.0
        self._first = True

    def run_for_degrees(self, degrees: float, speed: int = 50, blocking: bool = True):
        if self._first:
            self._pos += degrees * 0.9
            self._first = False
        else:
            self._pos += degrees

    def get_degrees(self) -> float:
        return self._pos

    def stop(self):
        pass

    def float(self):
        pass

    def set_default_stop_action(self, action: str):
        pass


class MotionUnitTests(unittest.TestCase):
    def setUp(self):
        self.arm = ArmController()
        # Reset fake motor positions between tests for determinism.
        for name, motor in self.arm.motors.items():
            if hasattr(motor, "_pos"):
                motor._pos = 0.0  # type: ignore[attr-defined]
            self.arm.current_abs[name] = 0.0
        self.arm._last_dir = {j: 0 for j in self.arm.motors}  # type: ignore[attr-defined]
        self.arm.backlash = {j: 0.0 for j in self.arm.motors}

    def _assert_rotation(self, rotations: float, tol: float = 0.5):
        start = self.arm.current_abs["A"]
        res = self.arm.move(
            "relative",
            {"A": rotations},
            speed=60,
            units="rotations",
            finalize=False,
        )
        expected_deg = rotations * self.arm.rotation_deg["A"]
        actual_delta = self.arm.current_abs["A"] - start
        self.assertAlmostEqual(actual_delta, expected_deg, delta=tol)
        summary = self.arm.last_move_summary()
        self.assertAlmostEqual(summary["converted_degrees"]["A"], expected_deg, delta=tol)
        self.assertFalse(summary["timeout"])
        return res

    def test_rotations_match_expected_degrees(self):
        for rotations in (1, 3, 10, 100):
            with self.subTest(rotations=rotations):
                self._assert_rotation(rotations, tol=0.1 if rotations <= 10 else 0.5)

    def test_degrees_move_matches_single_rotation(self):
        start = self.arm.current_abs["A"]
        self.arm.move(
            "relative",
            {"A": 360},
            speed=60,
            units="degrees",
            finalize=False,
        )
        self.assertAlmostEqual(self.arm.current_abs["A"] - start, 360.0, delta=0.1)

    def test_literal_degrees_do_not_apply_rotation_compensation(self):
        start = self.arm.current_abs["A"]
        self.arm.move(
            "relative",
            {"A": 3010},
            speed=60,
            units="degrees",
            finalize=False,
        )
        self.assertAlmostEqual(self.arm.current_abs["A"] - start, 3010.0, delta=0.1)

    def test_finalize_corrects_encoder_error(self):
        drift = DriftMotor("A")
        self.arm.motors["A"] = drift
        self.arm.current_abs["A"] = 0.0
        result = self.arm.move(
            "relative",
            {"A": 90},
            speed=40,
            units="degrees",
            finalize=True,
            finalize_deadband_deg=1.0,
        )
        summary = self.arm.last_move_summary()
        self.assertTrue(summary["finalized"])
        self.assertLess(abs(summary["final_error_deg"]["A"]), 1.0)
        self.assertAlmostEqual(self.arm.current_abs["A"], 90.0, delta=1.0)
        self.assertAlmostEqual(result["finalize_corrections"]["A"], summary["finalize_corrections"]["A"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
