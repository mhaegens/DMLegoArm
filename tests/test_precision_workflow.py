import unittest
from unittest import mock

from processes import _precision_workflow as pw


class DummyArm:
    def __init__(self):
        self.positions = {"C": 0.0, "D": 0.0}
        self.moves = []
        self.telemetry_ok_flag = True

    def move(self, mode, values, speed, units, finalize):
        # Simulate a stubborn joint that reports no position change.
        self.moves.append((mode, dict(values), speed))
        return {"new_abs": dict(self.positions)}

    def read_position(self):
        return dict(self.positions)

    def telemetry_healthy(self):
        return self.telemetry_ok_flag


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

    @mock.patch.object(pw, "_verify_stable", return_value=(False, {"C": 9.0}))
    def test_move_joint_allows_c_false_alarm(self, _mock_verify):
        arm = DummyArm()

        try:
            result = pw._move_joint(arm, "C", 5.0)
        except RuntimeError as exc:  # pragma: no cover - defensive
            self.fail(f"_move_joint raised unexpectedly: {exc}")

        self.assertIsInstance(result, dict)
        self.assertGreaterEqual(len(arm.moves), 2)

    @mock.patch.object(pw, "_verify_stable", return_value=(False, {"D": 2.5}))
    def test_small_error_accepts_without_retry(self, _mock_verify):
        arm = DummyArm()

        pw._move_joint(arm, "D", 5.0)

        self.assertEqual(len(arm.moves), 1)

    def test_medium_error_retries_once_then_accepts(self):
        verify_results = [
            (False, {"D": 8.0}),
            (False, {"D": 6.0}),
        ]

        def fake_verify(*_args, **_kwargs):
            return verify_results.pop(0)

        with mock.patch.object(pw, "_verify_stable", side_effect=fake_verify):
            arm = DummyArm()
            pw._move_joint(arm, "D", 5.0)

        self.assertEqual(len(arm.moves), 2)
        self.assertEqual(arm.moves[1][2], pw.SPEED_DEFAULT_FINAL)

    def test_large_error_limits_recovery_attempts(self):
        verify_results = [
            (False, {"D": 25.0}),
            (False, {"D": 22.0}),
            (False, {"D": 18.0}),
        ]

        def fake_verify(*_args, **_kwargs):
            return verify_results.pop(0)

        arm = DummyArm()
        with mock.patch.object(pw, "_verify_stable", side_effect=fake_verify):
            with self.assertRaises(RuntimeError):
                pw._move_joint(arm, "D", 5.0)

        self.assertEqual(len(arm.moves), 3)

    def test_telemetry_unhealthy_allows_one_extra_try(self):
        verify_results = [
            (False, {"D": float("nan")}),
            (True, {"D": 0.0}),
        ]

        def fake_verify(*_args, **_kwargs):
            return verify_results.pop(0)

        arm = DummyArm()
        arm.telemetry_ok_flag = False

        with mock.patch.object(pw, "_verify_stable", side_effect=fake_verify):
            pw._move_joint(arm, "D", 5.0)

        self.assertEqual(len(arm.moves), 2)

    @mock.patch.object(pw, "_verify_stable", return_value=(True, {}))
    def test_run_workflow_skips_unchanged_joints_except_edges(self, _mock_verify):
        arm = mock.Mock()
        arm.resolve_pose.side_effect = lambda pose: pose

        steps = [
            ("First", {"A": 1.0, "B": 2.0}),
            ("Second", {"A": 1.0, "B": 3.0}),
            ("Third", {"A": 1.0, "B": 3.0}),
            ("Final", {"A": 1.0, "B": 3.0}),
        ]

        with mock.patch.object(pw, "_move_joint", return_value={}) as mock_move:
            pw.run_workflow(arm, steps, joint_order=("B", "A"))

        self.assertEqual(
            mock_move.call_args_list,
            [
                mock.call(arm, "B", 2.0),
                mock.call(arm, "B", 2.0),
                mock.call(arm, "A", 1.0),
                mock.call(arm, "A", 1.0),
                mock.call(arm, "B", 3.0),
                mock.call(arm, "B", 3.0),
                mock.call(arm, "B", 3.0),
                mock.call(arm, "B", 3.0),
                mock.call(arm, "A", 1.0),
                mock.call(arm, "A", 1.0),
            ],
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
