#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Service adapter around the unchanged ci-vss-oss alerts latency runner."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

import yaml

PERF_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PERF_ROOT.parents[2]
sys.path.insert(0, str(REPO_ROOT / "libs/vss-perf-reporting/src"))
sys.path.insert(0, str(REPO_ROOT / "libs/vss-perf-tools"))
from support_images import load as load_images, environment as image_environment, save_snapshot  # noqa: E402

from vss_perf_reporting import build_and_save, discover_platform  # noqa: E402
from alert_results_adapter import to_test_cases  # noqa: E402

SERVICE = "Alerts-Verification"
# Both the native runner and report collector execute on the Compose host.
LOCAL_EVAL_HOST = "127.0.0.1"
REQUIRED_DEPLOYMENT_ENV_TEMPLATES = (
    "developer-profiles/dev-profile-alerts/.env",
    "services/nvstreamer/.env",
)


def run(argv, **kwargs):
    return subprocess.run([str(arg) for arg in argv], check=True, **kwargs)


def write_docker_shim(path, docker, image_variables):
    """Forward only the selected image variables through passwordless sudo."""
    names = ",".join(sorted(image_variables))
    path.write_text(
        "#!/usr/bin/env bash\nexec sudo -n --preserve-env=" + names + " "
        + shlex.quote(docker) + ' "$@"\n'
    )
    path.chmod(0o700)


def hardware_profile():
    """Native profile names describe detected GPUs, never select a Jenkins node."""
    name = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], text=True
    ).splitlines()[0]
    for pattern, profile in (
        ("RTX PRO 6000", "RTXPRO6000BW"),
        ("RTX PRO 4500", "RTXPRO4500BW"),
        ("H100", "H100"),
        ("GB300", "GB300"),
        ("B300", "GB300"),
        ("L40S", "L40S"),
        ("GB10", "DGX-SPARK"),
        ("Thor", "AGX-THOR"),
    ):
        if pattern in name:
            return profile
    raise ValueError(f"No native alerts hardware profile for detected GPU: {name}")


def agent_host_port(config):
    port = config.get("deployment", {}).get("agent_host_port", 8001)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("deployment.agent_host_port must be an integer from 1 to 65535")
    return port


def ensure_agent_port_available(port, env):
    # Docker can publish ports through NAT without a listening host process.
    published = subprocess.check_output(
        ["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"], text=True, env=env
    )
    for line in published.splitlines():
        if re.search(rf":{port}->", line):
            owner = line.split("\t", 1)[0]
            raise ValueError(f"Alerts agent host port {port} is already published by {owner}")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("0.0.0.0", port))
    except OSError as error:
        raise ValueError(f"Cannot bind Alerts agent host port {port}: {error}") from error


def scenario_settings(config_path, scenario):
    config = yaml.safe_load(config_path.read_text())
    selected = config["test_scenarios"][scenario]
    if selected["benchmark_mode"] not in ("synthetic_kafka", "e2e_kafka"):
        raise ValueError(
            "Only synthetic_kafka and e2e_kafka latency modes are supported"
        )
    global_config = config["global"]
    agent_host_port(global_config)
    dataset = selected.get("dataset", global_config["dataset"])
    workload = {**global_config.get("workload", {}), **selected.get("workload", {})}
    for key in (
        "num_sensors",
        "workers",
        "synthetic_alerts_per_minute",
        "synthetic_event_duration_ms",
    ):
        if float(workload[key]) <= 0:
            raise ValueError(f"{key} must be positive")
    if not re.fullmatch(r"[1-9][0-9]*[smhd]", str(workload["latency_duration"])):
        raise ValueError("latency_duration must be a positive duration such as 5m")
    for key in ("videos_subpath", "ground_truth_subpath"):
        relative = Path(dataset[key])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"{key} must be dataset-relative")
    source = workload["latency_source_video"]
    if Path(source).name != source:
        raise ValueError("latency_source_video must be a filename")
    return global_config, selected, dataset, workload


def prepare_dataset(dataset, env):
    """Use the same private package index, credentials and CLI as run_eval.sh."""
    cache = Path(env.get("PERF_DATASET_ROOT", str(PERF_ROOT / "setup/.cache/datasets")))
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / dataset["name"]
    required = (
        destination / dataset["videos_subpath"],
        destination / dataset["ground_truth_subpath"],
    )
    if not all(path.exists() for path in required):
        if destination.exists():
            raise ValueError(
                f"Incomplete cached dataset at {destination}; repair or use PERF_DATASET_ROOT"
            )
        helper = cache.parent / "nvdataset-venv"
        uv = env.get("PERF_UV_BIN", "uv")
        if not (helper / "bin/nvdataset").exists():
            run([uv, "venv", str(helper)])
            run(
                [
                    uv,
                    "pip",
                    "install",
                    "--python",
                    helper / "bin/python",
                    "--extra-index-url",
                    "https://artifactory.pdx.nvidia.com/artifactory/api/pypi/sw-ngc-data-platform-pypi-local/simple",
                    "nvdataset",
                ],
                env=env,
            )
        download_env = {
            **env,
            "NVDATASET_TENANTID": str(dataset["tenant_id"]),
            "NVDATASET_GROUPID": dataset["group_id"],
        }
        run(
            [helper / "bin/nvdataset", "download", dataset["name"], destination],
            env=download_env,
        )
    if not required[0].is_dir() or not required[1].is_file():
        raise ValueError("Downloaded dataset is missing videos or ground-truth JSON")
    return destination


def prepare_runtime(work, agent_port=8001):
    """Keep source code and deployment templates untouched in the checkout."""
    source = REPO_ROOT / "deploy/docker"
    environment_templates = {}
    for relative in REQUIRED_DEPLOYMENT_ENV_TEMPLATES:
        template = source / relative
        if template.is_symlink() or not template.is_file():
            raise ValueError(
                "Required deployment environment template is missing or not a regular file: " + relative
            )
        environment_templates[relative] = re.sub(
            r"(?m)^(?:export\s+)?([A-Z0-9_]*(?:KEY|TOKEN|PASSWORD|SECRET)[A-Z0-9_]*)=.*$",
            lambda match: match[1] + '="${' + match[1] + ':-}"',
            template.read_text(),
        )
    native_root = PERF_ROOT / "benchmark/native"
    shutil.copytree(native_root / "eval", work / "eval")
    shutil.copytree(PERF_ROOT / "setup/native-eval", work / "eval", dirs_exist_ok=True)
    deployment = work / "video-search-and-summarization/deploy/docker"
    shutil.copytree(
        source,
        deployment,
        ignore=shutil.ignore_patterns(
            ".env", "generated.env", "engines", "data", "__pycache__"
        ),
    )
    # Copy only required templates, with credentials supplied at runtime.
    for relative, content in environment_templates.items():
        destination = deployment / relative
        destination.write_text(content)
        destination.chmod(0o600)
    profile_env = deployment / "developer-profiles/dev-profile-alerts/.env"
    with profile_env.open("a") as stream:
        stream.write("\nSTREAM_TYPE=kafka\n")
    # dev-profile.sh copies overrides.env into generated.env, which has higher
    # Compose precedence than the profile .env. Change only this private copy.
    overrides = deployment / "developer-profiles/dev-profile-alerts/overrides.env"
    content = overrides.read_text()
    assignment = f"VSS_AGENT_HOST_PORT={agent_port}"
    pattern = r"(?m)^(?:export\s+)?VSS_AGENT_HOST_PORT=.*$"
    if re.search(pattern, content):
        content = re.sub(pattern, assignment, content)
    else:
        content += "\n" + assignment + "\n"
    overrides.write_text(content)
    # The native profile configurator still needs a private deployment copy.
    # Images are selected by Compose environment interpolation, not file rewriting.
    (work / "eval/.env").touch(mode=0o600)
    return deployment


def native_arguments(
    work, scenario, workload, dataset_root, dataset, profile, env_file
):
    args = [
        "bash",
        work / "eval/run_eval.sh",
        "--profile",
        "alerts",
        "--alerts-mode",
        "verification",
        "--eval-type",
        "latency",
        "--deployment-mode",
        "compose",
        "--host-ip",
        LOCAL_EVAL_HOST,
        "--env-file",
        env_file,
        "--hardware-profile",
        profile,
        "--blueprints-dir",
        work / "video-search-and-summarization",
        "--skip-dataset-download",
        "--dataset-path",
        dataset_root,
        "--videos-path",
        dataset_root / dataset["videos_subpath"],
        "--ground-truth-path",
        dataset_root / dataset["ground_truth_subpath"],
        "--latency-source-video",
        workload["latency_source_video"],
        "--num-sensors",
        str(workload["num_sensors"]),
        "--latency-duration",
        workload["latency_duration"],
        "--alert-category",
        workload["alert_category"],
        "--alert-bridge-num-workers",
        str(workload["workers"]),
    ]
    if scenario["benchmark_mode"] == "synthetic_kafka":
        args += [
            "--synthetic-alerts-injection",
            "--synthetic-alerts-per-minute",
            str(workload["synthetic_alerts_per_minute"]),
            "--synthetic-alert-event-duration-ms",
            str(workload["synthetic_event_duration_ms"]),
        ]
    else:
        args += ["--e2e-eval"]
    return [str(arg) for arg in args]


def collect_diagnostics(output, env, outcome="failure"):
    directory = output / "diagnostics"
    directory.mkdir(parents=True, exist_ok=True)
    collector = env.get("PERF_DIAGNOSTICS_COLLECTOR")
    if collector:
        subprocess.run(
            [
                collector,
                "--output",
                str(directory),
                "--outcome",
                outcome,
                "--service",
                SERVICE,
                "--compose-project",
                "vss",
            ],
            check=False,
            env=env,
        )
    else:
        # Inspect state only: full docker inspect contains secret environment values.
        ids = subprocess.check_output(
            ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=vss"],
            text=True,
            env=env,
        ).split()
        for cid in ids:
            with (directory / f"{cid}.log").open("w") as stream:
                subprocess.run(
                    ["docker", "logs", "--timestamps", "--tail", "5000", cid],
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    env=env,
                )
            state = subprocess.check_output(
                ["docker", "inspect", "--format", "{{json .State}}", cid],
                text=True,
                env=env,
            )
            (directory / f"{cid}.state.json").write_text(state)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=PERF_ROOT / "configs/config.yaml"
    )
    parser.add_argument("--scenario", default="quick_test")
    parser.add_argument("--config-id", default="alerts-verification-compose")
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--release", default=os.environ.get("RELEASE", ""))
    parser.add_argument("--pipeline-url", default=os.environ.get("PIPELINE_URL", ""))
    parser.add_argument(
        "--triggered-by", default=os.environ.get("TRIGGERED_BY", "manual")
    )
    for name in (
        "compose-file",
        "gpu-model",
        "vlm-gpus",
        "llm-gpus",
        "vlm-model",
        "llm-model",
        "vision-input-tokens",
    ):
        parser.add_argument("--" + name)
    args = parser.parse_args(argv)
    output = (
        args.output_json
        or Path(os.environ.get("PERF_OUTPUT_DIR", PERF_ROOT / "results"))
    ).resolve()
    output.mkdir(parents=True, exist_ok=True)
    native_output = output / "native"
    native_output.mkdir(exist_ok=True)
    started = time.monotonic()
    env = os.environ.copy()
    # Native dev-profile.sh owns these paths through generated.env. Inherited
    # exports would override that file when Compose resolves bind mounts.
    env.pop("VSS_APPS_DIR", None)
    env.pop("VSS_DATA_DIR", None)
    env.pop("VSS_AGENT_HOST_PORT", None)
    env["NGC_CLI_API_KEY"] = env.get("NGC_CLI_API_KEY") or env.get("NGC_API_KEY", "")
    env["NGC_API_KEY"] = env["NGC_CLI_API_KEY"]
    stage = "pre_flight"
    owned = False
    payload = None
    cases = []
    fatal = None
    work = None
    metadata_config = {
        "config_id": args.config_id,
        "artifact_ref": env.get("IMAGE_TAG") or env.get("ARTIFACT_REF", ""),
    }
    try:
        global_config, scenario, dataset, workload = scenario_settings(
            args.config, args.scenario
        )
        agent_port = agent_host_port(global_config)
        for value in (args.vlm_gpus, args.llm_gpus):
            if value and not re.fullmatch(r"[0-9]+", value):
                raise ValueError(
                    "Native alerts deployment accepts one device ID per model"
                )
        metadata_config.update(
            {
                "setup": scenario["benchmark_mode"],
                "workload": workload,
                "dataset": dataset,
                "agent_host_port": agent_port,
            }
        )
        image = metadata_config["artifact_ref"]
        if (
            not image
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/@:+-]*", image)
            or (":" not in image.rsplit("/", 1)[-1] and "@sha256:" not in image)
        ):
            raise ValueError(
                "IMAGE_TAG/ARTIFACT_REF must be an explicit full alerts image reference"
            )
        supporting = load_images(PERF_ROOT / global_config["supporting_images"])
        support_values = image_environment(supporting, env)
        env.update(support_values)
        env["PERF_ALERT_IMAGE"] = image
        save_snapshot(output / "support-images.json", support_values)
        metadata_config["supporting_images"] = supporting
        if not env["NGC_CLI_API_KEY"]:
            raise ValueError("NGC_API_KEY/NGC_CLI_API_KEY is required")
        for binary in ("docker", "curl", "jq", "ip", "bash", "nvidia-smi"):
            if not shutil.which(binary):
                raise ValueError(f"Required host executable is missing: {binary}")
        work = Path(tempfile.mkdtemp(prefix="alerts-perf-"))
        if subprocess.run(
            ["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ).returncode:
            docker = shutil.which("docker")
            run(["sudo", "-n", docker, "info"], stdout=subprocess.DEVNULL)
            tools = work / "bin"
            tools.mkdir()
            shim = tools / "docker"
            write_docker_shim(shim, docker, [*support_values, "PERF_ALERT_IMAGE"])
            env["PATH"] = str(tools) + os.pathsep + env["PATH"]
        run(["docker", "compose", "version"], env=env)
        ensure_agent_port_available(agent_port, env)
        existing = subprocess.check_output(
            ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=vss"],
            text=True,
            env=env,
        ).strip()
        if existing:
            raise ValueError(
                "Native alerts evaluation requires exclusive access: existing vss Compose containers detected"
            )
        profile = hardware_profile()
        dataset_root = prepare_dataset(dataset, env)
        if not (
            dataset_root / dataset["videos_subpath"] / workload["latency_source_video"]
        ).is_file():
            raise ValueError("Configured latency source video is missing")
        prepare_runtime(work, agent_port)
        # The native stack uses fixed container names as well as project=vss.
        # Reject a conflicting name even when it belongs to a different project.
        reserved_names = set()
        for root in (
            work / "eval",
            work / "video-search-and-summarization/deploy/docker",
        ):
            for path in root.rglob("*"):
                if path.suffix in (".yaml", ".yml") and path.is_file():
                    reserved_names.update(
                        re.findall(
                            r'(?m)^\s*container_name:\s*["\x27]?([A-Za-z0-9_.-]+)["\x27]?\s*$',
                            path.read_text(),
                        )
                    )
        existing_names = set(
            subprocess.check_output(
                ["docker", "ps", "-a", "--format", "{{.Names}}"], text=True, env=env
            ).splitlines()
        )
        if reserved_names & existing_names:
            raise ValueError(
                "Native container names already exist: "
                + ", ".join(sorted(reserved_names & existing_names))
            )
        env.update(
            {
                "CI_VSS_OSS_DIR": str(work),
                "BLUEPRINTS_DIR": str(work / "video-search-and-summarization"),
                "EVAL_RESULT_DIR_PATH": str(native_output),
                "EVAL_TYPE": "latency",
                "ALERTS_MODE": "verification",
                "BLUEPRINT": "alerts",
                "STREAM_TYPE": "kafka",
                "JOB_ID": work.name,
                "DOCKER_UID": str(os.getuid()),
                "DOCKER_GID": str(os.getgid()),
                "EVAL_DATASET_PATH": str(dataset_root),
                "EVAL_VIDEOS_PATH": str(dataset_root / dataset["videos_subpath"]),
                "EVAL_GROUND_TRUTH_PATH": str(
                    dataset_root / dataset["ground_truth_subpath"]
                ),
                "PROFILE_CONFIG_PYTHON": sys.executable,
                "ALERT_BRIDGE_SYNTHETIC_SUBMISSION_MODE": "message_bus",
                "ALERT_BRIDGE_EVALUATOR_PROMETHEUS_SUMMARY_BUFFER_SECONDS": str(
                    workload["summary_buffer_seconds"]
                ),
                "SYNTHETIC_E2E_MIN_COVERAGE_RATIO": str(workload["min_coverage_ratio"]),
            }
        )
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
        # Each profile gets its private .env; no credentials are persisted here.
        env_file = work / "eval/perf.env"
        values = {
            "EVAL_RESULT_DIR_PATH": str(native_output),
            "EVAL_TYPE": "latency",
            "BLUEPRINT": "alerts",
            "ALERTS_MODE": "verification",
            "STREAM_TYPE": "kafka",
        }
        env_file.write_text(
            "".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items())
        )
        stage = "benchmark"
        owned = True
        command = native_arguments(
            work, scenario, workload, dataset_root, dataset, profile, env_file
        )
        for flag, value in (
            ("--vlm-device-id", args.vlm_gpus),
            ("--llm-device-id", args.llm_gpus),
        ):
            if value:
                command += [flag, value]
        metadata_config["gpu_assignment"] = {"vlm": args.vlm_gpus, "llm": args.llm_gpus}
        child = subprocess.Popen(
            command,
            env=env,
            start_new_session=True,
        )

        def interrupt(signum, _frame):
            os.killpg(child.pid, signum)
            raise InterruptedError(f"Evaluation interrupted by signal {signum}")

        old_handlers = {
            sig: signal.signal(sig, interrupt)
            for sig in (signal.SIGINT, signal.SIGTERM)
        }
        try:
            returncode = child.wait()
        finally:
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
        if returncode == 0:
            actual_image = subprocess.check_output(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.Config.Image}}",
                    "vss-alert-bridge",
                ],
                text=True,
                env=env,
            ).strip()
            if actual_image != image:
                raise RuntimeError(
                    f"Deployed alert image differs from requested artifact: {actual_image}"
                )
            metadata_config["deployed_alert_image"] = actual_image
        report_file = native_output / "ab-eval/alert_bridge_evaluation_status.json"
        if report_file.is_file():
            payload = json.loads(report_file.read_text())
        else:
            if returncode:
                raise RuntimeError(
                    f"Native evaluation exited {returncode} without a report"
                )
            # Full-flow polling in the unchanged runner does not persist the API response.
            host = LOCAL_EVAL_HOST
            port = env.get("ALERT_BRIDGE_EVALUATOR_PORT", "5002")
            with urllib.request.urlopen(
                f"http://{host}:{port}/eval/{env['JOB_ID']}", timeout=30
            ) as response:
                payload = json.load(response)
            report_file.parent.mkdir(exist_ok=True)
            report_file.write_text(json.dumps(payload, indent=2))
        cases = to_test_cases(payload)
        if returncode:
            raise RuntimeError(
                f"Native evaluation exited {returncode}; inspect diagnostics"
            )
    except Exception as error:
        fatal = f"{stage}: {error}"
        print(fatal, file=sys.stderr)
    finally:
        if owned:
            try:
                outcome = (
                    "failure"
                    if fatal or any(case["status"] == "FAIL" for case in cases)
                    else "success"
                )
                collect_diagnostics(output, env, outcome)
            except Exception as error:
                print(f"Diagnostic collection warning: {error}", file=sys.stderr)
            try:
                # Avoid native dev-profile down: it deletes VSS_DATA_DIR/volumes.
                run(
                    [
                        "docker",
                        "compose",
                        "-p",
                        "vss",
                        "-f",
                        work / "eval/compose.yml",
                        "--profile",
                        "alerts",
                        "--profile",
                        "alerts-verification-latency-prometheus",
                        "--env-file",
                        env_file,
                        "down",
                        "--remove-orphans",
                    ],
                    env=env,
                    timeout=180,
                )
            except Exception as error:
                fatal = f"{fatal + '; ' if fatal else ''}teardown: {error}"
        if work is not None and fatal is None:
            try:
                shutil.rmtree(work)
            except OSError as error:
                print(
                    f"Private runtime removal warning: {work}: {error}", file=sys.stderr
                )
        elif work is not None:
            print(
                f"Private runtime retained for node debugging: {work} (not archived)",
                file=sys.stderr,
            )
    if fatal:
        cases.append(
            {
                "test_case_id": "execution",
                "status": "FAIL",
                "metrics": {},
                "error": {"stage": stage, "message": fatal},
            }
        )
    passed = sum(case["status"] == "PASS" for case in cases)
    platform = discover_platform(config_id=args.config_id)
    if args.gpu_model:
        platform.setdefault("gpu", {})["model"] = args.gpu_model
    path = build_and_save(
        output / "vss-perf-results",
        SERVICE,
        cases,
        config_id=args.config_id,
        platform=platform,
        config=metadata_config,
        benchmark_name=args.scenario,
        benchmark_mode=metadata_config.get("setup", "latency"),
        triggered_by=args.triggered_by,
        pipeline_url=args.pipeline_url,
        release=args.release,
        duration_seconds=time.monotonic() - started,
        passed=passed,
        failed=len(cases) - passed,
    )
    print(f"Canonical report: {path}")
    return 1 if fatal else 0


if __name__ == "__main__":
    sys.exit(main())
