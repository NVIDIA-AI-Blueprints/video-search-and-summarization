# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Token-aware, sentence-preferring chunks for Cosmos-Embed1 text."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from tokenizers import BertWordPieceTokenizer

from vss_unified_memory.application.models import TextPassage

_SPECIAL_TOKEN_COUNT = 2
_SENTENCE_ENDINGS = frozenset({".", "!", "?"})


@dataclass(frozen=True, slots=True)
class _Token:
    value: str
    start_char: int
    end_char: int


class BertWordPiecePassageChunker:
    """Build deterministic overlapping chunks using the embedding model vocabulary."""

    def __init__(
        self,
        vocab_path: Path,
        *,
        max_tokens: int = 128,
        overlap_tokens: int = 16,
        max_characters: int = 1000,
    ) -> None:
        if not vocab_path.is_file():
            raise ValueError(f"tokenizer vocabulary does not exist: {vocab_path}")
        if max_tokens <= _SPECIAL_TOKEN_COUNT:
            raise ValueError("max_tokens must leave room for model special tokens")
        content_capacity = max_tokens - _SPECIAL_TOKEN_COUNT
        if not 0 <= overlap_tokens < content_capacity:
            raise ValueError("overlap_tokens must be smaller than the content-token capacity")
        if max_characters < 1:
            raise ValueError("max_characters must be positive")

        self._tokenizer = BertWordPieceTokenizer(
            str(vocab_path),
            clean_text=True,
            handle_chinese_chars=True,
            strip_accents=True,
            lowercase=True,
        )
        self._max_tokens = max_tokens
        self._content_capacity = content_capacity
        self._overlap_tokens = overlap_tokens
        self._max_characters = max_characters
        vocab_hash = sha256(vocab_path.read_bytes()).hexdigest()[:12]
        self._version = f"cosmos-embed1-wordpiece-v1-{max_tokens}t-{overlap_tokens}o-{vocab_hash}"

    @property
    def version(self) -> str:
        return self._version

    def chunk(self, record_id: str, text: str) -> tuple[TextPassage, ...]:
        if not record_id.strip():
            raise ValueError("record_id cannot be empty")
        if not text.strip():
            raise ValueError("summary text cannot be empty")

        encoding = self._tokenizer.encode(text, add_special_tokens=False)
        tokens = tuple(
            _Token(value, offset[0], offset[1])
            for value, offset in zip(encoding.tokens, encoding.offsets, strict=True)
            if offset[1] > offset[0]
        )
        if not tokens:
            raise ValueError("summary text produced no tokenizer tokens")

        chunks: list[TextPassage] = []
        start = 0
        while start < len(tokens):
            hard_end = min(start + self._content_capacity, len(tokens))
            hard_end = self._fit_character_limit(tokens, start, hard_end)
            end = self._prefer_text_boundary(text, tokens, start, hard_end)
            start_char = tokens[start].start_char
            end_char = tokens[end - 1].end_char
            chunk_text = text[start_char:end_char]
            token_count = end - start + _SPECIAL_TOKEN_COUNT
            chunks.append(
                TextPassage.create(
                    record_id=record_id,
                    ordinal=len(chunks),
                    start_char=start_char,
                    end_char=end_char,
                    token_count=token_count,
                    text=chunk_text,
                )
            )
            if end == len(tokens):
                break
            start = self._next_start(tokens, start, end)

        return tuple(chunks)

    def _fit_character_limit(self, tokens: tuple[_Token, ...], start: int, end: int) -> int:
        while end > start + 1 and tokens[end - 1].end_char - tokens[start].start_char > self._max_characters:
            end -= 1
        if tokens[end - 1].end_char - tokens[start].start_char > self._max_characters:
            raise ValueError("one tokenizer token exceeds the embedding service character limit")
        while end > start + 1 and end < len(tokens) and tokens[end].value.startswith("##"):
            end -= 1
        return end

    def _prefer_text_boundary(
        self,
        text: str,
        tokens: tuple[_Token, ...],
        start: int,
        hard_end: int,
    ) -> int:
        if hard_end == len(tokens):
            return hard_end
        minimum = start + max(1, (hard_end - start) // 2)
        paragraph_end: int | None = None
        sentence_end: int | None = None
        word_end: int | None = None
        for end in range(minimum, hard_end + 1):
            if end < len(tokens) and tokens[end].value.startswith("##"):
                continue
            word_end = end
            gap_end = tokens[end].start_char if end < len(tokens) else len(text)
            gap = text[tokens[end - 1].end_char : gap_end]
            if "\n\n" in gap:
                paragraph_end = end
            if tokens[end - 1].value in _SENTENCE_ENDINGS:
                sentence_end = end
        return paragraph_end or sentence_end or word_end or hard_end

    def _next_start(self, tokens: tuple[_Token, ...], previous_start: int, end: int) -> int:
        start = max(previous_start + 1, end - self._overlap_tokens)
        while start < end and tokens[start].value.startswith("##"):
            start += 1
        return min(start, end)
