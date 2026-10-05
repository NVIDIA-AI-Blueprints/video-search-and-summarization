# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import contextlib
import importlib.util
import io
import subprocess
import unittest
from pathlib import Path
from unittest import mock

WATCHDOG_PATH = Path(__file__).parents[1] / "nemoclaw" / "dashboard-forward-watchdog.py"
MODULE_SPEC = importlib.util.spec_from_file_location("dashboard_forward_watchdog_under_test", WATCHDOG_PATH)
if MODULE_SPEC is None or MODULE_SPEC.loader is None:
    raise RuntimeError(f"Could not load {WATCHDOG_PATH}")
watchdog = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(watchdog)

SANDBOX = "vss-harness-sandbox"
PORT = 18789
FORWARD = (
    "/home/u/.local/bin/openshell --gateway nemoclaw --gateway-endpoint https://127.0.0.1:8080 "
    f"--workspace default forward service {SANDBOX} --target-port {PORT} --target-host 127.0.0.1 "
    f"--local 127.0.0.1:{PORT}"
)
SQUATTER = f"python3 -m http.server {PORT} --bind 127.0.0.1"
TOKEN = "s3cr3t-gateway-token"


class FakeHost:
    """The host as the watchdog sees it through subprocess: lsof, ps, curl, kill, recover."""

    def __init__(self, *, listeners=None, status="200", recover_heals=True, recover_output="",
                 curl_rc=None, recover_hangs=False):
        self.listeners = dict(listeners if listeners is not None else {"4141": FORWARD})
        self.status = status
        self.recover_heals = recover_heals
        self.recover_output = recover_output
        self.curl_rc = curl_rc
        self.recover_hangs = recover_hangs
        self.calls: list[tuple[str, ...]] = []

    def run(self, command, **_kwargs):
        self.calls.append(tuple(command))
        done = lambda rc=0, out="", err="": subprocess.CompletedProcess(command, rc, out, err)  # noqa: E731
        if command[:2] == ["lsof", "-t"]:
            return done(out="".join(f"{pid}\n" for pid in self.listeners))
        if command[:2] == ["ps", "-ww"]:
            return done(out=self.listeners.get(command[3], "") + "\n")
        if command[0] == "curl":
            rc = self.curl_rc if self.curl_rc is not None else (0 if self.status != "000" else 7)
            return done(rc, out=self.status)
        if command[0] == "kill":
            self.listeners.pop(command[-1], None)
            return done()
        if command[:3] == ["nemoclaw", SANDBOX, "recover"]:
            if self.recover_hangs:
                raise subprocess.TimeoutExpired(command, _kwargs.get("timeout"))
            if self.recover_heals and not self.listeners:
                self.listeners = {"4242": FORWARD}
                self.status = "200"
            return done(0 if self.recover_heals else 1, err=self.recover_output)
        raise AssertionError(f"unexpected command: {command}")


@contextlib.contextmanager
def host(fake: FakeHost):
    with mock.patch("subprocess.run", side_effect=fake.run), mock.patch("time.sleep"):
        yield fake


def kills(fake: FakeHost) -> list[tuple[str, ...]]:
    return [c for c in fake.calls if c[0] == "kill"]


class OwnershipTests(unittest.TestCase):
    def test_this_sandboxs_forward_is_recognised(self) -> None:
        self.assertTrue(watchdog.is_sandbox_forward(FORWARD, SANDBOX, PORT))

    def test_another_sandbox_port_or_binary_is_not(self) -> None:
        self.assertFalse(watchdog.is_sandbox_forward(FORWARD, "other", PORT))
        self.assertFalse(watchdog.is_sandbox_forward(FORWARD, SANDBOX, 19999))
        self.assertFalse(watchdog.is_sandbox_forward(SQUATTER, SANDBOX, PORT))
        self.assertFalse(watchdog.is_sandbox_forward(FORWARD.replace("openshell", "evilshell", 1), SANDBOX, PORT))

    def test_a_port_shared_with_any_other_listener_is_not_owned(self) -> None:
        with host(FakeHost(listeners={"4141": FORWARD, "5151": SQUATTER})):
            self.assertFalse(watchdog.forward_owned(SANDBOX, PORT))

    def test_an_empty_port_is_not_owned(self) -> None:
        with host(FakeHost(listeners={})):
            self.assertFalse(watchdog.forward_owned(SANDBOX, PORT))


class ProbeTests(unittest.TestCase):
    def test_200_401_and_403_prove_the_transport(self) -> None:
        for status in ("200", "401", "403"):
            with self.subTest(status=status), host(FakeHost(status=status)):
                self.assertTrue(watchdog.forward_answers(PORT))

    def test_errors_and_no_response_do_not(self) -> None:
        for status in ("500", "502", "000", ""):
            with self.subTest(status=status), host(FakeHost(status=status)):
                self.assertFalse(watchdog.forward_answers(PORT))

    def test_a_status_line_followed_by_a_stall_is_not_answering(self) -> None:
        # curl prints the status it received, then exits 28 when the body stalls.
        with host(FakeHost(status="200", curl_rc=28)):
            self.assertFalse(watchdog.forward_answers(PORT))

    def test_the_probe_bypasses_the_proxy_and_is_bounded(self) -> None:
        with host(FakeHost()) as fake:
            watchdog.forward_answers(PORT, timeout=3)
        probe = next(c for c in fake.calls if c[0] == "curl")
        self.assertIn("--noproxy", probe)
        self.assertEqual(probe[probe.index("--max-time") + 1], "3")
        self.assertEqual(probe[-1], f"http://127.0.0.1:{PORT}/health")


class RepairTests(unittest.TestCase):
    def test_a_wedged_verified_forward_is_stopped_then_recovered(self) -> None:
        with host(FakeHost(status="000")) as fake:
            result = watchdog.repair(SANDBOX, PORT)
        self.assertTrue(result.answering)
        self.assertEqual(result.stopped, ["4141"])
        self.assertLess(fake.calls.index(("kill", "4141")), fake.calls.index(("nemoclaw", SANDBOX, "recover")))

    def test_an_unrelated_listener_is_never_signalled(self) -> None:
        with host(FakeHost(listeners={"5151": SQUATTER}, status="000", recover_heals=False)) as fake:
            result = watchdog.repair(SANDBOX, PORT)
        self.assertFalse(result.answering)
        self.assertEqual(result.stopped, [])
        self.assertEqual(kills(fake), [])
        self.assertIn(("nemoclaw", SANDBOX, "recover"), fake.calls)

    def test_a_forward_ignoring_sigterm_is_finished_with_sigkill(self) -> None:
        fake = FakeHost(status="000")
        run = fake.run

        def stubborn(command, **kwargs):
            if command == ["kill", "4141"]:
                fake.calls.append(tuple(command))
                return subprocess.CompletedProcess(command, 0, "", "")
            return run(command, **kwargs)

        with mock.patch("subprocess.run", side_effect=stubborn), mock.patch("time.sleep"):
            watchdog.stop_verified_forward(SANDBOX, PORT)
        self.assertIn(("kill", "-9", "4141"), fake.calls)

    def test_a_stalled_recover_is_bounded_and_counts_as_failed(self) -> None:
        with host(FakeHost(listeners={}, status="000", recover_hangs=True)) as fake:
            result = watchdog.repair(SANDBOX, PORT)
        self.assertFalse(result.answering)
        self.assertEqual(result.recover.returncode, 124)
        self.assertIn("timed out", result.recover.stderr)
        self.assertIn(("nemoclaw", SANDBOX, "recover"), fake.calls)

    def test_a_missing_forward_goes_straight_to_recover(self) -> None:
        with host(FakeHost(listeners={}, status="000")) as fake:
            result = watchdog.repair(SANDBOX, PORT)
        self.assertTrue(result.answering)
        self.assertEqual(kills(fake), [])


class LoopTests(unittest.TestCase):
    def _run(self, fake, cycles, **overrides):
        argv = ["--sandbox", SANDBOX, "--port", str(PORT)]
        for key, value in overrides.items():
            argv += [f"--{key.replace('_', '-')}", str(value)]
        clock = iter(range(0, 100000, 15))
        stderr = io.StringIO()
        with host(fake), contextlib.redirect_stderr(stderr):
            watchdog.run(watchdog.parse_args(argv), clock=lambda: next(clock), sleep=lambda _s: None, cycles=cycles)
        return stderr.getvalue()

    @staticmethod
    def recovers(fake):
        return fake.calls.count(("nemoclaw", SANDBOX, "recover"))

    def test_a_healthy_forward_is_never_touched(self) -> None:
        fake = FakeHost()
        self._run(fake, cycles=10)
        self.assertEqual(self.recovers(fake), 0)
        self.assertEqual(kills(fake), [])

    def test_failures_below_the_threshold_do_not_repair(self) -> None:
        fake = FakeHost(status="000")
        self._run(fake, cycles=2, threshold=3)
        self.assertEqual(self.recovers(fake), 0)

    def test_the_threshold_triggers_one_repair(self) -> None:
        fake = FakeHost(status="000")
        log = self._run(fake, cycles=5, threshold=3)
        self.assertEqual(self.recovers(fake), 1)
        self.assertIn("repaired: forward answering", log)

    def test_failed_repairs_are_rate_limited(self) -> None:
        # The fake clock advances 15 s per call; a 120 s gap allows one repair per
        # ~4 probes even though every probe fails.
        fake = FakeHost(listeners={"5151": SQUATTER}, status="000", recover_heals=False)
        self._run(fake, cycles=12, threshold=1, min_repair_gap=120)
        self.assertLessEqual(self.recovers(fake), 4)
        self.assertGreaterEqual(self.recovers(fake), 2)

    def test_repeated_failed_repairs_back_off_and_name_the_diagnostic(self) -> None:
        fake = FakeHost(listeners={"5151": SQUATTER}, status="000", recover_heals=False)
        log = self._run(fake, cycles=60, threshold=1, min_repair_gap=0, max_failed_repairs=2, backoff=100000)
        self.assertEqual(self.recovers(fake), 2)
        self.assertIn(f"`nemoclaw {SANDBOX} status`", log)

    def test_the_log_never_carries_the_gateway_token(self) -> None:
        fake = FakeHost(
            status="000",
            recover_output=f"Dashboard: http://127.0.0.1:{PORT}/#token={TOKEN}\nopen ?token={TOKEN}&x=1",
        )
        log = self._run(fake, cycles=4, threshold=1)
        self.assertNotIn(TOKEN, log)
        self.assertIn("token=<redacted>", log)


class ParseArgsTests(unittest.TestCase):
    def test_defaults_match_the_documented_contract(self) -> None:
        args = watchdog.parse_args(["--sandbox", SANDBOX])
        self.assertEqual((args.port, args.interval, args.threshold), (18789, 15.0, 3))

    def test_a_zero_threshold_is_rejected(self) -> None:
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            watchdog.parse_args(["--sandbox", SANDBOX, "--threshold", "0"])


if __name__ == "__main__":
    unittest.main()
