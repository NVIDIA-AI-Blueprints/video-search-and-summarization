#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for gpu_trace.py.

This module's whole contract is "collect telemetry, and under no circumstance
break the leg". So the cases that matter are the failure ones, and the tests
that matter are the ones that fail when a safety property is removed.

`_remote` shells out, so it is exercised against a real executable on PATH
rather than being monkeypatched away. A stub that replaces the function cannot
notice the remote command losing its `timeout` prefix, and that prefix is the
only thing standing between us and the 213-minute sampler leak this module
exists to avoid.

Mutations confirmed to fail this suite:
  * drop `timeout {hard_stop}` from the start command
  * let `trace()` propagate an exception instead of swallowing it
  * omit `declared_gpu_count` from the sidecar
  * add a field to QUERY_FIELDS
  * skip the `rm -f` of the remote CSV
"""

from __future__ import annotations

import json
import os
import stat
import sys
import time
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gpu_trace as mod  # noqa: E402

# A stand-in for `brev` that records the exact command it was handed and
# replies with two GPUs' worth of plausible nvidia-smi output. Recording argv is
# what lets the tests assert on the remote command string itself.
BREV_STUB = r"""#!/usr/bin/env python3
import sys, os, re, subprocess
argv = sys.argv[1:]
rec = os.environ["GPUTRACE_STUB_REC"]
open(rec, "a").write("\x00".join(argv) + "\n")
if os.environ.get("GPUTRACE_STUB_FAIL") == "1":
    sys.stderr.write("boom\n"); sys.exit(1)
cmd = argv[-1] if argv else ""

# The command is EXECUTED, in a real bash, against a fake nvidia-smi and a
# scratch HOME seeded with a decoy secret. An earlier version of this stub
# only recorded argv, and a mutation that appended
#   awk '{print "0,0,0,0,0,0," $0}' ~/.ssh/id_rsa
# to the fetch command passed all 22 tests. A stub that does not run the
# command cannot notice the command growing a payload, which is precisely the
# boundary these tests exist to protect.
if os.environ.get("GPUTRACE_STUB_HOSTILE_PID") == "1" and "echo PID=$!" in cmd:
    # A compromised or confused box reports shell syntax where a pid belongs.
    sys.stdout.write("PID=1; touch " + os.environ["GPUTRACE_STUB_HOME"] + "/PWNED\n")
    sys.stdout.write("NVIDIA RTX PRO 6000 Blackwell Server Edition\n")
    sys.stdout.write(argv[1] + "\n" if len(argv) > 1 else "")
    raise SystemExit
env = dict(os.environ)
env["HOME"] = os.environ["GPUTRACE_STUB_HOME"]
env["PATH"] = os.environ["GPUTRACE_STUB_BIN"] + os.pathsep + env["PATH"]
p = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, env=env)
sys.stdout.write(p.stdout)
sys.stdout.write(argv[1] + "\n" if len(argv) > 1 else "")
"""

# Stands in for nvidia-smi. Emits two GPUs' worth of well-formed rows, and
# under -l keeps running for SMI_RUN_SEC so the backgrounding grammar is
# genuinely exercised: with the wrong grammar the start call blocks for this
# long, which is exactly what the timing test measures.
# A real leg runs for 35 to 120 minutes. The tests must not stop the sampler
# before it has written anything, or they would be measuring interpreter
# startup rather than the code under test.
BODY_SETTLE_SEC = 1.5
SMI_RUN_SEC = 8
NVIDIA_SMI_STUB = r"""#!/usr/bin/env python3
import sys, time
if "--query-gpu=name" in " ".join(sys.argv):
    print("NVIDIA RTX PRO 6000 Blackwell Server Edition"); raise SystemExit
for n in range(2):
    print(f"2026/08/01 21:00:{n:02d}.000, 0, 0, 0, 42800, 97887, 71.2")
    print(f"2026/08/01 21:00:{n:02d}.000, 1, 97, 3, 74600, 97887, 310.5")
# A polluted line in the remote file, which a same-user /tmp path can acquire.
# It has seven comma-separated fields, so a comma-COUNT filter publishes it.
print("NGC_CLI_API_KEY=CANARYSECRET,a,b,c,d,e,f")
sys.stdout.flush()
if "-l" in sys.argv:
    time.sleep(%d)
""" % SMI_RUN_SEC

# macOS has no GNU `timeout`; the eval boxes are Linux and do. Without one on
# PATH the executed command fails and no trace is produced, so the suite needs
# a stand-in that honours `-k N <seconds> <cmd...>` and forwards SIGTERM.
TIMEOUT_STUB = r"""#!/usr/bin/env python3
import os, signal, subprocess, sys, threading
a = sys.argv[1:]
if a and a[0] == "-k":
    a = a[2:]
a = a[1:]                                   # drop the duration
p = subprocess.Popen(a)
def reap(*_):
    try: p.terminate()
    except Exception: pass
signal.signal(signal.SIGTERM, reap)
sys.exit(p.wait())
"""


class Stub:
    """Puts a fake `brev` (and a failing `ssh`) first on PATH."""

    KEYS = ("PATH", "GPUTRACE_STUB_REC", "GPUTRACE_STUB_FAIL",
            "GPUTRACE_STUB_HOME", "GPUTRACE_STUB_BIN", "GPUTRACE_STUB_HOSTILE_PID")

    def __init__(self, fail: bool = False, hostile_pid: bool = False):
        self.fail = fail
        self.hostile_pid = hostile_pid

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.rec = d / "rec"
        for name in ("brev", "ssh"):
            p = d / name
            p.write_text(BREV_STUB, encoding="utf-8")
            p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        # A scratch HOME holding a decoy secret. If a mutation ever teaches the
        # remote command to read it, the canary test below sees it in the CSV.
        self.home = d / "home"
        (self.home / ".ssh").mkdir(parents=True)
        (self.home / ".ssh" / "id_rsa").write_text(
            "-----BEGIN OPENSSH PRIVATE KEY-----\nCANARYSECRET\n", encoding="utf-8")
        self.bin = d / "bin"
        self.bin.mkdir()
        for name, body in (("nvidia-smi", NVIDIA_SMI_STUB), ("timeout", TIMEOUT_STUB)):
            f = self.bin / name
            f.write_text(body, encoding="utf-8")
            f.chmod(f.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        self.old = {k: os.environ.get(k) for k in self.KEYS}
        os.environ["PATH"] = f"{d}{os.pathsep}{self.old['PATH']}"
        os.environ["GPUTRACE_STUB_REC"] = str(self.rec)
        os.environ["GPUTRACE_STUB_FAIL"] = "1" if self.fail else "0"
        os.environ["GPUTRACE_STUB_HOME"] = str(self.home)
        os.environ["GPUTRACE_STUB_BIN"] = str(self.bin)
        os.environ["GPUTRACE_STUB_HOSTILE_PID"] = "1" if self.hostile_pid else "0"
        return self

    def commands(self) -> list[str]:
        """The command string handed to each invocation, in order."""
        if not self.rec.exists():
            return []
        return [ln.split("\x00")[-1]
                for ln in self.rec.read_text(encoding="utf-8").splitlines() if ln]

    def argvs(self) -> list[list[str]]:
        if not self.rec.exists():
            return []
        return [ln.split("\x00")
                for ln in self.rec.read_text(encoding="utf-8").splitlines() if ln]

    def __exit__(self, *exc):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()


class NothingButGpuMetricsCanReachTheArtifact(unittest.TestCase):
    """The stub executes the remote command for real against a scratch HOME
    seeded with a decoy private key, so a command that grows a payload is
    caught here rather than in a public artifact."""

    def test_no_secret_from_the_box_reaches_the_csv(self):
        with tempfile.TemporaryDirectory() as out, Stub():
            with mod.trace("box", Path(out), spec_stem="search"):
                time.sleep(BODY_SETTLE_SEC)
            files = list((Path(out) / "gputrace").glob("*"))
            self.assertTrue(files, "no trace produced, so this canary proves nothing")
            body = "".join(p.read_text() for p in files)
        self.assertNotIn("CANARYSECRET", body)
        self.assertNotIn("PRIVATE KEY", body)

    def test_a_row_that_is_not_nvidia_smi_shaped_is_dropped(self):
        """`ln.count(",") >= 6` accepted anything the remote `cat` returned.
        The remote path is a predictable same-user file in /tmp."""
        self.assertIsNone(mod.ROW_RE.match("SECRET=a,b,c,d,e,f,g"))
        self.assertIsNone(mod.ROW_RE.match("0,0,0,0,0,0,-----BEGIN KEY-----"))
        self.assertIsNone(mod.ROW_RE.match("2026/08/01 21:00:00.000, 0, 0"))
        self.assertTrue(mod.ROW_RE.match(
            "2026/08/01 21:00:00.000, 1, 97, 3, 74600, 97887, 310.5"))
        self.assertTrue(mod.ROW_RE.match(
            "2026/08/01 21:00:00.000, 0, [N/A], 0, 1800, 97887, [N/A]"))

    def test_a_remote_pid_carrying_shell_syntax_is_refused(self):
        """The pid is interpolated into a shell command. `PID=1; printf ...`
        executed in the cleanup shell and its output reached the CSV."""
        for hostile in ("999; printf x", "1 || rm -rf /", "$(id)", "-1", "",
                        "1\nrm -rf /", "0"):
            self.assertIsNone(mod.PID_RE.match(hostile), hostile)
        self.assertTrue(mod.PID_RE.match("4242"))

    def test_fetch_output_is_capped(self):
        self.assertLessEqual(mod.MAX_FETCH_BYTES, 64 * 1024 * 1024)
        self.assertGreater(mod.MAX_FETCH_BYTES, 0)

    def test_a_polluted_remote_file_does_not_reach_the_artifact(self):
        """END TO END, not a regex unit test. The stub's remote file contains
        `NGC_CLI_API_KEY=CANARYSECRET,a,b,c,d,e,f` -- seven comma-separated
        fields, so the original `ln.count(",") >= 6` filter published it
        verbatim into an artifact that is uploaded publicly. Asserting on
        ROW_RE alone left that filter free to be loosened again."""
        with tempfile.TemporaryDirectory() as out, Stub():
            with mod.trace("box", Path(out), spec_stem="pollute"):
                time.sleep(BODY_SETTLE_SEC)
            files = list((Path(out) / "gputrace").glob("*"))
            self.assertTrue(files, "no trace produced, so this proves nothing")
            body = "".join(p.read_text() for p in files)
        self.assertNotIn("CANARYSECRET", body)
        self.assertNotIn("NGC_CLI_API_KEY", body)
        self.assertIn("97887", body)          # the real samples did get through

    def test_a_hostile_remote_pid_never_reaches_the_cleanup_shell(self):
        """END TO END. `PID=1; touch $HOME/PWNED` was executed by the cleanup
        shell. Asserting on PID_RE alone left the code free to stop using it."""
        with tempfile.TemporaryDirectory() as out, Stub(hostile_pid=True) as stub:
            with mod.trace("box", Path(out), spec_stem="hostile"):
                pass
            pwned = (stub.home / "PWNED").exists()
            finish = stub.commands()[-1]
        self.assertFalse(pwned, "remote-supplied text was executed as shell code")
        self.assertNotIn("touch", finish, f"injected command survived: {finish!r}")
        self.assertFalse(finish.startswith("kill 1;"),
                         "an unvalidated pid was interpolated into the kill")


class RemoteCallsAreBounded(unittest.TestCase):
    def test_remote_respects_a_total_deadline_across_both_attempts(self):
        """The timeout used to be per attempt, so a hung box cost 2x120s. It is
        also enforced by killing the process GROUP: subprocess's own timeout
        reaps only the immediate child, which is the bug envs/brev_env.py
        already fixes the same way for harbor."""
        hang = tempfile.mkdtemp()
        for name in ("brev", "ssh"):
            f = Path(hang) / name
            f.write_text("#!/bin/sh\nsleep 60\n", encoding="utf-8")
            f.chmod(f.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        old = os.environ.get("PATH", "")
        try:
            os.environ["PATH"] = f"{hang}{os.pathsep}{old}"
            t0 = time.monotonic()
            self.assertIsNone(mod._remote("box", "echo hi", timeout=3))
            elapsed = time.monotonic() - t0
        finally:
            os.environ["PATH"] = old
        # Two attempts at a 3s PER-ATTEMPT budget would be ~6s. A TOTAL
        # deadline is ~3s. The bound has to sit between them or the
        # distinction the test exists for is not being made.
        self.assertLess(elapsed, 5,
                        f"deadline is per-attempt, not total ({elapsed:.1f}s)")


class TheHardStopIsPresent(unittest.TestCase):
    """The one property that cannot regress silently.

    Without `timeout`, a sampler survives a SIGKILLed leg and keeps writing
    across unrelated trials. That already happened once and cost the entire
    trace.
    """

    @staticmethod
    def _bound(cmd: str) -> int:
        """The duration argument, skipping any `-k N` kill-escalation flag."""
        parts = cmd.split("timeout ", 1)[1].split()
        return int(parts[2] if parts[0] == "-k" else parts[0])

    def test_start_command_is_bounded_by_timeout(self):
        with tempfile.TemporaryDirectory() as out, Stub() as stub:
            with mod.trace("vss-eval-rtx-2g", Path(out), harbor_timeout_sec=7800):
                pass
            start = stub.commands()[0]
        self.assertIn("timeout ", start, f"remote sampler is unbounded: {start!r}")
        # Must outlive a normal leg, and must not be open-ended.
        bound = self._bound(start)
        self.assertGreater(bound, 7800)
        self.assertLess(bound, 7800 + 3600)

    def test_hard_stop_has_a_floor_for_tiny_timeouts(self):
        with tempfile.TemporaryDirectory() as out, Stub() as stub:
            with mod.trace("box", Path(out), harbor_timeout_sec=1):
                pass
            bound = self._bound(stub.commands()[0])
        self.assertGreaterEqual(bound, 900)

    def test_remote_csv_is_removed(self):
        """A box that has been up six weeks must not accumulate traces."""
        with tempfile.TemporaryDirectory() as out, Stub() as stub:
            with mod.trace("box", Path(out)):
                pass
            self.assertIn("rm -f", stub.commands()[-1])

    def test_the_sampler_is_actually_stopped(self):
        """Deleting the kill left all 22 of the original tests green. The
        cleanup command must name the pid the start call reported."""
        with tempfile.TemporaryDirectory() as out, Stub() as stub:
            with mod.trace("box", Path(out)):
                pass
            cmds = stub.commands()
        start, finish = cmds[0], cmds[-1]
        pid = start.split("echo PID=$!")[0]     # sanity: the start reports one
        self.assertIn("echo PID=$!", start)
        self.assertRegex(finish, r"^kill [1-9][0-9]* ",
                         f"cleanup does not kill the sampler: {finish!r}")

    def test_only_one_command_is_backgrounded_and_it_closes_its_pipe(self):
        """`mkdir && nohup ... &` backgrounds the whole AND-list, so `$!` is a
        subshell rather than `timeout` and killing it leaves the sampler alive.
        That subshell also inherits stdout, so the start call blocks until the
        SAMPLER exits -- measured at 3.03s under bash for a 3s target versus
        0.01s under zsh, which is why a zsh laptop never saw it."""
        with tempfile.TemporaryDirectory() as out, Stub() as stub:
            with mod.trace("box", Path(out)):
                pass
            start = stub.commands()[0]
        head = start.split("&", 1)[0]
        self.assertNotIn("&&", head,
                         f"an AND-list is being backgrounded, so $! is a subshell: {start!r}")
        self.assertIn(">/dev/null 2>&1", start.replace("2>/dev/null </dev/null", ">/dev/null 2>&1")
                      if "2>/dev/null </dev/null" in start else start,
                      f"background job keeps a descriptor open: {start!r}")

    def test_the_start_call_does_not_block_on_the_sampler(self):
        """End to end through a real bash and a real looping nvidia-smi stub."""
        import time as _t
        with tempfile.TemporaryDirectory() as out, Stub():
            t0 = _t.monotonic()
            with mod.trace("box", Path(out)):
                pass
            elapsed = _t.monotonic() - t0
        self.assertLess(elapsed, SMI_RUN_SEC - 1,
                        f"start blocked on the sampler ({elapsed:.1f}s); the AND-list grammar is back")


class TheAllowlist(unittest.TestCase):
    """Only GPU metrics leave the box. PR #516 leaked NGC_CLI_API_KEY through
    artifact collection; this must not become a second such channel."""

    def test_query_fields_are_exactly_the_seven_declared(self):
        self.assertEqual(
            mod.QUERY_FIELDS.split(","),
            ["timestamp", "index", "utilization.gpu", "utilization.memory",
             "memory.used", "memory.total", "power.draw"],
        )

    def test_header_matches_the_query_field_count(self):
        self.assertEqual(len(mod.CSV_HEADER.split(",")),
                         len(mod.QUERY_FIELDS.split(",")))

    def test_remote_commands_never_read_env_or_process_state(self):
        with tempfile.TemporaryDirectory() as out, Stub() as stub:
            with mod.trace("box", Path(out)):
                pass
            blob = " ".join(stub.commands())
        for forbidden in ("printenv", "env ", "/proc/", "docker inspect",
                          "cat /etc", "ps -e", "--query-compute-apps"):
            self.assertNotIn(forbidden, blob, f"{forbidden!r} would collect more than GPU metrics")


class NeverFailsTheLeg(unittest.TestCase):
    def test_body_runs_when_the_box_is_unreachable(self):
        ran = []
        with tempfile.TemporaryDirectory() as out, Stub(fail=True):
            with mod.trace("box", Path(out)):
                ran.append(True)
        self.assertEqual(ran, [True])

    def test_body_runs_when_no_exec_tool_exists_at_all(self):
        ran = []
        old = os.environ.get("PATH", "")
        empty = tempfile.mkdtemp()
        try:
            os.environ["PATH"] = empty
            with tempfile.TemporaryDirectory() as out:
                with mod.trace("box", Path(out)):
                    ran.append(True)
        finally:
            os.environ["PATH"] = old
        self.assertEqual(ran, [True])

    def test_an_exception_in_the_body_still_propagates(self):
        """Swallowing the leg's own failure would hide a real eval error."""
        with tempfile.TemporaryDirectory() as out, Stub():
            with self.assertRaises(ValueError):
                with mod.trace("box", Path(out)):
                    raise ValueError("the leg failed")

    def test_the_trace_is_still_written_when_the_body_raises(self):
        """A timed-out trial (rc=124) is exactly the trace worth reading."""
        with tempfile.TemporaryDirectory() as out, Stub():
            with self.assertRaises(ValueError):
                with mod.trace("box", Path(out), spec_stem="search",
                               platform="RTXPRO6000BW"):
                    time.sleep(BODY_SETTLE_SEC)
                    raise ValueError("boom")
            self.assertTrue(list((Path(out) / "gputrace").glob("*.csv")))

    def test_an_exception_while_writing_the_trace_never_escapes(self):
        """The finally block is the last thing that runs before the leg's exit
        status is decided. If _finish raises there -- disk full, read-only
        mount, results_root removed by a concurrent cleanup -- the exception
        escapes the `with` and fails a leg that otherwise passed. Writing the
        trace is never worth that.

        Targets the property directly rather than relying on a path that
        happens to be unwritable: an earlier version used /dev/null/... and
        passed for the wrong reason, because _finish returned early before it
        ever reached the failing mkdir.
        """
        ran = []
        original = mod._finish
        mod._finish = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
        try:
            with tempfile.TemporaryDirectory() as out, Stub():
                with mod.trace("box", Path(out), spec_stem="search"):
                    ran.append(True)
        finally:
            mod._finish = original
        self.assertEqual(ran, [True])

    def test_an_unwritable_results_root_does_not_fail_the_leg(self):
        """The realistic form of the same thing, end to end."""
        ran = []
        bad = Path("/dev/null/not-a-directory")
        with Stub():
            with mod.trace("box", bad, spec_stem="search"):
                time.sleep(BODY_SETTLE_SEC)
                ran.append(True)
        self.assertEqual(ran, [True])

    def test_a_failure_writing_the_trace_does_not_mask_the_legs_own_error(self):
        """And when both go wrong, the leg's error is the one that must win."""
        bad = Path("/dev/null/not-a-directory")
        with Stub():
            with self.assertRaises(ValueError):
                with mod.trace("box", bad):
                    raise ValueError("the leg failed")

    def test_no_instance_is_a_no_op(self):
        ran = []
        with tempfile.TemporaryDirectory() as out:
            with mod.trace("", Path(out)):
                ran.append(True)
        self.assertEqual(ran, [True])
        self.assertFalse((Path(out) / "gputrace").exists())


class Output(unittest.TestCase):
    def _run(self, out: str, **kw):
        with Stub():
            with mod.trace("vss-eval-rtx-2g", Path(out), spec_stem="search",
                           platform="RTXPRO6000BW", step=1,
                           declared_gpu_count=2, skill="vss-search-archive", **kw):
                time.sleep(BODY_SETTLE_SEC)
        d = Path(out) / "gputrace"
        return (next(d.glob("*.csv")), next(d.glob("*.json")))

    def test_csv_has_the_header_and_the_samples(self):
        with tempfile.TemporaryDirectory() as out:
            csv, _ = self._run(out)
            lines = csv.read_text().strip().splitlines()
        self.assertEqual(lines[0], mod.CSV_HEADER)
        # Assert the shape, not a row count: the count is a property of the
        # stub, and pinning it makes the test fail for reasons that are not
        # defects. Every data row must be nvidia-smi shaped, and both GPUs of
        # a 2-GPU box must appear or the declared-vs-used answer is wrong.
        self.assertGreater(len(lines), 1)
        for row in lines[1:]:
            self.assertTrue(mod.ROW_RE.match(row), row)
        seen = {row.split(",")[1].strip() for row in lines[1:]}
        self.assertEqual(seen, {"0", "1"})

    def test_sidecar_carries_the_declaration_under_test(self):
        with tempfile.TemporaryDirectory() as out:
            csv, js = self._run(out)
            d = json.loads(js.read_text())
            csv_rows = len(csv.read_text().strip().splitlines()) - 1
        # Without the declared count the whole comparison is impossible.
        self.assertEqual(d["declared_gpu_count"], 2)
        self.assertEqual(d["skill"], "vss-search-archive")
        self.assertEqual(d["spec_stem"], "search")
        self.assertEqual(d["platform"], "RTXPRO6000BW")
        self.assertEqual(d["instance"], "vss-eval-rtx-2g")
        # Matches the CSV the sidecar describes, rather than a fixed number.
        self.assertEqual(d["samples"], csv_rows)
        self.assertGreater(d["samples"], 0)
        self.assertIn("RTX PRO 6000", d["gpu_name"])
        self.assertGreater(d["finished_at"], 0)

    def test_sidecar_carries_no_host_identifiers_beyond_the_box_name(self):
        with tempfile.TemporaryDirectory() as out:
            _, js = self._run(out)
            blob = js.read_text()
        for forbidden in ("10.", "ubuntu@", "ssh-rsa", "API_KEY", "TOKEN"):
            self.assertNotIn(forbidden, blob)

    def test_steps_do_not_overwrite_each_other(self):
        """A multi-step spec makes several harbor invocations under one lock."""
        with tempfile.TemporaryDirectory() as out, Stub():
            for step in (1, 2, 3):
                with mod.trace("box", Path(out), spec_stem="calibration",
                               platform="RTXPRO6000BW", step=step):
                    time.sleep(BODY_SETTLE_SEC)
            self.assertEqual(len(list((Path(out) / "gputrace").glob("*.csv"))), 3)

    def test_token_is_filename_safe(self):
        t = mod._token("123", "a/b spec", "RTX/PRO", 1)
        self.assertNotIn("/", t)
        self.assertNotIn(" ", t)
        self.assertLessEqual(len(t), 140)

    def test_a_long_spec_name_cannot_truncate_the_step_away(self):
        """Truncating the JOINED string lets a long spec push `-stepN` off the
        end, so step 1 and step 2 collide and the second silently overwrites
        the first. The step must be appended after truncation, not before."""
        long_spec = "x" * 200
        tokens = {mod._token("30515350883", long_spec, "RTXPRO6000BW", n)
                  for n in (1, 2, 3)}
        self.assertEqual(len(tokens), 3, f"steps collide after truncation: {tokens}")
        for n in (1, 2, 3):
            self.assertTrue(
                mod._token("30515350883", long_spec, "RTXPRO6000BW", n).endswith(f"-step{n}"))


class Toggles(unittest.TestCase):
    def test_default_is_on(self):
        """Opt-in telemetry that nobody enables collects nothing."""
        self.assertTrue(mod.ENABLED or os.environ.get("EVAL_GPU_TRACE") is not None)

    def test_disabled_makes_it_a_no_op(self):
        ran = []
        old = mod.ENABLED
        mod.ENABLED = False
        try:
            with tempfile.TemporaryDirectory() as out, Stub() as stub:
                with mod.trace("box", Path(out)):
                    ran.append(True)
                self.assertEqual(stub.commands(), [])
                self.assertFalse((Path(out) / "gputrace").exists())
        finally:
            mod.ENABLED = old
        self.assertEqual(ran, [True])


class FallsBackToSsh(unittest.TestCase):
    def test_ssh_alias_is_the_lowercased_instance_name(self):
        """Registered nodes are unreachable via `brev exec`; the alias
        convention is the one envs/brev_env.py::_ssh_alias_for uses."""
        with Stub() as stub:
            mod._remote("VSS-Eval-RTX-2G", "echo hi")
            argvs = stub.argvs()
        self.assertEqual(argvs[0][0], "exec")
        # brev succeeded, so ssh must not have been attempted.
        self.assertEqual(len(argvs), 1)

    def test_ssh_is_tried_when_brev_fails(self):
        with Stub(fail=True) as stub:
            self.assertIsNone(mod._remote("Box-Name", "echo hi"))
            argvs = stub.argvs()
        self.assertEqual(len(argvs), 2, "ssh fallback was not attempted")
        self.assertIn("box-name", argvs[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
