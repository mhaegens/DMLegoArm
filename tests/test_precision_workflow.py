import unittest
from unittest import mock

from processes import _precision_workflow as pw


class DummyArm:
    def __init__(self):
        self.positions = {"D": 0.0}
        self.moves = []

    def move(self, mode, values, speed, units, finalize):
        # Simulate a stubborn joint that reports no position change.
        self.moves.append((mode, dict(values), speed))
        return {"new_abs": dict(self.positions)}

    def read_position(self):
        return dict(self.positions)


class PrecisionWorkflowTests(unittest.TestCase):
    @mock.patch.object(pw, "_verify_stable", return_value=(False, {"D": 5.0}))
    def test_move_joint_does_not_fail_on_d_error(self, _mock_verify):
        arm = DummyArm()

        try:
            result = pw._move_joint(arm, "D", 10.0)
        except RuntimeError as exc:  # pragma: no cover - defensive
            self.fail(f"_move_joint raised unexpectedly: {exc}")

        self.assertIsInstance(result, dict)
        # Ensure we attempted the move and retries even though verification failed.
        self.assertGreaterEqual(len(arm.moves), 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
