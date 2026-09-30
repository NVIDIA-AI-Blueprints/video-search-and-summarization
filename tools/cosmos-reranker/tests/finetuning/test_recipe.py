# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Protect the checkpoint's loss, cache identity, and grouped batch contract."""

import json
import math
import os
import shutil
import signal
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch

from cosmos_reranker.finetuning import recipe
from cosmos_reranker.finetuning.recipe import STAGES
from cosmos_reranker.finetuning.loss import (
    BinaryResponseSpec,
    RankPointLoss,
    RankPointWithStructuredAttributeLoss,
    extract_binary_scores,
)
from cosmos_reranker.finetuning.visual_cache import visual_cache_key
from cosmos_reranker.finetuning.prepare_annotations import cache_image_names
from cosmos_reranker.evaluation.generate_embeddings import load_pas_rows

SPEC = BinaryResponseSpec((1, 2, 3), (1, 4, 3), 1, 2, 4)


def test_training_children_use_the_invoked_python_environment(tmp_path, monkeypatch):
    training_bin = tmp_path / "training/bin"
    system_bin = tmp_path / "system/bin"
    for directory in (training_bin, system_bin):
        directory.mkdir(parents=True)
        for name in ("python", "torchrun"):
            executable = directory / name
            executable.touch()
            executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(system_bin))
    monkeypatch.setattr(recipe.sys, "executable", str(training_bin / "python"))
    env = recipe.process_environment(tmp_path)
    for name in ("python", "torchrun"):
        assert shutil.which(name, path=env["PATH"]) == str(training_bin / name)
    assert str(system_bin) in env["PATH"].split(os.pathsep)
    # A container shell can reset PATH after the environment is passed to it.
    env["PATH"] = str(system_bin)
    assert env["COSMOS_PYTHON_EXECUTABLE"] == str(training_bin / "python")


def test_cache_uses_the_same_exported_images_as_retrieval(tmp_path):
    rows = [
        {"dataset": "test", "image_path": "images/nested/person.jpg",
         "unique_name": "train_0001.jpg", "caption": "red shirt", "query_type": "easy"},
        {"dataset": "test", "image_path": "images/nested/person.jpg",
         "unique_name": "train_0001_alias.jpg", "caption": "blue pants", "query_type": "medium"},
        {"dataset": "test", "image_path": "images/other/person.jpg",
         "unique_name": "train_0002.jpg", "caption": "black shoes", "query_type": "hard"},
    ]
    pairs = tmp_path / "train_pairs.json"
    pairs.write_text(json.dumps(rows))
    image_root = tmp_path / "images"
    images, _ = load_pas_rows(pairs, image_root)
    assert [image_root / name for name in cache_image_names(pairs)] == [
        image["path"] for image in images
    ]
    assert cache_image_names(pairs) == ["train_0001.jpg", "train_0002.jpg"]


@pytest.mark.parametrize("positive_count", [1, 3, 19])
def test_equal_scores_preserve_positive_probability_mass(positive_count):
    labels = torch.tensor([1.0] * positive_count + [0.0] * (20 - positive_count))
    scores = torch.zeros(20, requires_grad=True)
    value = RankPointLoss(SPEC).from_scores(scores, labels)
    assert value.item() == pytest.approx(math.log(20 / positive_count) + math.log(2))
    value.backward()
    assert torch.all(scores.grad[:positive_count] < 0)
    assert torch.all(scores.grad[positive_count:] > 0)


def test_binary_projection_uses_state_before_answer_and_fp32():
    output = torch.zeros(2, 6, 8, dtype=torch.bfloat16, requires_grad=True)
    labels = torch.tensor([[-100, -100, 1, 2, 3, -100], [-100, -100, 1, 4, 3, -100]])
    weight = torch.zeros(10, 8, dtype=torch.bfloat16)
    weight[2, 0] = 2
    weight[4, 0] = -1
    with torch.no_grad():
        output[:, 2, 0] = torch.tensor([1.0, -1.0])
    scores, binary = extract_binary_scores(output, labels, SPEC, projection_weight=weight)
    assert scores.dtype == torch.float32
    torch.testing.assert_close(scores, torch.tensor([3.0, -3.0]))
    torch.testing.assert_close(binary, torch.tensor([1.0, 0.0]))
    scores.sum().backward()
    assert output.grad[:, :2].count_nonzero() == 0
    assert output.grad[:, 3:].count_nonzero() == 0
    assert output.grad[:, 2].count_nonzero() == 2


@pytest.mark.parametrize("labels", [torch.ones(20), torch.zeros(20), torch.zeros(19)])
def test_invalid_query_groups_fail(labels):
    with pytest.raises(ValueError):
        RankPointLoss(SPEC).from_scores(torch.zeros_like(labels), labels)


@pytest.mark.parametrize("scaling", [1.0, 0.5])
def test_structured_loss_keeps_weight_gradients_and_consumes_metadata(scaling):
    output = torch.zeros(20, 6, 8, requires_grad=True)
    target = torch.tensor(
        [[-100, -100, 1, 2 if index < 3 else 4, 3, -100] for index in range(20)]
    )
    records = [{"image": index} for index in range(20)]

    class ChoiceLoss:
        def __call__(self, output, target, selected, projection_weight):
            assert selected == records
            contribution = output[:, 3, 0].mean() + 2.0
            return contribution, {"attribute": (contribution.detach() * 20, 20)}, None, None

    loss = RankPointWithStructuredAttributeLoss(
        RankPointLoss(SPEC), ChoiceLoss(), SimpleNamespace(loss_weight=0.25)
    )
    loss.set_structured_records(records)
    value = loss(output, target, loss_scaling_factor=scaling)
    assert value.item() == pytest.approx((math.log(20 / 3) + math.log(2) + 0.5) * scaling)
    loss.assert_structured_records_consumed()
    assert loss.mean_metrics()["attribute"].item() == pytest.approx(2.0)
    value.backward()
    torch.testing.assert_close(output.grad[:, 3, 0], torch.full((20,), 0.25 * scaling / 20))
    assert output.grad[:, 2].count_nonzero() > 0
    with pytest.raises(ValueError, match="structured metadata"):
        loss(output, target)


def test_cache_identity_resolves_symlink(tmp_path):
    image = tmp_path / "image.jpg"
    image.touch()
    alias = tmp_path / "alias.jpg"
    alias.symlink_to(image)
    assert visual_cache_key(alias) == visual_cache_key(image)
    assert visual_cache_key(image) != visual_cache_key(tmp_path / "other.jpg")


def test_rendered_32_gpu_recipe_retains_schedule_and_resumption(tmp_path, monkeypatch):
    monkeypatch.setattr(recipe, "resolve_model", lambda model, root: model.resolve())
    package = Path(__file__).resolve().parents[2]
    argv = ["recipe", "--root", str(package), "configure", "--gpus", "32"]
    for name in ("model", "pas-root", "data-root", "cache-root", "run-root"):
        argv.extend([f"--{name}", str(tmp_path / name)])
    monkeypatch.setattr(sys, "argv", argv)
    recipe.main()
    configs = tmp_path / "run-root/configs"
    first = tomllib.loads((configs / STAGES["stage0"][0]).read_text())
    second = tomllib.loads((configs / STAGES["stage1"][0]).read_text())
    for config in (first, second):
        assert config["train"]["max_num_steps"] == 200000
        assert config["train"]["optm_warmup_steps"] == 500
        assert (
            config["train"]["train_batch_per_replica"]
            * config["policy"]["parallelism"]["dp_replicate_size"]
            == 640
        )
        assert config["train"]["train_policy"]["mini_batch"] == 20
        assert len(config["custom"]["train_dataset"]["annotation_path"]) == 2
        assert len(config["custom"]["visual_cache"]["manifests"]) == 16
        assert not config["validation"]["enable"]
    assert first["train"]["resume"] is False
    assert second["train"]["resume"] == str(
        tmp_path / "run-root/stage0/checkpoints/step_2000/policy"
    )
    assert first["custom"]["loss"] == second["custom"]["loss"]
    assert first["custom"]["structured_attribute"]["loss_weight"] == 0.25
    assert second["custom"]["structured_attribute"]["loss_weight"] == 0.25
    assert first["train"]["optm_lr"] == second["train"]["optm_lr"]


def test_train_uses_installed_launcher_and_waits_for_adapter_export(tmp_path, monkeypatch):
    run_root = tmp_path / "run"
    output = run_root / "stage0"
    config = run_root / "configs" / STAGES["stage0"][0]
    config.parent.mkdir(parents=True)
    config.write_text(f'redis = "13221"\n[train]\noutput_dir = "{output}"\n')
    marker = output / "checkpoints/step_2000/policy/.rank_0_complete"
    executable = tmp_path / "environment/bin/python"
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(recipe, "environment", lambda root, runtime=False: {})
    events = []

    class Launcher:
        pid = 123

        def __init__(self, command, env):
            events.append(("launch", command))
            marker.parent.mkdir(parents=True)
            marker.touch()

        def poll(self):
            return None

        def send_signal(self, sig):
            events.append(("signal", sig))

        def wait(self):
            return 0

    monkeypatch.setattr(recipe.subprocess, "Popen", Launcher)
    monkeypatch.setattr(recipe, "run", lambda *args, **kwargs: events.append(("export", args)))
    args = SimpleNamespace(root=tmp_path, run_root=run_root, stage="stage0", launch_args=None)
    recipe.train(args)

    assert events[0] == (
        "launch",
        [
            str(executable.parent / "cosmos-rl"),
            "--config",
            str(config),
            str(tmp_path / "src/cosmos_reranker/finetuning/training.py"),
        ],
    )
    assert events[1][0] == "export"
    assert "cosmos_reranker.finetuning.wait_adapter_export" in events[1][1]
    assert events[2] == ("signal", signal.SIGUSR1)


def test_distributed_sampler_preserves_complete_groups():
    from cosmos_reranker.finetuning.training import DistributedGroupBatchSampler

    assigned = []
    for rank in range(8):
        sampler = DistributedGroupBatchSampler(
            range(640),
            group_size=20,
            batch_size=80,
            num_replicas=8,
            rank=rank,
            shuffle=True,
            seed=160901,
        )
        batches = list(sampler)
        assert len(batches) == 1
        for start in range(0, 80, 20):
            group = batches[0][start : start + 20]
            assert group == list(range(group[0], group[0] + 20))
            assert group[0] % 20 == 0
        assigned.extend(batches[0])
    assert sorted(assigned) == list(range(640))
