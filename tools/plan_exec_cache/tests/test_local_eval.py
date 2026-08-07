import importlib.util
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock


MODULE = (Path(__file__).resolve().parents[1]
          / "integrations/harbor/local_eval.py")
SPEC = importlib.util.spec_from_file_location("harbor_local_eval", MODULE)
local_eval = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(local_eval)


class LocalEvalTests(unittest.TestCase):
    def test_reads_task_step_and_spec(self):
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory)
            (task / "tests").mkdir()
            (task / "task.toml").write_text(
                "[metadata]\nstep_index = 4\n", encoding="utf-8"
            )
            spec = task / "tests" / "case.json"
            spec.write_text("{}", encoding="utf-8")

            self.assertEqual(local_eval.task_step(task), 4)
            self.assertEqual(local_eval.task_spec(task, None), spec)

    def test_reads_claude_result_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            trajectory = Path(directory) / "trajectory.jsonl"
            trajectory.write_text(
                json.dumps({"type": "assistant"}) + "\n" +
                json.dumps({
                    "type": "result",
                    "num_turns": 3,
                    "total_cost_usd": 0.25,
                    "usage": {
                        "input_tokens": 10,
                        "cache_read_input_tokens": 20,
                        "cache_creation_input_tokens": 30,
                        "output_tokens": 40,
                    },
                }) + "\n",
                encoding="utf-8",
            )

            result = local_eval.usage(local_eval.read_result(trajectory))

        self.assertEqual(result["total_tokens"], 100)
        self.assertEqual(result["num_turns"], 3)
        self.assertEqual(result["cost_usd"], 0.25)

    def test_summarizes_task_results(self):
        reports = [
            {
                "passed": True, "reward": 1.0, "total_tokens": 100,
                "cost_usd": 0.1, "agent_seconds": 2.0,
                "verifier_seconds": 1.0,
            },
            {
                "passed": True, "reward": 1.0, "total_tokens": 200,
                "cost_usd": 0.2, "agent_seconds": 3.0,
                "verifier_seconds": 1.5,
            },
        ]

        result = local_eval.summarize("direct", reports)

        self.assertTrue(result["passed"])
        self.assertEqual(result["total_tokens"], 300)
        self.assertAlmostEqual(result["cost_usd"], 0.3)
        self.assertEqual(result["agent_seconds"], 5.0)

    def test_prints_task_input_output_and_metrics(self):
        report = {
            "task": "/tmp/task-step", "mode": "cold", "passed": True,
            "reward": 1.0, "total_tokens": 1234, "cost_usd": 0.25,
            "agent_seconds": 2.0, "verifier_seconds": 1.5,
            "input": "Do the task", "agent_output": "Task complete",
            "output": "/tmp/output",
        }
        stream = StringIO()
        with redirect_stdout(stream):
            local_eval.print_task_report(report)

        text = stream.getvalue()
        self.assertIn("Do the task", text)
        self.assertIn("Task complete", text)
        self.assertIn("1,234 tokens", text)
        self.assertIn("$0.2500", text)
        self.assertIn("reward 1.000", text)
        self.assertIn("total 3.5s", text)

    def test_claude_command_does_not_override_tools_or_effort(self):
        command = local_eval.claude_command("do it", "model-name")
        self.assertNotIn("--tools", command)
        self.assertNotIn("--effort", command)
        self.assertEqual(
            command[command.index("--permission-mode") + 1],
            "bypassPermissions",
        )
        self.assertIn("--no-session-persistence", command)

    def test_optimized_mode_requires_explicit_cache(self):
        with self.assertRaises(ValueError):
            local_eval.mode_environment("cold", None)

    def test_cli_defaults_to_timestamped_cache(self):
        args = local_eval.parse_args([
            "--task", "/tmp/task", "--mode", "compare",
        ])
        self.assertEqual(args.cache_home.name, "cache")
        self.assertEqual(args.output.name, "results")
        self.assertEqual(args.cache_home.parent, args.output.parent)
        self.assertEqual(args.cache_home.parent.parent,
                         local_eval.REPO_ROOT / "local_eval")
        self.assertRegex(args.cache_home.parent.name, r"^\d{8}t\d{6}z$")

    def test_compare_accepts_selected_arms(self):
        args = local_eval.parse_args([
            "--task", "/tmp/task", "--mode", "compare",
            "--arms", "cold", "warm",
        ])
        self.assertEqual(args.arms, ["cold", "warm"])

    def test_accepts_runs_alias_and_comma_separated_arms(self):
        args = local_eval.parse_args([
            "--task", "/tmp/task", "--mode", "compare",
            "--arms", "direct,", "cold,", "warm", "--repeat", "3",
        ])
        self.assertEqual(args.runs, 3)
        self.assertEqual(
            local_eval.normalize_arms(args.arms),
            ("direct", "cold", "warm"),
        )

    def test_averages_runs_and_renders_comparison_table(self):
        def run(tokens, cost, seconds):
            arms = {}
            for mode, factor in (("direct", 1.0), ("cold", 0.5)):
                task = {
                    "task": "/tmp/task", "mode": mode, "passed": True,
                    "reward": 1.0, "total_tokens": tokens * factor,
                    "cost_usd": cost * factor,
                    "agent_seconds": seconds * factor,
                    "verifier_seconds": 100.0,
                }
                arms[mode] = local_eval.summarize(mode, [task])
            return {"passed": True, "arms": arms}

        average = local_eval.average_runs(
            [run(100, 1.0, 10.0), run(200, 2.0, 20.0)],
            ("direct", "cold"),
        )
        table = local_eval.comparison_table(average, ["Upload"])

        self.assertEqual(average["arms"]["direct"]["total_tokens"], 150)
        self.assertEqual(average["arms"]["cold"]["cost_usd"], 0.75)
        self.assertIn("All 1 task", table)
        self.assertIn("T -50.0% &#124; $ -50.0% &#124; L -50.0%", table)
        self.assertIn("| $1.5000 | 15.0s |", table)

    def test_all_modes_disable_cross_session_memory_not_thinking(self):
        with mock.patch.dict(
            local_eval.os.environ,
            {"CLAUDE_CODE_DISABLE_THINKING": "1"},
        ):
            env = local_eval.mode_environment("direct", None)
        self.assertEqual(env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"], "1")
        self.assertNotIn("CLAUDE_CODE_DISABLE_THINKING", env)

    def test_task_config_exposes_only_bundled_skills(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = root / "task"
            bundled = task / "skills" / "target-skill"
            bundled.mkdir(parents=True)
            (bundled / "SKILL.md").write_text("# Target\n", encoding="utf-8")
            output = root / "output"
            output.mkdir()

            config = local_eval.prepare_claude_config(task, output)

            link = config / "skills" / "target-skill"
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.resolve(), bundled.resolve())
            self.assertEqual([path.name for path in link.parent.iterdir()],
                             ["target-skill"])

    def test_cold_and_warm_cache_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cold = root / "cold"
            local_eval.check_cache("cold", cold)
            cold.mkdir()
            (cold / "memory").write_text("present", encoding="utf-8")
            with self.assertRaises(ValueError):
                local_eval.check_cache("cold", cold)
            with self.assertRaises(ValueError):
                local_eval.check_cache("warm", cold)

            memory = cold / "memories" / "demo.action"
            memory.mkdir(parents=True)
            (memory / "procedure.md").write_text("plan", encoding="utf-8")
            local_eval.check_cache("warm", cold)

            with self.assertRaises(ValueError):
                local_eval.check_cache("warm", root / "missing")

    def test_compare_resets_each_arm_and_reuses_cold_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tasks = []
            for name in ("upload", "ready"):
                task = root / name
                (task / "tests").mkdir(parents=True)
                (task / "instruction.md").write_text("do it", encoding="utf-8")
                (task / "task.toml").write_text(
                    "[metadata]\nstep_index = 1\n", encoding="utf-8"
                )
                (task / "tests" / "case.json").write_text(
                    "{}", encoding="utf-8"
                )
                tasks.append(task)
            reset = root / "reset"
            reset.write_text("reset", encoding="utf-8")
            cache = root / "cache"
            output = root / "output"
            modes = []

            def fake_evaluate(task, mode, cache_home, task_output, *_args):
                modes.append(mode)
                if mode == "cold":
                    memory = cache_home / "memories" / "demo.action"
                    memory.mkdir(parents=True, exist_ok=True)
                    (memory / "procedure.md").write_text(
                        "procedure", encoding="utf-8"
                    )
                return {
                    "passed": True, "reward": 1.0, "total_tokens": 10,
                    "cost_usd": 0.01, "agent_seconds": 1.0,
                    "verifier_seconds": 0.5,
                }

            argv = ["--mode", "compare"]
            for task in tasks:
                argv += ["--task", str(task)]
            argv += [
                "--reset-script", str(reset),
                "--cache-home", str(cache),
                "--output", str(output),
            ]
            with mock.patch.object(local_eval, "evaluate_task",
                                   side_effect=fake_evaluate), \
                    mock.patch.object(local_eval, "run_reset") as run_reset:
                returncode = local_eval.main(argv)

            self.assertEqual(returncode, 0)
            self.assertEqual(modes, ["direct"] * 2 + ["cold"] * 2 + ["warm"] * 2)
            self.assertEqual(run_reset.call_count, 3)
            report = json.loads((output / "result.json").read_text())
            self.assertEqual(set(report["arms"]), {"direct", "cold", "warm"})

    def test_compare_runs_only_selected_arms(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = root / "task"
            (task / "tests").mkdir(parents=True)
            (task / "instruction.md").write_text("do it", encoding="utf-8")
            (task / "task.toml").write_text(
                "[metadata]\nstep_index = 1\n", encoding="utf-8"
            )
            (task / "tests" / "case.json").write_text(
                "{}", encoding="utf-8"
            )
            reset = root / "reset"
            reset.write_text("reset", encoding="utf-8")
            cache = root / "cache"
            calls = []

            def fake_evaluate(_task, mode, cache_home, _output, *_args):
                calls.append(mode)
                if mode == "cold":
                    memory = cache_home / "memories" / "demo.action"
                    memory.mkdir(parents=True)
                    (memory / "procedure.md").write_text(
                        "procedure", encoding="utf-8"
                    )
                return {
                    "passed": True, "reward": 1.0, "total_tokens": 10,
                    "cost_usd": 0.01, "agent_seconds": 1.0,
                    "verifier_seconds": 0.5,
                }

            argv = [
                "--mode", "compare", "--arms", "cold", "warm",
                "--task", str(task), "--reset-script", str(reset),
                "--cache-home", str(cache),
                "--output", str(root / "output"),
            ]
            with mock.patch.object(local_eval, "evaluate_task",
                                   side_effect=fake_evaluate), \
                    mock.patch.object(local_eval, "run_reset") as run_reset:
                returncode = local_eval.main(argv)

            self.assertEqual(returncode, 0)
            self.assertEqual(calls, ["cold", "warm"])
            self.assertEqual(run_reset.call_count, 2)
            report = json.loads((root / "output" / "result.json").read_text())
            self.assertEqual(set(report["arms"]), {"cold", "warm"})

    def test_repeated_compare_isolates_caches_and_records_each_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = root / "task"
            (task / "tests").mkdir(parents=True)
            (task / "instruction.md").write_text("do it", encoding="utf-8")
            (task / "task.toml").write_text(
                "[metadata]\nstep_index = 1\n", encoding="utf-8"
            )
            (task / "tests" / "case.json").write_text(
                "{}", encoding="utf-8"
            )
            reset = root / "reset"
            reset.write_text("reset", encoding="utf-8")
            calls = []

            def fake_evaluate(_task, mode, cache_home, task_output, *_args):
                calls.append((mode, cache_home, task_output))
                if mode == "cold":
                    memory = cache_home / "memories" / "demo.action"
                    memory.mkdir(parents=True)
                    (memory / "procedure.md").write_text(
                        "procedure", encoding="utf-8"
                    )
                return {
                    "task": str(task), "mode": mode, "passed": True,
                    "reward": 1.0, "total_tokens": 10,
                    "cost_usd": 0.01, "agent_seconds": 1.0,
                    "verifier_seconds": 0.5,
                }

            output = root / "output"
            argv = [
                "--mode", "compare", "--runs", "3",
                "--task", str(task), "--task-label", "Upload",
                "--reset-script", str(reset),
                "--cache-home", str(root / "cache"),
                "--output", str(output),
            ]
            with mock.patch.object(local_eval, "evaluate_task",
                                   side_effect=fake_evaluate), \
                    mock.patch.object(local_eval, "run_reset") as run_reset, \
                    redirect_stdout(StringIO()):
                returncode = local_eval.main(argv)

            self.assertEqual(returncode, 0)
            self.assertEqual(len(calls), 9)
            self.assertEqual(run_reset.call_count, 9)
            self.assertEqual(
                {cache.name for _, cache, _ in calls},
                {"run-01", "run-02", "run-03"},
            )
            self.assertTrue((output / "run-01" / "direct").is_dir())
            report = json.loads((output / "result.json").read_text())
            self.assertEqual(report["run_count"], 3)
            self.assertEqual(len(report["runs"]), 3)
            self.assertEqual(report["average"]["arms"]["warm"]
                             ["total_tokens"], 10)
            summary = (output / "summary.md").read_text()
            self.assertIn("## Run 3", summary)
            self.assertIn("## Average across 3 runs", summary)
            self.assertIn("| Upload |", summary)

    def test_compare_records_failures_without_stopping(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tasks = []
            for name in ("first", "second"):
                task = root / name
                (task / "tests").mkdir(parents=True)
                (task / "instruction.md").write_text("do it", encoding="utf-8")
                (task / "task.toml").write_text(
                    "[metadata]\nstep_index = 1\n", encoding="utf-8"
                )
                (task / "tests" / "case.json").write_text(
                    "{}", encoding="utf-8"
                )
                tasks.append(task)
            reset = root / "reset"
            reset.write_text("reset", encoding="utf-8")
            cache = root / "cache"
            calls = []

            def fake_evaluate(task, mode, cache_home, task_output, *_args):
                calls.append((mode, task.name))
                if mode == "cold":
                    memory = cache_home / "memories" / "demo.action"
                    memory.mkdir(parents=True, exist_ok=True)
                    (memory / "procedure.md").write_text(
                        "procedure", encoding="utf-8"
                    )
                passed = not (mode == "direct" and task.name == "first")
                return {
                    "passed": passed,
                    "reward": 1.0 if passed else 0.5,
                    "total_tokens": 10,
                    "cost_usd": 0.01,
                    "agent_seconds": 1.0,
                    "verifier_seconds": 0.5,
                }

            argv = ["--mode", "compare"]
            for task in tasks:
                argv += ["--task", str(task)]
            argv += [
                "--reset-script", str(reset),
                "--cache-home", str(cache),
                "--output", str(root / "output"),
            ]
            with mock.patch.object(local_eval, "evaluate_task",
                                   side_effect=fake_evaluate), \
                    mock.patch.object(local_eval, "run_reset"):
                returncode = local_eval.main(argv)

            self.assertEqual(returncode, 1)
            self.assertEqual(
                calls,
                [(mode, task.name) for mode in ("direct", "cold", "warm")
                 for task in tasks],
            )


if __name__ == "__main__":
    unittest.main()
