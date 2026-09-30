# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Protect native video sampling, binary scores, and shared evaluation imports."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from cosmos_reranker.reranking.base import Candidate, CandidateScore, ScoringReranker
from cosmos_reranker.reranking.common import homogeneous_candidate_modality, read_logits
from cosmos_reranker.reranking.data import Segment
from cosmos_reranker.reranking.frames import controlled_frame_indices


def test_video_sampling_respects_window_and_cap():
    candidate = Candidate(Segment(0, "video", 10, 15), 0, video_path=Path("video.mp4"))
    indices = controlled_frame_indices(
        candidate, fps=2, max_frames=6, total_frames=600, video_fps=30
    )
    assert len(indices) == 6
    assert indices[0] == 300
    assert indices[-1] == 450
    assert indices == sorted(indices)


def test_inference_readout_requires_both_requested_tokens():
    score, _ = read_logits("margin", {2: {"logprob": -0.25}, 4: {"logprob": -2.5}}, [2, 4], {}, "")
    assert score == 2.25
    score, metadata = read_logits("margin", {2: {"logprob": -0.25}}, [2, 4], {}, "")
    assert score is None
    assert metadata["error"] == "missing token logprob"


def test_mixed_media_scoring_batch_fails():
    image = Candidate(Segment(0, "image", None, None), 1, image_path=Path("image.jpg"))
    video = Candidate(Segment(1, "video", None, None), 0, video_path=Path("video.mp4"))
    with pytest.raises(ValueError, match="mixes image and video"):
        homogeneous_candidate_modality([image, video])


def test_scores_order_candidates_and_preserve_unscored_tail():
    class Scorer(ScoringReranker):
        def score_pairs(self, pairs):
            return [CandidateScore(-1), CandidateScore(2)]

    candidates = [Candidate(Segment(i, str(i), None, None), -float(i)) for i in range(3)]
    ordered = Scorer(rerank_depth=2).rerank("query", candidates)
    assert [candidate.segment.segment_id for candidate in ordered] == [1, 0, 2]
    assert ordered[-1].rerank_score is None


def test_shared_inference_and_evaluation_import_without_finetuning():
    package = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(package / "src"))
    script = """
import importlib.abc
import sys
class NoTraining(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('cosmos_reranker.finetuning') or fullname == 'cosmos_rl':
            raise AssertionError(f'Shared code imported training: {fullname}')
sys.meta_path.insert(0, NoTraining())
import cosmos_reranker.reranking.cosmos_reason
import cosmos_reranker.evaluation.evaluate
import cosmos_reranker.evaluation.merge_results
import cosmos_reranker.evaluation.generate_embeddings
import cosmos_reranker.evaluation.fit_val_apply_test_fusion
"""
    subprocess.run([sys.executable, "-c", script], env=env, check=True, capture_output=True)


def test_row_aligned_embeddings_match_pair_keys(tmp_path):
    import json
    import numpy as np
    from cosmos_reranker.evaluation.evaluate import load_embedding_dict

    pairs = tmp_path / "pairs.json"
    pairs.write_text(
        json.dumps(
            [
                {"dataset": "d", "image_path": "first.jpg", "caption": "red shirt"},
                {"dataset": "d", "image_path": "second.jpg", "caption": "blue shirt"},
                {"dataset": "d", "image_path": "first.jpg", "caption": "red shirt"},
            ]
        )
    )
    images = tmp_path / "images.npy"
    texts = tmp_path / "texts.npy"
    np.save(images, np.eye(2, dtype=np.float32))
    np.save(texts, np.array([[1, 0], [0, 1], [1, 0]], dtype=np.float32))
    assert list(load_embedding_dict(images, pairs, images=True)) == [
        "d\tfirst.jpg",
        "d\tsecond.jpg",
    ]
    assert list(load_embedding_dict(texts, pairs, images=False)) == ["red shirt", "blue shirt"]
    np.save(texts, np.ones((2, 2), dtype=np.float32))
    with pytest.raises(ValueError, match="Embedding rows"):
        load_embedding_dict(texts, pairs, images=False)
