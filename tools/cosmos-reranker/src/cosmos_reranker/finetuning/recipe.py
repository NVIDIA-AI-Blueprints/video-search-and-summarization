# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
import argparse
import os
import shlex
import signal
import subprocess
import sys
import time
import tomllib
from pathlib import Path

from .model import resolve_model


ROOT = Path(__file__).resolve().parents[3]

BASE_COMMIT = "89b1fdd89441964868a8767ee964e1e41a85ea68"
STAGES = {
    "stage0": ("train_stage0_to_step2000_replay.toml", 2000),
    "stage1": ("train_stage2000_to_step8000_replay.toml", 8000),
}


def run(*args, **kwargs):
    subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def process_environment(root, *, extra_paths=()):
    env = os.environ.copy()
    env["COSMOS_PYTHON_EXECUTABLE"] = sys.executable
    env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), env.get("PATH", "")])
    paths = [str(root / "src")]
    paths[:0] = [str(path) for path in extra_paths]
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env.update(
        OMP_NUM_THREADS="1",
        MKL_NUM_THREADS="1",
        OPENBLAS_NUM_THREADS="1",
        TOKENIZERS_PARALLELISM="false",
        TRANSFORMERS_AUTO_SWITCH="0",
    )
    return env


def environment(root, *, runtime=False):
    paths = []
    if runtime:
        dependency = root / ".runtime/trainer"
        if not (dependency / "cosmos_rl").is_dir():
            raise ValueError(
                "Run `python -m cosmos_reranker.finetuning.recipe runtime` before training"
            )
        paths.append(dependency)
    return process_environment(root, extra_paths=paths)


def runtime(args):
    """Create an isolated, pinned framework checkout and apply the recipe patch."""
    destination = args.root / ".runtime/trainer"
    patch = Path(__file__).resolve().parent / "runtime/trainer.patch"
    if destination.exists():
        revision = subprocess.check_output(
            ["git", "-C", str(destination), "rev-parse", "HEAD"],
            text=True,
        ).strip()
        if revision != BASE_COMMIT:
            raise ValueError(f"Training runtime version differs; inspect {destination}")
        run("git", "-C", destination, "apply", "--reverse", "--check", patch)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        run("git", "init", destination)
        run("git", "-C", destination, "remote", "add", "origin", args.source)
        run("git", "-C", destination, "fetch", "--depth=1", "origin", BASE_COMMIT)
        run("git", "-C", destination, "checkout", "--detach", "FETCH_HEAD")
        run("git", "-C", destination, "apply", "--check", patch)
        run("git", "-C", destination, "apply", patch)
    print(f"Prepared training runtime: {destination}")
    run(sys.executable, "-m", "pip", "install", "-e", destination)


def configure(args):
    """Render both stages while preserving the source optimizer and LR horizon."""
    import toml

    if args.gpus not in (8, 16, 32):
        raise ValueError("Use 8, 16, or 32 data-parallel GPUs (global candidate batch remains 640)")
    model = resolve_model(args.model, args.root)
    batch = 640 // args.gpus
    for stage, (name, _) in STAGES.items():
        config = tomllib.loads((Path(__file__).resolve().parent / "configs" / name).read_text())
        output = args.run_root.resolve() / stage
        config["results_dir"] = str(output)
        config["redis"] = str(args.redis_port + (1 if stage == "stage0" else 0))
        config["train"]["output_dir"] = str(output)
        config["train"]["timestamp"] = f"cosmosreranker{stage}dp{args.gpus}"
        config["train"]["resume"] = (
            False
            if stage == "stage0"
            else str(args.run_root.resolve() / "stage0/checkpoints/step_2000/policy")
        )
        config["train"]["train_batch_per_replica"] = batch
        config["train"]["train_policy"]["dataloader_batch_size"] = batch
        config["policy"]["parallelism"]["dp_replicate_size"] = args.gpus
        config["policy"]["model_name_or_path"] = str(model)
        custom = config["custom"]
        custom["train_dataset"]["annotation_path"] = [
            str(args.data_root.resolve() / filename)
            for filename in (
                "train_deployed_top20_k20_trainonly750k_seed880821.json",
                "train_deployed_top20_k20_remaining_after750k_seed880822.json",
            )
        ]
        custom["train_dataset"]["media_path"] = str(args.pas_root.resolve() / "images")
        attributes = custom["structured_attribute"]
        attributes["train_records_path"] = str(
            args.data_root.resolve() / "attribute_replay/train_records.json"
        )
        attributes["validation_records_path"] = str(
            args.data_root.resolve() / "attribute_replay/val_records.json"
        )
        attributes["attribute_vocab_path"] = str(args.pas_root.resolve() / "attribute_vocab.json")
        cache = args.cache_root.resolve()
        custom["visual_cache"]["manifests"] = [
            str(cache / directory / f"manifest_rank{rank:02d}.json")
            for directory in (
                "pas_cr3_block24_prefix_step03906_trainonly750k_star",
                "pas_cr3_block24_prefix_step03906_top50_supplement4474_clean",
            )
            for rank in range(8)
        ]
        destination = args.run_root / "configs" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(toml.dumps(config))
        print(destination)


def prepare(args):
    from cosmos_reranker.evaluation.embedders.siglip_v2_checkpoint import resolve_tokenizer_dir

    model = resolve_model(args.model, args.root)
    tokenizer = resolve_tokenizer_dir(args.tokenizer)
    env = environment(args.root)
    env.update(
        PAS_DATA_ROOT=str(args.pas_root.resolve()),
        RETRIEVER_CHECKPOINT=str(args.retriever.resolve()),
        SIGLIP_TOKENIZER=str(tokenizer),
        VAL_CAPTION_DENYLIST=str(args.validation_subset.resolve()),
        CR3_BASE_MODEL=str(model),
        PAS_DATA_OUTPUT=str(args.data_root.resolve()),
        PAS_EMBED_OUTPUT=str(args.embedding_root.resolve()),
        PAS_CACHE_OUTPUT=str(args.cache_root.resolve()),
        PYTHON_BIN=sys.executable,
        TORCHRUN_BIN=str(Path(sys.executable).parent / "torchrun"),
    )
    if args.gallery_embeddings:
        env["TRAIN_IMAGE_EMBEDDINGS"] = str(args.gallery_embeddings.resolve())
    run("bash", args.root / "src/cosmos_reranker/finetuning/prepare.sh", args.stage, env=env)


def export_adapter(root, run_root):
    adapter = run_root / "stage1/safetensors/step_8000"
    run(
        sys.executable,
        "-m",
        "cosmos_reranker.finetuning.wait_adapter_export",
        "--adapter-dir",
        adapter,
        env=environment(root),
    )
    destination = run_root / "deploy"
    if destination.exists():
        raise ValueError(f"Export destination exists; inspect it before exporting: {destination}")
    run(
        sys.executable,
        "-m",
        "cosmos_reranker.finetuning.split_joint_lora_adapter",
        "--adapter",
        adapter,
        "--language-output",
        destination / "language",
        "--visual-output",
        destination / "visual",
        env=environment(root),
    )


def train(args):
    name, target = STAGES[args.stage]
    config = args.run_root / "configs" / name
    if not config.is_file():
        raise ValueError(f"Run configure first: {config}")
    settings = tomllib.loads(config.read_text())
    output = Path(settings["train"]["output_dir"])
    marker = output / f"checkpoints/step_{target}/policy/.rank_0_complete"
    if args.stage == "stage1":
        prior = Path(settings["train"]["resume"]) / ".rank_0_complete"
        if not prior.is_file():
            raise ValueError(f"Stage 0 must finish first: {prior}")
    if marker.is_file():
        print(f"Stage already completed: {marker}")
        return
    env = environment(args.root, runtime=True)
    env.setdefault("WANDB_MODE", "offline")
    env.setdefault("COSMOS_MASTER_PORT", str(int(settings["redis"]) + 20000))
    env.update(COSMOS_LOCAL_STATIC_RDZV="1", COSMOS_SHUTDOWN_ON_NO_POLICY_REPLICAS="1")
    command = [str(Path(sys.executable).parent / "cosmos-rl"), "--config", str(config)]
    if args.launch_args:
        command.extend(shlex.split(args.launch_args))
    command.append(str(args.root / "src/cosmos_reranker/finetuning/training.py"))
    output.mkdir(parents=True, exist_ok=True)
    launcher = subprocess.Popen(command, env=env)
    print(f"Started PID {launcher.pid}; target checkpoint: {marker}", flush=True)
    try:
        while launcher.poll() is None:
            if marker.is_file():
                # Do not stop while asynchronous safetensors export is incomplete.
                run(
                    sys.executable,
                    "-m",
                    "cosmos_reranker.finetuning.wait_adapter_export",
                    "--adapter-dir",
                    output / f"safetensors/step_{target}",
                    env=environment(args.root),
                )
                launcher.send_signal(signal.SIGUSR1)
                break
            time.sleep(5)
        status = launcher.wait()
    except BaseException:
        if launcher.poll() is None:
            launcher.send_signal(signal.SIGUSR1)
            launcher.wait()
        raise
    if not marker.is_file():
        raise RuntimeError(
            f"Training exited {status} without the complete target checkpoint: {marker}"
        )
    print(f"Completed {args.stage} at step {target}")


def merge(args):
    model = resolve_model(args.model, args.root)
    run(
        sys.executable,
        "-m",
        "cosmos_reranker.finetuning.merge_adapter",
        "--base-model",
        model,
        "--adapter",
        args.adapter / "visual",
        "--output",
        args.output,
        "--merge-dtype",
        "float32",
        "--save-dtype",
        "float16",
        env=environment(args.root),
    )


def register_commands(commands):
    p = commands.add_parser("runtime", help="Prepare the isolated training runtime")
    p.add_argument("--source", default="https://github.com/nvidia-cosmos/cosmos-rl.git")
    p.set_defaults(func=runtime)
    p = commands.add_parser("configure", help="Render the exact two-stage fine-tuning recipe")
    for name in ("pas-root", "data-root", "cache-root", "run-root"):
        p.add_argument(f"--{name}", type=Path, required=True)
    p.add_argument("--model", type=Path, default=os.environ.get("BASE_MODEL"))
    p.add_argument("--gpus", type=int, choices=(8, 16, 32), default=8)
    p.add_argument("--redis-port", type=int, default=13220)
    p.set_defaults(func=configure)
    p = commands.add_parser("prepare", help="Mine K20 groups and build the frozen vision cache")
    p.add_argument("stage", choices=("indices", "embeddings", "mine", "attributes", "cache", "all"))
    for name in (
        "pas-root",
        "retriever",
        "validation-subset",
        "data-root",
        "embedding-root",
        "cache-root",
    ):
        p.add_argument(f"--{name}", type=Path, required=True)
    p.add_argument("--model", type=Path, default=os.environ.get("BASE_MODEL"))
    p.add_argument("--tokenizer", type=Path, default=os.environ.get("SIGLIP_TOKENIZER"))
    p.add_argument("--gallery-embeddings", type=Path)
    p.set_defaults(func=prepare)
    p = commands.add_parser("train", help="Train to step 2000 or resume to step 8000")
    p.add_argument("stage", choices=tuple(STAGES))
    p.add_argument("--run-root", type=Path, required=True)
    p.add_argument(
        "--launch-args", help="Additional Cosmos launcher flags as a shell-quoted string"
    )
    p.set_defaults(func=train)
    p = commands.add_parser(
        "export", help="Split a newly trained joint LoRA into deployable adapters"
    )
    p.add_argument("--run-root", type=Path, required=True)
    p.set_defaults(func=lambda a: export_adapter(a.root, a.run_root))
    p = commands.add_parser("merge-vision", help="Merge the visual adapter into an inference base")
    p.add_argument("--model", type=Path, default=os.environ.get("BASE_MODEL"))
    p.add_argument("--adapter", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.set_defaults(func=merge)


def main():
    parser = argparse.ArgumentParser(
        description="Prepare data and reproduce the selected checkpoint"
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    register_commands(commands)
    p = commands.add_parser("checkpoint", help="Prepare the reviewed local checkpoint")
    p.add_argument("--adapter", type=Path)

    def checkpoint(args):
        from cosmos_reranker.finetuning.checkpoint import materialize

        materialize(args.adapter or args.root / "checkpoints/finetuned-cr3")

    p.set_defaults(func=checkpoint)
    args = parser.parse_args()
    args.root = args.root.resolve()
    try:
        args.func(args)
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"recipe: {error}\n")


if __name__ == "__main__":
    main()
