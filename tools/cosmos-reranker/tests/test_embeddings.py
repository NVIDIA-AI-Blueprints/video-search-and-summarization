# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import argparse
import json

import numpy as np

from cosmos_reranker.evaluation import generate_embeddings
from cosmos_reranker.evaluation.evaluate import load_embedding_dict


def test_repeated_caption_across_batches_uses_first_vector(tmp_path, monkeypatch):
    class BatchDependentEncoder:
        embedding_dim = 2
        calls = 0

        def embed_text(self, captions):
            self.calls += 1
            return np.asarray(
                [
                    [1.0, 0.001 * (self.calls - 1)] if c == "red" else [0.0, 1.0]
                    for c in captions
                ],
                dtype=np.float32,
            )

    queries = [{"caption": c} for c in ("red", "blue", "red")]
    pairs = tmp_path / "pairs.json"
    pairs.write_text(json.dumps(queries))
    args = argparse.Namespace(
        text_batch_size=2, overwrite=False, checkpoint=tmp_path / "model", pairs_file=pairs
    )
    monkeypatch.setattr(generate_embeddings, "_write_array_manifest", lambda *a, **k: None)

    path = generate_embeddings.generate_text_embeddings(
        BatchDependentEncoder(), queries, tmp_path, args
    )
    array = np.load(path)
    np.testing.assert_array_equal(array, [[1, 0], [0, 1], [1, 0]])
    cache = load_embedding_dict(path, pairs, images=False)
    assert set(cache) == {"red", "blue"}
    np.testing.assert_array_equal(cache["red"], array[0])
