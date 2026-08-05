import importlib.util
import json
import tempfile
import unittest
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
        self.assertEqual(
            args.cache_home.parent, Path("/tmp/skill-eval-local-cache")
        )
        self.assertRegex(args.cache_home.name, r"^\d{8}t\d{6}z$")

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
