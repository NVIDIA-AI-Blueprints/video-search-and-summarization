# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import ast
import asyncio
import json
import os
import re
import sys
import types
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import pytest

NOTEBOOK = Path(__file__).resolve().parents[1] / "train_rl_adapter.ipynb"
ATTRIBUTION = NOTEBOOK.parent / "LICENSE-3rd-party.txt"


def _notebook():
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def _source(cell):
    return "".join(cell.get("source", []))


def _forbidden(name):
    def fail(*args, **kwargs):
        raise AssertionError(f"forbidden dry-run side effect: {name}: {args!r}")

    return fail


def _execute_dry_run(home):
    notebook = _notebook()
    namespace = {"__name__": "__main__"}
    blocked = (
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "urllib.request.urlopen",
        "socket.create_connection",
        "os.system",
        "os.popen",
        "os.kill",
        "time.sleep",
        "builtins.open",
        "tarfile.open",
        "shutil.copy",
        "shutil.copy2",
        "shutil.copyfile",
        "shutil.copytree",
        "shutil.move",
        "shutil.rmtree",
    )
    with ExitStack() as stack:
        stack.enter_context(
            mock.patch.dict(
                os.environ,
                {
                    "HOME": str(home),
                    "TRAIN_RL_ADAPTER_CONFIG": "",
                },
            )
        )
        for target in blocked:
            stack.enter_context(mock.patch(target, _forbidden(target)))
        for name in (
            "open",
            "write_text",
            "write_bytes",
            "mkdir",
            "unlink",
            "rename",
            "replace",
            "touch",
            "rmdir",
        ):
            stack.enter_context(
                mock.patch.object(Path, name, _forbidden(f"Path.{name}"))
            )
        for index, cell in enumerate(notebook["cells"]):
            if cell["cell_type"] == "code":
                exec(  # noqa: S102
                    compile(_source(cell), f"{NOTEBOOK.name}:cell-{index}", "exec"),
                    namespace,
                )
    return namespace


@pytest.fixture
def dry_namespace(tmp_path):
    namespace = _execute_dry_run(tmp_path)
    assert list(tmp_path.iterdir()) == []
    return namespace


def _parameter_namespace(tmp_path, overrides):
    config = tmp_path / "parameters.json"
    config.write_text(json.dumps(overrides))
    cell = next(
        cell for cell in _notebook()["cells"] if cell.get("id") == "rl-adapter-02"
    )
    namespace = {"__name__": "__main__"}
    with mock.patch.dict(
        os.environ,
        {"HOME": str(tmp_path), "TRAIN_RL_ADAPTER_CONFIG": str(config)},
    ):
        exec(compile(_source(cell), "parameter-cell", "exec"), namespace)  # noqa: S102
    return namespace


def test_parameter_root_overrides_rebase_derived_paths(tmp_path):
    work = tmp_path / "work"
    ns = _parameter_namespace(
        tmp_path,
        {"WORK_DIR": str(work)},
    )
    nemo = work / "nemo-rl"
    assert ns["NEMO_RL_DIR"] == nemo
    assert ns["GYM_DIR"] == nemo / "3rdparty/Gym-workspace/Gym"
    assert ns["NEMO_PYTHON"] == nemo / ".venv/bin/python"
    assert ns["DATA_DIR"] == work / "data"
    assert ns["LOG_DIR"] == work / "logs"
    assert ns["MEGATRON_CHECKPOINT_ROOT"] == work / "megatron-base-cache"
    assert ns["MERGED_HF_DIR"] == work / "merged-hf-champion"

    explicit_gym = tmp_path / "explicit-gym"
    explicit_nemo = tmp_path / "explicit-nemo"
    other = _parameter_namespace(
        tmp_path,
        {
            "WORK_DIR": str(tmp_path / "other-work"),
            "NEMO_RL_DIR": str(explicit_nemo),
            "GYM_DIR": str(explicit_gym),
        },
    )
    assert other["GYM_DIR"] == explicit_gym
    assert other["NEMO_RL_DIR"] == explicit_nemo
    assert other["RESOURCE_DIR_NAME"] != ns["RESOURCE_DIR_NAME"]


def test_notebook_format_is_clean_and_compiles():
    notebook = _notebook()
    assert notebook["nbformat"] == 4 and notebook["nbformat_minor"] >= 5
    assert notebook["metadata"]["language_info"]["version"] == "3.12"
    ids = [cell.get("id") for cell in notebook["cells"]]
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", cell_id or "") for cell_id in ids)
    assert len(ids) == len(set(ids))
    for index, cell in enumerate(notebook["cells"]):
        assert isinstance(cell["source"], list)
        assert cell["source"] == _source(cell).splitlines(keepends=True)
        assert max(map(len, cell["source"]), default=0) < 1000
        if cell["cell_type"] == "code":
            assert cell.get("execution_count") is None
            assert cell.get("outputs") == []
            compile(_source(cell), f"{NOTEBOOK.name}:cell-{index}", "exec")

    notice = ATTRIBUTION.read_text(encoding="utf-8")
    for component in (
        "nemo-rl (0.6.0)",
        "nemo-gym (1a4912e231bb2795b062f7de97496caaf382c7f6)",
        "nemo-automodel (92635e74f4fb16784268b9a9fd7b7d6a83fff6c5)",
        "megatron-bridge (95e5f38f8727c4ab30830559c68939f35f4e52f6)",
        "megatron-core (d30c3ae5469fe3f6a64d4fd2e63b6e7f7844ea81)",
        "vllm (0.17.1)",
        "ray (2.54.0)",
        "openai (2.6.1)",
        "fastapi (0.124.4)",
        "httpx (0.28.1)",
        "pydantic (2.12.4)",
    ):
        assert f"## {component}\n**License:**" in notice


def test_default_dry_run_and_measurement_gates(dry_namespace, monkeypatch, tmp_path):
    ns = dry_namespace
    assert ns["DRY_RUN"] is True
    for gate in (
        "ALLOW_SETUP_WRITES",
        "ALLOW_GPU_LAUNCH",
        "ALLOW_OWN_ORPHAN_SWEEP",
        "ALLOW_VSS_RESTART",
        "RUN_BASELINE",
        "RUN_TRAINING",
        "RUN_MODEL_CONVERSION",
        "RUN_MODEL_SERVER",
        "RUN_VSS_ROUTE",
    ):
        assert ns[gate] is False
    assert ns["JUDGE_INFRA_ZERO_LIMIT"] == 0.02
    assert ns["VSS_BASE_URL"] == "http://127.0.0.1:7777"
    assert ns["NEMO_RL_DIR"] == ns["WORK_DIR"] / "nemo-rl"
    assert re.fullmatch(r"lvs_aggregate_[0-9a-f]{12}", ns["RESOURCE_DIR_NAME"])
    assert ns["VLLM_CONTAINER_NAME"].startswith("vss-rl-")
    assert ns["VLLM_HOST_BIND"] == "127.0.0.1"
    assert ns["NEMO_RL_COMMIT"] == "5fb588932bf835506a8a5bac01de4f8c7ab0a065"
    assert ns["NEMO_RL_UV_LOCK_SHA256"] == (
        "7b1d1d41cc1945c4fec6ff7285d2e6a633b727f98a9cc97241b7bebb11387bec"
    )
    assert len(ns["NEMO_RL_SUBMODULES"]) == 5
    assert (
        ns["NEMO_RL_SUBMODULES"][
            "3rdparty/Megatron-Bridge-workspace/Megatron-Bridge/3rdparty/Megatron-LM"
        ]
        == "d30c3ae5469fe3f6a64d4fd2e63b6e7f7844ea81"
    )
    with pytest.raises(KeyError, match="Runtime pins cannot be overridden"):
        ns["apply_local_overrides"]({"NEMO_RL_COMMIT": "mutable"})
    assert ns["RESOURCE_REQUIREMENTS"].splitlines() == [
        "-e nemo-gym @ ../../",
        "fastapi==0.124.4",
        "httpx==0.28.1",
        "pydantic==2.12.4",
    ]
    assert ns["MAX_TOTAL_SEQUENCE_LENGTH"] == 14336
    assert (ns["PROMPTS_PER_STEP"], ns["GENERATIONS_PER_PROMPT"]) == (8, 16)
    assert (ns["LORA_DIM"], ns["LORA_ALPHA"]) == (64, 128)
    assert ns["VALIDATION_PERIOD"] == ns["SAVE_PERIOD"] == 5
    assert ns["expected_validation_steps"](30, True) == [0, 5, 10, 15, 20, 25, 30]
    assert ns["hydra_inline_mapping"]({"num_nextn_predict_layers": 0}) == (
        "{num_nextn_predict_layers:0}"
    )
    with pytest.raises(TypeError, match="Unsupported Hydra mapping value"):
        ns["hydra_inline_mapping"]({"nested": {"value": 1}})
    assert ns["validated_vllm_host_bind"]("127.0.0.1", route_back=False) == (
        "127.0.0.1"
    )
    assert ns["validated_vllm_host_bind"]("::1", route_back=False) == "[::1]"
    assert ns["validated_vllm_host_bind"]("0.0.0.0", route_back=True) == "0.0.0.0"
    with pytest.raises(ValueError, match="non-loopback"):
        ns["validated_vllm_host_bind"]("127.0.0.1", route_back=True)
    with pytest.raises(ValueError, match="IPv4 or IPv6"):
        ns["validated_vllm_host_bind"]("localhost", route_back=False)

    original_run_checked = ns["run_checked"]
    ns["run_checked"] = lambda *unused, **unused_kwargs: types.SimpleNamespace(
        stdout="z-package==2\na-package==1\n"
    )
    assert ns["resolved_resource_environment"](Path("resource-python")) == (
        "a-package==1\nz-package==2\n"
    )
    ns["run_checked"] = original_run_checked

    command = ns["grpo_command"](
        checkpoint_dir=Path("checkpoint"),
        nemo_log_dir=Path("log"),
        max_steps=30,
        training=True,
    )
    required = {
        "++policy.megatron_cfg.peft.lora_B_init_method=zero",
        "++policy.hf_config_overrides={num_nextn_predict_layers:0}",
        "++grpo.val_at_start=true",
        "++grpo.val_at_end=true",
        "++env.nemo_gym.skip_venv_if_present=true",
        "++policy.tokenizer.chat_template_kwargs={enable_thinking: false}",
        "++policy.generation.vllm_cfg.http_server_serving_chat_kwargs.reasoning_parser=null",
    }
    assert required.issubset(command)
    assert f"++env.nemo_gym.uv_venv_dir={ns['GYM_DIR']}" in command
    assert not any("max_val_samples" in argument for argument in command)
    source = "\n".join(_source(cell) for cell in _notebook()["cells"])
    assert "uses_reasoning_parser" in source and "JUDGE_INFRA_ZERO" in source
    assert "VSS_RL_RUN_MARKER" in source and "getpass.getuser()" in source
    assert 'if "RAY_RUN_TOKEN" not in globals():' in source
    assert '"http.https://github.com/.extraheader="' in source
    assert "ENABLE_FORCE_VAL_AT_START_PATCH" not in source
    assert '"max_tokens": max_tokens' in ns["COVERAGE_JUDGE_SOURCE"]
    assert "judge_max_tokens: int = 8000" in ns["RESOURCE_APP"]
    assert f"judge_max_tokens: {ns['JUDGE_MAX_TOKENS']}" in ns["RESOURCE_CONFIG"]
    serving_source = next(
        _source(cell)
        for cell in _notebook()["cells"]
        if cell.get("id") == "rl-adapter-35"
    )
    assert "com.nvidia.vss-rl.run={RAY_RUN_MARKER}" in serving_source
    assert '"--gpus", \'"device=\' +' in serving_source
    assert (
        '"-p", f"{publish_host}:{VLLM_HOST_PORT}:{VLLM_CONTAINER_PORT}"'
        in serving_source
    )
    assert "container_id = result.stdout.strip()" in serving_source
    assert serving_source.index(
        "check_compute_and_disk(SERVING_GPU_IDS)"
    ) < serving_source.index("run_checked(serve_command, mutates=True, gpu=True)")
    assert 'conversion_env["CUDA_VISIBLE_DEVICES"]' in serving_source
    assert serving_source.index("check_compute_and_disk(CONVERSION_GPU_IDS)") < (
        serving_source.index("run_logged_conversion(convert_command")
    )
    resource_source = next(
        _source(cell)
        for cell in _notebook()["cells"]
        if cell.get("id") == "rl-adapter-21"
    )
    assert resource_source.index(
        "require_no_notebook_runtime()"
    ) < resource_source.index('UV, "venv", "--seed", "--clear"')
    setup_source = next(
        _source(cell)
        for cell in _notebook()["cells"]
        if cell.get("id") == "rl-adapter-05"
    )
    clone_marker = '"git", "-c", "http.https://github.com/.extraheader="'
    assert setup_source.index("Disk preflight failed before NeMo RL setup") < (
        setup_source.index(clone_marker)
    )
    run_token, run_marker = ns["RAY_RUN_TOKEN"], ns["RAY_RUN_MARKER"]
    parameter_cell = next(
        cell for cell in _notebook()["cells"] if cell.get("id") == "rl-adapter-02"
    )
    with mock.patch.dict(
        os.environ,
        {"HOME": str(tmp_path), "TRAIN_RL_ADAPTER_CONFIG": ""},
    ):
        exec(compile(_source(parameter_cell), "parameter-cell-rerun", "exec"), ns)  # noqa: S102
    assert (ns["RAY_RUN_TOKEN"], ns["RAY_RUN_MARKER"]) == (run_token, run_marker)
    original_work_dir = ns["WORK_DIR"]
    original_instrument = ns["INSTRUMENT_NAME"]
    original_derived = {
        name: ns[name]
        for name in (
            "NEMO_RL_DIR",
            "GYM_DIR",
            "NEMO_PYTHON",
            "DATA_DIR",
            "LOG_DIR",
            "MEGATRON_CHECKPOINT_ROOT",
            "MERGED_HF_DIR",
        )
    }
    ns["DRY_RUN"] = False
    changed_config = tmp_path / "changed-run-identity.json"
    for changed_name, changed_value in (
        ("INSTRUMENT_NAME", "changed-instrument"),
        ("WORK_DIR", str(tmp_path / "changed-work-dir")),
    ):
        changed_config.write_text(json.dumps({changed_name: changed_value}))
        with (
            mock.patch.dict(
                os.environ,
                {"HOME": str(tmp_path), "TRAIN_RL_ADAPTER_CONFIG": str(changed_config)},
            ),
            pytest.raises(RuntimeError, match="fixed for this kernel"),
        ):
            exec(  # noqa: S102
                compile(
                    _source(parameter_cell), "parameter-cell-changed-identity", "exec"
                ),
                ns,
            )
        assert ns["WORK_DIR"] == original_work_dir
        assert ns["INSTRUMENT_NAME"] == original_instrument
        assert ns["DRY_RUN"] is False
        assert (ns["RAY_RUN_TOKEN"], ns["RAY_RUN_MARKER"]) == (run_token, run_marker)
        assert {name: ns[name] for name in original_derived} == original_derived
    active_row = {
        "pid": 122,
        "ppid": 1,
        "user": ns["getpass"].getuser(),
        "sid": 122,
        "args": "raylet",
    }
    ns["runtime_process_snapshot"] = lambda: [active_row]
    ns["process_environment"] = lambda unused: {"VSS_RL_RUN_MARKER": run_marker}
    assert ns["notebook_runtime_processes"]() == [active_row]
    ns["RAY_TEMP_DIR"].mkdir(parents=True)
    ns["RAY_OWNERSHIP_FILE"].write_text(
        json.dumps(
            {
                "marker": run_marker,
                "owner": ns["getpass"].getuser(),
                "ray_temp_dir": str(ns["RAY_TEMP_DIR"].resolve()),
                "owner_pid": os.getpid(),
                "owner_start_time": ns["process_start_time"](os.getpid()),
            }
        )
    )
    assert ns["verified_runtime_ownership"]() == (
        ns["RAY_TEMP_DIR"].resolve(),
        run_marker,
    )
    ns["DRY_RUN"] = True
    assert ns["notebook_runtime_processes"]() == [active_row]
    assert '"requirements.txt": RESOURCE_REQUIREMENTS' in source
    assert (
        "resolved_resource_environment(resource_python) "
        "!= RESOURCE_ENVIRONMENT_FREEZE.read_text()" in source
    )
    assert (
        "def verify_deployed_instrument(protocol):\n"
        "    verify_nemo_checkout(require_initialized=True)" in source
    )
    assert (
        "if verify_deployment:\n        verify_deployed_instrument(protocol)" in source
    )
    assert (
        "def secured_champion_paths():\n"
        "    verify_nemo_checkout(require_initialized=True)" in source
    )
    assert '"resource_environment": RESOURCE_ENVIRONMENT_FREEZE' in source
    training_source = next(
        _source(cell)
        for cell in _notebook()["cells"]
        if cell.get("id") == "rl-adapter-27"
    )
    assert training_source.index("baseline_ready()") < training_source.index(
        "check_cuda_stack()"
    )
    assert (
        "parser_check =" not in training_source
        and "import_check =" not in training_source
    )
    assert (
        "pkill" not in source
        and "ray stop" not in source
        and "docker compose down" not in source
    )

    for relative, embedded in ns["RESOURCE_FILES"].items():
        if relative.endswith(".py"):
            compile(embedded, relative, "exec")
    names = {"editable_check", "patch_yaml", "parser_check", "import_check", "script"}
    compiled = []
    for cell_index, cell in enumerate(_notebook()["cells"]):
        if cell["cell_type"] != "code":
            continue
        for node in ast.walk(ast.parse(_source(cell))):
            if not isinstance(node, ast.Assign) or not isinstance(
                node.value, ast.Constant
            ):
                continue
            targets = [
                target.id for target in node.targets if isinstance(target, ast.Name)
            ]
            if targets and targets[0] in names and isinstance(node.value.value, str):
                compile(node.value.value, f"cell-{cell_index}:{targets[0]}", "exec")
                compiled.append((cell_index, targets[0]))
    assert {name for _cell, name in compiled} == names

    row = {"pid": 123, "ppid": 1, "user": "operator", "sid": 123, "args": "raylet"}
    ns["DRY_RUN"] = False
    ns["runtime_process_snapshot"] = lambda: [row]
    ns["process_environment"] = lambda unused: {"RAY_TMPDIR": str(ns["RAY_TEMP_DIR"])}
    monkeypatch.setattr(ns["getpass"], "getuser", lambda: row["user"])
    assert ns["notebook_runtime_processes"]() == []
    ns["process_environment"] = lambda unused: {
        "VSS_RL_RUN_MARKER": ns["RAY_RUN_MARKER"]
    }
    assert ns["notebook_runtime_processes"]() == [row]
    other_run_marker = ns["RAY_RUN_MARKER"].rsplit(":", 1)[0] + ":" + "f" * 32
    ns["process_environment"] = lambda unused: {"VSS_RL_RUN_MARKER": other_run_marker}
    assert ns["notebook_runtime_processes"]() == []

    ns["WORK_DIR"] = tmp_path
    ns["RAY_TEMP_DIR"] = tmp_path / "ray"
    ns["RAY_OWNERSHIP_FILE"] = ns["RAY_TEMP_DIR"] / ".vss-rl-owner.json"
    ns["RAY_RUN_MARKER"] = f"{ns['INSTRUMENT_NAME']}:{tmp_path.resolve()}:" + "b" * 32
    old_run_marker = f"{ns['INSTRUMENT_NAME']}:{tmp_path.resolve()}:" + "a" * 32
    ns["RAY_TEMP_DIR"].mkdir()
    ns["ALLOW_OWN_ORPHAN_SWEEP"] = True
    signals = []
    monkeypatch.setattr(os, "pidfd_open", lambda unused: pytest.fail("opened pidfd"))
    monkeypatch.setattr(
        ns["signal"], "pidfd_send_signal", lambda *args: signals.append(args)
    )
    with pytest.raises(RuntimeError, match="regular notebook ownership marker"):
        ns["cleanup_notebook_runtime"]()
    assert signals == []

    old_ownership = {
        "marker": old_run_marker,
        "owner": row["user"],
        "ray_temp_dir": str(ns["RAY_TEMP_DIR"].resolve()),
        "owner_pid": 9000,
        "owner_start_time": "17",
    }
    ns["RAY_OWNERSHIP_FILE"].write_text(json.dumps(old_ownership))
    ns["process_start_time"] = lambda pid: "17" if pid == 9000 else None
    with pytest.raises(RuntimeError, match="still alive"):
        ns["verified_runtime_ownership"]()

    ns["process_start_time"] = lambda unused: None
    for invalid in (
        old_ownership | {"extra": True},
        old_ownership | {"owner": "another-user"},
        old_ownership | {"marker": "not-a-run-marker"},
    ):
        ns["RAY_OWNERSHIP_FILE"].write_text(json.dumps(invalid))
        with pytest.raises(RuntimeError, match="ownership is not proven"):
            ns["verified_runtime_ownership"]()
    ns["RAY_OWNERSHIP_FILE"].write_text(json.dumps(old_ownership))
    assert ns["verified_runtime_ownership"]() == (
        ns["RAY_TEMP_DIR"].resolve(),
        old_run_marker,
    )
    closed = []
    real_os_close = os.close

    def record_fake_close(fd):
        if fd in {99, 1124}:
            closed.append(fd)
        else:
            real_os_close(fd)

    runtime_processes = ns["notebook_runtime_processes"]
    ns["notebook_runtime_processes"] = lambda unused_marker=None: [row]
    ns["process_environment"] = lambda unused: {
        "VSS_RL_RUN_MARKER": ns["RAY_RUN_MARKER"]
    }
    ns["pidfd_has_exited"] = lambda unused: False
    monkeypatch.setattr(os, "pidfd_open", lambda unused: 99)
    monkeypatch.setattr(os, "close", record_fake_close)
    with pytest.raises(RuntimeError, match="ownership changed before signaling"):
        ns["cleanup_notebook_runtime"]()
    assert closed == [99]
    assert signals == []

    owned_row = row | {"pid": 124}
    concurrent_row = row | {"pid": 125}
    ns["runtime_process_snapshot"] = lambda: [owned_row, concurrent_row]
    ns["process_environment"] = lambda pid: {
        "VSS_RL_RUN_MARKER": old_run_marker if pid == 124 else ns["RAY_RUN_MARKER"]
    }
    assert runtime_processes(old_run_marker) == [owned_row]
    ns["notebook_runtime_processes"] = runtime_processes
    monkeypatch.setattr(os, "pidfd_open", lambda pid: pid + 1000)
    replacement_ownership = {
        "marker": ns["RAY_RUN_MARKER"],
        "owner": row["user"],
        "ray_temp_dir": str(ns["RAY_TEMP_DIR"].resolve()),
        "owner_pid": 9100,
        "owner_start_time": "18",
    }

    def replace_ownership_while_waiting(unused, unused_timeout):
        ns["RAY_OWNERSHIP_FILE"].write_text(json.dumps(replacement_ownership))
        return set()

    ns["wait_for_pidfds"] = replace_ownership_while_waiting
    closed.clear()
    with pytest.raises(RuntimeError, match="marker changed during cleanup"):
        ns["cleanup_notebook_runtime"]()
    assert ns["RAY_TEMP_DIR"].is_dir()
    assert closed == [1124]

    ns["RAY_OWNERSHIP_FILE"].write_text(json.dumps(old_ownership))
    ns["wait_for_pidfds"] = lambda unused, unused_timeout: set()
    cleanup_dir = tmp_path / (".ray-cleanup-" + "a" * 32)
    real_rmtree = ns["shutil"].rmtree

    def remove_moved_runtime(path):
        assert Path(path) == cleanup_dir
        ns["RAY_TEMP_DIR"].mkdir()
        ns["RAY_OWNERSHIP_FILE"].write_text(json.dumps(replacement_ownership))
        real_rmtree(path)

    monkeypatch.setattr(ns["shutil"], "rmtree", remove_moved_runtime)
    closed.clear()
    signals.clear()
    ns["cleanup_notebook_runtime"]()
    assert closed == [1124]
    assert signals == [(1124, ns["signal"].SIGTERM)]
    assert not cleanup_dir.exists()
    assert json.loads(ns["RAY_OWNERSHIP_FILE"].read_text()) == replacement_ownership


def test_resource_install_and_prepared_data_are_run_scoped(dry_namespace, tmp_path):
    ns = dry_namespace
    ns["GYM_DIR"] = tmp_path / "gym"
    ns["RESOURCE_DIR_NAME"] = "lvs_aggregate_0123456789ab"
    root = ns["GYM_DIR"] / "resources_servers" / ns["RESOURCE_DIR_NAME"]
    train = tmp_path / "train.jsonl"
    validation = tmp_path / "validation.jsonl"
    train.write_text('{"agent_ref":"a","text":"caf\\u00e9"}\n')
    validation.write_text('{"agent_ref":"a","text":"held-out"}\n')
    ns["TRAIN_FILE"], ns["K5_FILE"] = train, validation
    ns["RESOURCE_FILES"] = {"app.py": "value = 1\n"}
    ns["RESOURCE_DATA_FILES"] = {
        "data/train.jsonl": train,
        "data/validation.jsonl": validation,
    }

    ns["install_resource_tree"](root)
    sentinel = root / "operator-note.txt"
    sentinel.write_text("keep\n")
    ns["install_resource_tree"](root)
    assert sentinel.read_text() == "keep\n"
    (root / "app.py").write_text("value = 2\n")
    with pytest.raises(RuntimeError, match="differs from the frozen source"):
        ns["install_resource_tree"](root)
    assert sentinel.read_text() == "keep\n"

    ns["PREPARED_DATA_DIR"] = tmp_path / "prepared"
    ns["PREPARED_DATA_DIR"].mkdir()
    (ns["PREPARED_DATA_DIR"] / "train.jsonl").write_text(
        '{ "agent_ref": "a", "text": "café" }\n'
    )
    (ns["PREPARED_DATA_DIR"] / "validation.jsonl").write_text(
        '{"agent_ref":"a","text":"held-out"}\n'
    )
    ns["verify_prepared_data"]()
    (ns["PREPARED_DATA_DIR"] / "validation.jsonl").write_text("")
    with pytest.raises(RuntimeError, match="differs from frozen rows"):
        ns["verify_prepared_data"]()


class _Model:
    def __init__(self, **values):
        self.__dict__.update(values)

    def model_dump(self, exclude=None):
        return {
            name: value
            for name, value in vars(self).items()
            if name not in (exclude or ())
        }


def _resource_modules(monkeypatch, ns):
    coverage = types.ModuleType("coverage_judge")
    coverage.__file__ = "coverage_judge.py"
    exec(  # noqa: S102
        compile(ns["COVERAGE_JUDGE_SOURCE"], coverage.__file__, "exec"),
        coverage.__dict__,
    )

    class AsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *unused):
            return False

    fastapi = types.ModuleType("fastapi")
    fastapi.FastAPI = fastapi.Request = _Model
    httpx = types.ModuleType("httpx")
    httpx.AsyncClient = AsyncClient
    base = types.ModuleType("nemo_gym.base_resources_server")
    for name in (
        "BaseResourcesServerConfig",
        "BaseSeedSessionRequest",
        "BaseSeedSessionResponse",
        "BaseVerifyRequest",
        "BaseVerifyResponse",
        "SimpleResourcesServer",
    ):
        setattr(base, name, _Model)
    nemo_gym = types.ModuleType("nemo_gym")
    nemo_gym.base_resources_server = base
    for name, module in {
        "coverage_judge": coverage,
        "fastapi": fastapi,
        "httpx": httpx,
        "nemo_gym": nemo_gym,
        "nemo_gym.base_resources_server": base,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    app = types.ModuleType("lvs_aggregate_app")
    app.__file__ = "app.py"
    exec(compile(ns["RESOURCE_APP"], app.__file__, "exec"), app.__dict__)  # noqa: S102
    return coverage, app


def test_embedded_reward_and_resource_contract(monkeypatch, dry_namespace, tmp_path):
    grading_log = tmp_path / "grading-samples.jsonl"
    monkeypatch.setenv("LVS_GRADING_LOG", str(grading_log))
    monkeypatch.setenv("LVS_SAMPLE_RATE", "0")
    coverage, app = _resource_modules(monkeypatch, dry_namespace)
    assert coverage.caption_copy_fraction("x" * 40, "x" * 40) == 1.0
    checklist = [{"id": f"c{i}", "fact": f"fact {i}"} for i in range(1, 5)]
    grade = coverage._parse_reply(
        'prefix {"covered":{"c1":true,"c2":true,"c3":true,"c4":false},'
        '"fabrications":["invented"]} suffix',
        checklist,
    )
    assert grade["coverage"] == 0.75
    assert coverage.reward_from_grade(grade, 0.05) == 0.5
    with pytest.raises(ValueError, match="omitted"):
        coverage._parse_reply('{"covered":{"c1":true}}', checklist)
    with pytest.raises(ValueError, match="non-boolean"):
        coverage._parse_reply(
            '{"covered":{"c1":1,"c2":true,"c3":true,"c4":true}}',
            checklist,
        )

    server = app.LVSAggregateResourcesServer()
    server.config = app.LVSAggregateConfig()
    captions = "0123456789" * 40
    validation = app.LVSAggregateVerifyRequest(
        response=types.SimpleNamespace(
            output=[{"type": "output_text", "text": captions}]
        ),
        checklist=checklist,
        captions=captions,
        video="v",
        window="w",
    )
    copied = asyncio.run(server.verify(validation))
    assert copied.reward == 0.0 and copied.verifier_ok is True
    assert copied.verifier_status.startswith("copy_detected")

    coverage.caption_copy_fraction = lambda *unused: 0.35
    result = [
        {
            "verifier_ok": True,
            "status": "graded",
            "coverage": 1.0,
            "covered_n": 4,
            "n_items": 4,
            "per_item": {},
            "fabrications": [],
        }
    ]

    async def grade_async(*unused, **unused_kwargs):
        return result[0]

    coverage.grade_async = grade_async
    app._random.random = lambda: 1.0
    training = app.LVSAggregateVerifyRequest(
        response=types.SimpleNamespace(
            output=[{"type": "output_text", "text": "summary"}]
        ),
        checklist=checklist,
        captions=captions,
        video="v",
        window="w",
        graded_copy="true",
    )
    assert asyncio.run(server.verify(training)).reward == pytest.approx(0.5)
    result[0].update(
        verifier_ok=False, status="judge failure", coverage=0.0, covered_n=0
    )
    failed = asyncio.run(server.verify(training))
    assert failed.reward == 0.0 and failed.verifier_ok is False
    assert grading_log.is_file()


def _write_run(ns, log_path, nemo_dir, score=0.4):
    evidence = nemo_dir / "exp_001/val_data_step0.jsonl"
    evidence.parent.mkdir(parents=True)
    evidence.write_text(
        "".join(json.dumps({"rewards": [score]}) + "\n" for _ in range(25))
    )
    point = {
        "measured_at_pt": "2026-09-02T12:00:00-07:00",
        "instrument": ns["INSTRUMENT_NAME"],
        "step": 0,
        "score": score,
        "samples": 25,
        "evidence_path": str(evidence),
    }
    log_path.write_text(
        "VALIDATION_RECORD=" + json.dumps(point, sort_keys=True) + "\n"
        "PROGRESS_TOTAL_STEP=1\nRUN_EXIT=0 RUN_ENDED_AT_PT=2026-09-02T12:01:00-07:00\n"
    )
    return evidence


def test_validation_evidence_and_baseline_recovery(dry_namespace, tmp_path):
    ns = dry_namespace
    ns.update(
        {"DRY_RUN": False, "ALLOW_SETUP_WRITES": True, "INSTRUMENT_NAME": "contract-k5"}
    )
    for name, filename in {
        "TRAIN_FILE": "train.jsonl",
        "K5_FILE": "validation-k5.jsonl",
        "SPLIT_FILE": "split.json",
        "BASELINE_PROTOCOL": "protocol.json",
        "FROZEN_REFERENCE": "frozen.json",
        "MEASUREMENT_LEDGER": "measurements.csv",
        "BASELINE_LOG": "baseline.log",
        "BASELINE_NEMO_LOG_DIR": "baseline-nemo",
        "RESOURCE_ENVIRONMENT_FREEZE": "resource-environment.freeze.txt",
    }.items():
        ns[name] = tmp_path / filename
    ns["TRAIN_FILE"].write_text('{"row_id":"train"}\n')
    ns["K5_FILE"].write_text(
        "".join(
            json.dumps({"row_id": f"row-{index}"}) + "\n"
            for index in range(5)
            for _ in range(5)
        )
    )
    ns["SPLIT_FILE"].write_text('{"validation_source_ids":["held-out"]}\n')
    ns["RESOURCE_ENVIRONMENT_FREEZE"].write_text("package==1\n")
    evidence = _write_run(ns, ns["BASELINE_LOG"], ns["BASELINE_NEMO_LOG_DIR"])
    assert (
        ns["verify_validation_run"](
            ns["BASELINE_LOG"],
            ns["BASELINE_NEMO_LOG_DIR"],
            [0],
            1,
        )[0]["score"]
        == 0.4
    )

    evidence_text = evidence.read_text()
    evidence.write_text(evidence_text.replace("0.4", "0.8", 1))
    with pytest.raises(RuntimeError, match="does not match logged score"):
        ns["verify_validation_run"](
            ns["BASELINE_LOG"], ns["BASELINE_NEMO_LOG_DIR"], [0], 1
        )
    evidence.write_text(evidence_text)
    log_text = ns["BASELINE_LOG"].read_text()
    ns["BASELINE_LOG"].write_text(log_text.replace("PROGRESS_TOTAL_STEP=1\n", ""))
    with pytest.raises(RuntimeError, match="progress is incomplete"):
        ns["verify_validation_run"](
            ns["BASELINE_LOG"], ns["BASELINE_NEMO_LOG_DIR"], [0], 1
        )
    ns["BASELINE_LOG"].write_text(log_text)

    protocol = {
        "instrument": ns["INSTRUMENT_NAME"],
        "prediction": 0.3,
        "success_bar": 0.6,
        "train_sha256": ns["sha256_file"](ns["TRAIN_FILE"]),
        "validation_sha256": ns["sha256_file"](ns["K5_FILE"]),
        "split_sha256": ns["sha256_file"](ns["SPLIT_FILE"]),
        "model_path_sha256": "model-hash",
        "instrument_spec": ns["instrument_spec"](),
        "instrument_spec_sha256": ns["instrument_spec_sha256"](),
    }
    ns["BASELINE_PROTOCOL"].write_text(json.dumps(protocol) + "\n")
    ns["verify_deployed_instrument"] = lambda unused: None
    ns["megatron_base_identity"] = lambda: {
        "path": "/base/iter_0000000",
        "sha256": "base-hash",
    }
    first = ns["finalize_baseline_from_verified_log"]()
    assert ns["finalize_baseline_from_verified_log"]() == first
    assert first["baseline"] == 0.4
    assert len(ns["MEASUREMENT_LEDGER"].read_text().splitlines()) == 2
    evidence.write_text(evidence_text + '{"rewards":[0.4]}\n')
    with pytest.raises(RuntimeError, match="per-sample validation evidence"):
        ns["baseline_ready"](verify_deployment=False)
