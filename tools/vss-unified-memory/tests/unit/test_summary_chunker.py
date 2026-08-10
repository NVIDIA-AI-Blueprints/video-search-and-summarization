# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

from vss_unified_memory.adapters.chunking.bert_wordpiece import BertWordPiecePassageChunker

VOCAB = Path(__file__).parents[1] / "fixtures" / "test-vocab.txt"


def test_chunker_preserves_offsets_limits_and_overlap() -> None:
    text = "Alpha beta gamma delta. Epsilon zeta eta theta. Iota kappa lambda mu. Nu xi omicron."
    chunker = BertWordPiecePassageChunker(VOCAB, max_tokens=10, overlap_tokens=2)

    chunks = chunker.chunk("summary:1", text)

    assert len(chunks) > 1
    assert all(chunk.token_count <= 10 for chunk in chunks)
    assert all(chunk.text == text[chunk.start_char : chunk.end_char] for chunk in chunks)
    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
    assert chunks[1].start_char < chunks[0].end_char
    assert chunks[0].text.endswith(".")
    assert "128t" not in chunker.version


def test_chunker_uses_model_limit_in_version() -> None:
    chunker = BertWordPiecePassageChunker(VOCAB, max_tokens=128, overlap_tokens=16)
    chunk = chunker.chunk("summary:1", "Alpha beta gamma.")[0]
    assert chunk.token_count == 6
    assert "128t-16o" in chunker.version
