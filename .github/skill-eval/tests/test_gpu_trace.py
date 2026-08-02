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
import subprocess
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
if os.environ.get("GPUTRACE_STUB_HOSTILE_DIR") == "1" and "echo PID=$!" in cmd:
    # A box replying with a directory that carries shell syntax. Without the
    # trailing anchor on DIR_RE this lands inside `rm -rf {remote_dir}`.
    sys.stdout.write("PID=4242\n")
    sys.stdout.write("DIR=/tmp/vss-gputrace.aaaaaaaa; rm -f /tmp/CANARY_VICTIM #\n")
    sys.stdout.write(argv[1] + "\n" if len(argv) > 1 else "")
    raise SystemExit
if os.environ.get("GPUTRACE_STUB_HOSTILE_PID") == "1" and "echo PID=$!" in cmd:
    # A compromised or confused box reports shell syntax where a pid belongs.
    sys.stdout.write("PID=1; touch " + os.environ["GPUTRACE_STUB_HOME"] + "/PWNED\n")
    sys.stdout.write("DIR=/tmp/vss-gputrace.aaaaaaaa\n")
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
# The pid the fake reports. Shared by the stub and the assertion so the two
# cannot drift; a hardcoded literal in only one of them is how "some number is
# killed" passed while the wrong number was being killed.
STUB_PID = "4242"
NVIDIA_SMI_STUB = r"""#!/usr/bin/env python3
# A fake that IMPLEMENTS nvidia-smi's contract rather than encoding whatever the
# caller happens to ask for. Three independent reviews found the previous
# version ignored argv entirely, so deleting `--query-gpu=...`, appending
# `gpu_uuid,gpu_serial`, adding `-i 0`, or changing `--format` all passed the
# entire suite while collecting nothing usable on a real box.
#
# Rows are BUILT FROM THE PARSED FIELD LIST, so reordering or extending the
# query changes the output and its column count. That is what makes a drift
# between the command and CSV_HEADER visible instead of silent.
import os, sys, time

def ts(i, n): return "2026/08/01 21:00:{:02d}.000".format(n)
FIELDS = {
    "timestamp": ts,
    "index": lambda i, n: str(i),
    "name": lambda i, n: "NVIDIA RTX PRO 6000 Blackwell Server Edition",
    "utilization.gpu": lambda i, n: "97" if i else "0",
    "utilization.memory": lambda i, n: "3" if i else "0",
    "memory.used": lambda i, n: "74600" if i else "42800",
    "memory.total": lambda i, n: "97887",
    "power.draw": lambda i, n: "310.5" if i else "71.2",
    "gpu_uuid": lambda i, n: "GPU-deadbeef-0000-0000-0000-00000000000" + str(i),
    "gpu_serial": lambda i, n: "132000000000" + str(i),
    "temperature.gpu": lambda i, n: "34",
    "power.limit": lambda i, n: "600.00",
}
UNITS = {"utilization.gpu": " %", "utilization.memory": " %", "memory.used": " MiB",
         "memory.total": " MiB", "power.draw": " W", "power.limit": " W"}

argv = sys.argv[1:]
query = None; fmt = ""; loop = None; outfile = None; ids = None
k = 0
while k < len(argv):
    a = argv[k]
    if a.startswith("--query-gpu="): query = a.split("=", 1)[1].split(",")
    elif a.startswith("--format="):  fmt = a.split("=", 1)[1]
    elif a == "-l": k += 1; loop = argv[k]
    elif a == "-f": k += 1; outfile = argv[k]
    elif a in ("-i", "--id"): k += 1; ids = [int(x) for x in argv[k].split(",")]
    k += 1

if query is None:
    sys.stderr.write("Invalid combination of input arguments.\n"); raise SystemExit(2)
for f in query:
    if f not in FIELDS:
        sys.stderr.write('Field "' + f + '" is not a valid field to query.\n'); raise SystemExit(2)
if query == ["name"]:
    print(FIELDS["name"](0, 0)); raise SystemExit

noheader = "noheader" in fmt; nounits = "nounits" in fmt
gpus = ids if ids is not None else [0, 1]
out = open(outfile, "w") if outfile else sys.stdout

def emit(n):
    if n == 0 and not noheader:
        out.write(", ".join(query) + "\n")
    for i in gpus:
        cells = []
        for f in query:
            v = FIELDS[f](i, n)
            if not nounits and f in UNITS: v += UNITS[f]
            cells.append(v)
        out.write(", ".join(cells) + "\n")

if os.environ.get("GPUTRACE_SMI_UNSUPPORTED") == "1":
    # Real nvidia-smi prints these for unqueryable fields on some SKUs and under
    # vGPU. ROW_RE accepts [N/A] and rejects [Not Supported]; a test can now say
    # which is intended instead of the fake never emitting either.
    out.write("2026/08/01 21:00:00.000, 0, [Not Supported], 0, 42800, 97887, [N/A]\n")

emit(0)
out.write("NGC_CLI_API_KEY=CANARYSECRET,a,b,c,d,e,f\n")
out.flush()
if loop is None: raise SystemExit
# Actually loop, so the interval is observable rather than merely present.
n = 1
end = time.time() + __RUNSEC__
while time.time() < end:
    time.sleep(min(float(loop), 0.5)); emit(n); out.flush(); n += 1
""".replace("__RUNSEC__", str(SMI_RUN_SEC))

# macOS has no GNU `timeout`; the eval boxes are Linux and do. Without one on
# PATH the executed command fails and no trace is produced, so the suite needs
# a stand-in that honours `-k N <seconds> <cmd...>` and forwards SIGTERM.
TIMEOUT_STUB = r"""#!/usr/bin/env python3
# Implements GNU timeout's contract instead of discarding it. The previous fake
# parsed the duration off argv and threw it away -- no timer, no escalation, no
# exit 124 -- so the "hard-bounded lifetime" this module exists for had zero
# behavioural coverage. Measured consequence: `MAX_SEC=0` and `-k 0` both passed
# the whole suite, and under real GNU timeout a duration of 0 means the timeout
# is DISABLED, i.e. exactly the unbounded sampler the bound is there to prevent.
import os, signal, subprocess, sys, threading, time

a = sys.argv[1:]
kill_after = None
if a and a[0] == "-k":
    if len(a) < 2:
        sys.stderr.write("timeout: option requires an argument -- 'k'\n"); raise SystemExit(125)
    kill_after = a[1]; a = a[2:]
if kill_after is None:
    # Deliberately stricter than GNU timeout, which allows -k to be absent.
    # This codebase's hard-stop guarantee IS the escalation: a sampler wedged
    # in an nvidia-smi driver call ignores SIGTERM, and without SIGKILL it
    # outlives the bound on a machine we are renting.
    sys.stderr.write("timeout-stub: -k is required by this caller's contract\n")
    raise SystemExit(125)
if not a:
    sys.stderr.write("timeout: missing operand\n"); raise SystemExit(125)
duration, cmd = a[0], a[1:]

def _secs(v, what):
    try:
        f = float(v)
    except ValueError:
        sys.stderr.write("timeout: invalid time interval '%s'\n" % v); raise SystemExit(125)
    if f <= 0:
        # GNU timeout treats 0 as "no timeout at all". The tests refuse it so
        # that pinning either value to zero is a failure rather than a shrug.
        sys.stderr.write("timeout-stub: %s of 0 disables the bound; refused\n" % what)
        raise SystemExit(125)
    return f

secs = _secs(duration, "duration")
kill_secs = _secs(kill_after, "kill-after") if kill_after is not None else None

p = subprocess.Popen(cmd, start_new_session=True)
expired = {"v": False}

def _fire():
    expired["v"] = True
    try: os.killpg(p.pid, signal.SIGTERM)
    except Exception: pass
    if kill_secs is not None:
        def _hard():
            time.sleep(kill_secs)
            if p.poll() is None:
                try: os.killpg(p.pid, signal.SIGKILL)
                except Exception: pass
        threading.Thread(target=_hard, daemon=True).start()

t = threading.Timer(secs, _fire); t.daemon = True; t.start()
signal.signal(signal.SIGTERM, lambda *_: _fire())
rc = p.wait()
t.cancel()
sys.exit(124 if expired["v"] else rc)
"""


class Stub:
    """Puts a fake `brev` (and a failing `ssh`) first on PATH."""

    KEYS = ("PATH", "GPUTRACE_STUB_REC", "GPUTRACE_STUB_FAIL",
            "GPUTRACE_STUB_HOME", "GPUTRACE_STUB_BIN", "GPUTRACE_STUB_HOSTILE_PID",
            "GPUTRACE_STUB_HOSTILE_DIR")

    def __init__(self, fail: bool = False, hostile_pid: bool = False,
                 payload: str | None = None, hostile_dir: bool = False):
        self.fail = fail
        self.hostile_pid = hostile_pid
        self.hostile_dir = hostile_dir
        # Override what the fake nvidia-smi emits, so a test can assert on how
        # many samples survive the round trip rather than on a fixed fixture.
        self.payload = payload

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
        smi = NVIDIA_SMI_STUB
        if self.payload is not None:
            # Honours -f like the real binary and like the main fake. A payload
            # override that wrote to stdout would silently produce no trace once
            # the production command stopped using a shell redirect -- the same
            # "fake encodes the caller's assumption" trap, one level down.
            smi = ('#!/usr/bin/env python3\nimport sys, time\n'
                   'argv = sys.argv[1:]\n'
                   'if "--query-gpu=name" in " ".join(argv):\n'
                   '    print("FAKE"); raise SystemExit\n'
                   'out = open(argv[argv.index("-f") + 1], "w") if "-f" in argv else sys.stdout\n'
                   'out.write(%r)\nout.flush()\n'
                   'if "-l" in argv: time.sleep(%d)\n'
                   % (self.payload if self.payload.endswith("\n") else self.payload + "\n",
                      SMI_RUN_SEC))
        for name, body in (("nvidia-smi", smi), ("timeout", TIMEOUT_STUB)):
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
        os.environ["GPUTRACE_STUB_HOSTILE_DIR"] = "1" if self.hostile_dir else "0"
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


class TheOtherRegexGuardsAnInterpolation(unittest.TestCase):
    """DIR_RE guards the same kind of interpolation PID_RE does, and had no
    test at all. Removing its `$` anchor -- one character -- lets a remote
    reply carry a trailing `; rm -f <path> #` straight into `rm -rf {dir}`,
    which was demonstrated end to end by deleting a victim file."""

    def test_a_directory_carrying_shell_syntax_is_refused(self):
        for hostile in (
            "/tmp/vss-gputrace.aaaaaaaa; rm -rf /",
            "/tmp/vss-gputrace.aaaaaaaa && curl evil",
            "/tmp/vss-gputrace.aaaaaaaa\nrm -rf /",
            "/tmp/vss-gputrace.aaaaaaaa/../../etc",
            "/tmp/vss-gputrace.$(id)",
            "/etc", "", "/tmp/vss-gputrace.short",
        ):
            self.assertIsNone(mod.DIR_RE.match(hostile), hostile)
        self.assertTrue(mod.DIR_RE.match("/tmp/vss-gputrace.aB3xY9zQ"))

    def test_the_regex_is_anchored_at_both_ends(self):
        """Asserted explicitly because a trailing-anchor deletion is invisible
        to every match-based test that only feeds it clean values."""
        self.assertTrue(mod.DIR_RE.pattern.startswith("^"))
        self.assertTrue(mod.DIR_RE.pattern.endswith("$"))
        self.assertTrue(mod.ROW_RE.pattern.endswith("$"))
        self.assertTrue(mod.PID_RE.pattern.endswith("$"))

    def test_a_hostile_directory_never_reaches_the_cleanup_shell(self):
        """End to end, mirroring the hostile-pid test."""
        with tempfile.TemporaryDirectory() as out, Stub(hostile_dir=True) as stub:
            with mod.trace("box", Path(out), spec_stem="hostiledir"):
                pass
            cmds = " ".join(stub.commands())
        self.assertNotIn("rm -f /tmp/CANARY_VICTIM", cmds,
                         f"injected command survived DIR_RE: {cmds!r}")

    def test_a_row_with_a_valid_prefix_and_a_garbage_suffix_is_dropped(self):
        """Every existing negative fails on the PREFIX. Deleting ROW_RE's
        trailing anchor lets a well-formed row carry anything after it, which
        is the PR #516 channel restored by one byte."""
        good = "2026/08/01 21:00:00.000, 1, 97, 3, 74600, 97887, 310.5"
        self.assertTrue(mod.ROW_RE.match(good))
        for suffix in (
            ",-----BEGIN OPENSSH PRIVATE KEY----- CANARYSECRET",
            " -----BEGIN OPENSSH PRIVATE KEY-----",
            ",nvapi-CANARYSECRET",
            ", 1, 2, 3",
        ):
            self.assertIsNone(mod.ROW_RE.match(good + suffix), suffix)


class TheChainIdentityIsCarried(unittest.TestCase):
    """`chain` appeared zero times in this file, while run_leg.py passes a real
    chain_key and two chains sharing a step index wrote the same filename."""

    def test_two_chains_at_the_same_step_do_not_collide(self):
        a = mod._token("30515350883", "search", "RTX", 1, "chainA")
        b = mod._token("30515350883", "search", "RTX", 1, "chainB")
        self.assertNotEqual(a, b, "two chains produce the same trace filename")

    def test_chains_do_not_overwrite_each_other_end_to_end(self):
        with tempfile.TemporaryDirectory() as out, Stub():
            for chain in ("remote-all", "standalone"):
                with mod.trace("box", Path(out), spec_stem="search",
                               platform="RTX", step=1, chain=chain):
                    time.sleep(BODY_SETTLE_SEC)
            self.assertEqual(len(list((Path(out) / "gputrace").glob("*.csv"))), 2)


class NoSamplesMeansNoFile(unittest.TestCase):
    """A trace file that says "declared 2 GPUs, 0 samples over 70 minutes" is
    indistinguishable from a genuinely idle 2-GPU box and argues directly for
    the irreversible downgrade. Absence is the only honest answer."""

    def test_an_empty_fetch_writes_nothing_at_all(self):
        with tempfile.TemporaryDirectory() as out, Stub(payload=""):
            with mod.trace("box", Path(out), spec_stem="nosamples",
                           declared_gpu_count=2):
                time.sleep(BODY_SETTLE_SEC)
            # Asserted INSIDE the TemporaryDirectory. Outside it the directory
            # is already gone and `.exists()` is vacuously False, so the test
            # passes without testing anything -- which is exactly what it did.
            d = Path(out) / "gputrace"
            written = sorted(p.name for p in d.glob("*")) if d.exists() else []
        self.assertEqual(written, [], "a zero-sample trace was published as fact")

    def test_rows_that_all_fail_the_filter_write_nothing(self):
        with tempfile.TemporaryDirectory() as out, Stub(payload="garbage\nmore garbage\n"):
            with mod.trace("box", Path(out), spec_stem="allbad"):
                time.sleep(BODY_SETTLE_SEC)
            d = Path(out) / "gputrace"
            written = sorted(p.name for p in d.glob("*")) if d.exists() else []
        self.assertEqual(written, [],
                         "rows that all failed the filter were published anyway")


class TheKnobsParseSafely(unittest.TestCase):
    """_int_env and the ENABLED parse had no test. A malformed knob used to
    fail the leg at import; a zero one disables the caps it guards."""

    def test_int_env_rejects_garbage_and_non_positive(self):
        for raw in ("garbage", "", "-1", "0", "1.5", None):
            env = {} if raw is None else {"X_KNOB": raw}
            old = os.environ.pop("X_KNOB", None)
            os.environ.update(env)
            try:
                self.assertEqual(mod._int_env("X_KNOB", 42), 42, repr(raw))
            finally:
                os.environ.pop("X_KNOB", None)
                if old is not None:
                    os.environ["X_KNOB"] = old

    def test_int_env_accepts_a_real_override(self):
        os.environ["X_KNOB"] = "7"
        try:
            self.assertEqual(mod._int_env("X_KNOB", 42), 7)
        finally:
            os.environ.pop("X_KNOB", None)

    def test_the_documented_opt_out_string_actually_disables(self):
        """`EVAL_GPU_TRACE=0` is the documented kill switch. Tests that set
        mod.ENABLED directly never exercise the parse that implements it."""
        import importlib
        for raw, want in (("0", False), ("false", False), ("False", False),
                          ("", False), ("1", True), ("yes", True)):
            os.environ["EVAL_GPU_TRACE"] = raw
            try:
                importlib.reload(mod)
                self.assertIs(mod.ENABLED, want, f"EVAL_GPU_TRACE={raw!r}")
            finally:
                os.environ.pop("EVAL_GPU_TRACE", None)
        importlib.reload(mod)


class TheFetchCapCountsBytes(unittest.TestCase):
    """The cap's whole purpose is bounding memory. Nothing drove more than a
    few KB through it, so counting characters instead of bytes, or counting
    chunks instead of length, or dropping the killpg were all invisible."""

    def test_the_cap_is_enforced_and_reported(self):
        old = mod.MAX_FETCH_BYTES
        mod.MAX_FETCH_BYTES = 4096
        try:
            proc = subprocess.Popen(
                [sys.executable, "-c",
                 "import sys; sys.stdout.write('x' * 200000)"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, start_new_session=True)
            out = mod._read_capped(proc, 20)
        finally:
            mod.MAX_FETCH_BYTES = old
        self.assertLessEqual(len(out.encode("utf-8")), 4096,
                             "the cap counted something other than bytes")

    def test_an_endless_source_is_killed_not_merely_truncated(self):
        old = mod.MAX_FETCH_BYTES
        mod.MAX_FETCH_BYTES = 2048
        try:
            proc = subprocess.Popen(
                [sys.executable, "-c",
                 "import sys, time\n"
                 "while True:\n"
                 "    sys.stdout.write('y' * 8192); sys.stdout.flush()"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, start_new_session=True)
            mod._read_capped(proc, 20)
            time.sleep(1)
            alive = proc.poll() is None
        finally:
            mod.MAX_FETCH_BYTES = old
            with contextlib_suppress():
                proc.kill()
        self.assertFalse(alive, "the emitter kept running after the cap fired")


def contextlib_suppress():
    import contextlib
    return contextlib.suppress(Exception)


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
    PID_FROM_STUB = STUB_PID

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

    def test_the_sampler_loops_for_the_life_of_the_leg(self):
        """Dropping `-l` makes nvidia-smi print once and exit, which looks
        identical to lifetime sampling in a fixture that emits two rows up
        front. Then a 2-hour leg yields one instant instead of a trace."""
        with tempfile.TemporaryDirectory() as out, Stub() as stub:
            with mod.trace("box", Path(out)):
                pass
            start = stub.commands()[0]
        self.assertRegex(start, r"-l \d+",
                         f"sampler does not loop: {start!r}")

    def test_the_output_path_is_unpredictable(self):
        """A name derived from the job identity is guessable, and shell `>`
        follows symlinks: a pre-placed `<token>.csv -> ~/.ssh/authorized_keys`
        gets truncated. Verified in real bash on a victim holding KEEP-ME."""
        with tempfile.TemporaryDirectory() as out, Stub() as stub:
            with mod.trace("box", Path(out), spec_stem="search",
                           platform="RTXPRO6000BW", step=1):
                pass
            start = stub.commands()[0]
        self.assertIn("mktemp -d", start)
        self.assertIn("set -C", start)          # noclobber: refuse to follow
        self.assertNotIn("search", start.split("nvidia-smi")[0],
                         f"job identity leaked into the remote path: {start!r}")

    def test_the_bound_escalates_to_sigkill(self):
        """`-k` is the whole guarantee: SIGTERM alone cannot stop a sampler
        wedged in a driver call. The floor on hard_stop is 900s so a real
        expiry cannot be driven from a test, hence the explicit assertion --
        the fake refuses a missing -k, which covers the behaviour."""
        with tempfile.TemporaryDirectory() as out, Stub() as stub:
            with mod.trace("box", Path(out)):
                pass
            start = stub.commands()[0]
        self.assertRegex(start, r"timeout -k [1-9][0-9]* [0-9]+",
                         f"no SIGKILL escalation on the hard stop: {start!r}")

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
            finish = stub.commands()[-1]
        # The whole mktemp directory, not one file: it is created per
        # invocation and a box that has been up six weeks must not accumulate
        # one directory per leg.
        self.assertIn("rm -rf /tmp/vss-gputrace.", finish)

    def test_the_sampler_is_actually_stopped(self):
        """Deleting the kill left all 22 of the original tests green. The
        cleanup command must name the pid the start call reported."""
        with tempfile.TemporaryDirectory() as out, Stub() as stub:
            with mod.trace("box", Path(out)):
                time.sleep(BODY_SETTLE_SEC)
            # Scoped to THIS stub's bin directory. A broad pattern matched an
            # unrelated stale process on the developer machine and reported a
            # survivor that was never ours.
            alive_after = len(subprocess.run(
                ["pgrep", "-f", str(stub.bin)],
                capture_output=True, text=True).stdout.split())
            cmds = stub.commands()
        start, finish = cmds[0], cmds[-1]
        self.assertIn("echo PID=$!", start)
        # The invariant is not "a kill command was emitted", it is "the sampler
        # is dead". Asserting the former let a mutation that hardcodes the pid
        # to 999999 pass while the real sampler kept running.
        self.assertEqual(alive_after, 0,
                         f"sampler survived the cleanup: {finish!r}")
        # It must also confirm the pid is still OUR sampler before signalling:
        # a pid that was freed and reused points at an unrelated process.
        # Matching the per-invocation nonce directory, not merely "some
        # nvidia-smi": these boxes run nvidia-smi constantly, so a recycled pid
        # that happens to be any of them would be killed, possibly another leg's.
        guard = finish.split("kill")[0]
        self.assertIn("ps -o args= -p", guard,
                      f"pid is signalled without an identity check: {finish!r}")
        self.assertRegex(guard, r"grep -q /tmp/vss-gputrace\.[A-Za-z0-9]{8}",
                         f"identity check is not scoped to this invocation: {finish!r}")

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

    def test_header_is_the_literal_contract_downstream_reads(self):
        """Spelled out, NOT compared against the production constant. Swapping
        `mem_used_mib` and `mem_total_mib` in CSV_HEADER passed the whole suite
        because the test read the same constant it was checking, and downstream
        would then report resident memory as capacity and vice versa."""
        self.assertEqual(mod.CSV_HEADER,
                         "timestamp,gpu_index,util_gpu_pct,util_mem_pct,"
                         "mem_used_mib,mem_total_mib,power_w")
        # And the header order must match the order nvidia-smi is asked for.
        self.assertEqual(mod.QUERY_FIELDS,
                         "timestamp,index,utilization.gpu,utilization.memory,"
                         "memory.used,memory.total,power.draw")

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

    def test_every_returned_sample_is_kept(self):
        """Truncating to the first N rows makes a 2-hour leg look like its
        opening seconds while the file still parses and the sidecar's own
        count still agrees with it. Nothing else in the suite would notice."""
        many = "\n".join(
            f"2026/08/01 21:{i // 60:02d}:{i % 60:02d}.000, {i % 2}, {i}, 0, 100, 97887, 71.2"
            for i in range(40))
        with tempfile.TemporaryDirectory() as out, Stub(payload=many):
            with mod.trace("box", Path(out), spec_stem="keepall"):
                time.sleep(BODY_SETTLE_SEC)
            csv = next((Path(out) / "gputrace").glob("*.csv"))
            rows = csv.read_text().strip().splitlines()[1:]
        self.assertEqual(len(rows), 40, "samples were dropped")

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
