# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared output-format and readout helpers for generative VLM rerankers."""

from __future__ import annotations
from typing import Any, Literal, Sequence
from .config import RerankerConfig
from .base import Candidate, CandidateScore, ScoringReranker

RELEVANCE_PROMPT = 'You are judging whether a short video clip matches a search query.\nQuery: "{query}"\nThe video is one candidate clip. Decide whether it matches the query.'
IMAGE_RELEVANCE_PROMPT = 'Query: "{query}"\nDoes the person in the image fully match the query?'
IMAGE_PROMPT_PREFIXES = {"default": ""}
COT_SUFFIX = ""
OUTPUT_FORMATS = {
    "logit_delta": {
        "suffix": " Answer with <answer>yes</answer> or <answer>no</answer>.",
        "prefix": "<answer>",
        "read": "margin",
        "pos": "yes",
        "neg": "no",
    }
}
COT_OUTPUT_FORMATS = set()
CandidateModality = Literal["image", "video"]


def homogeneous_candidate_modality(candidates: Sequence[Candidate]) -> CandidateModality:
    """Return the batch modality, rejecting empty or mixed candidate batches."""
    if not candidates:
        raise ValueError("Candidate batch must contain at least one candidate")
    image_flags = {candidate.image_path is not None for candidate in candidates}
    if len(image_flags) != 1:
        raise ValueError("Candidate batch mixes image and video inputs")
    return "image" if image_flags.pop() else "video"


def output_spec(output_format: str, *, cot: bool) -> dict[str, Any]:
    if output_format not in OUTPUT_FORMATS:
        raise ValueError(
            f"unknown output_format {output_format!r}; supported: {sorted(OUTPUT_FORMATS)}"
        )
    if cot and output_format not in COT_OUTPUT_FORMATS:
        raise ValueError(f"Reasoning generation is not supported for {output_format!r}")
    return OUTPUT_FORMATS[output_format]


def prompt_suffix(spec: dict[str, Any], *, cot: bool) -> str:
    return COT_SUFFIX if cot else str(spec["suffix"])


def first_token_id(tokenizer: Any, text: str) -> int:
    return tokenizer(text, add_special_tokens=False).input_ids[0]


def score_token_ids(tokenizer: Any, spec: dict[str, Any]) -> list[int]:
    read = spec["read"]
    if read == "margin":
        return [first_token_id(tokenizer, spec["pos"]), first_token_id(tokenizer, spec["neg"])]
    return []


def answer_tail(rationale: str, *, cot: bool, context: str = "") -> str:
    tail = rationale
    if cot:
        close = tail.rfind("</think>")
        if close != -1:
            tail = tail[: close + len("</think>")]
    if "<think>" in context + tail and "</think>" not in context + tail:
        tail += "</think>"
    return tail


def output_logprobs(output: Any) -> Any | None:
    outputs = getattr(output, "outputs", None) or []
    if not outputs:
        return None
    logprobs_by_pos = getattr(outputs[0], "logprobs", None)
    if not logprobs_by_pos:
        return None
    return logprobs_by_pos[0]


def _field(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def extract_logprobs(logprobs: Any, token_ids: list[int]) -> dict[int, float]:
    wanted = set(token_ids)
    out: dict[int, float] = {}
    if not isinstance(logprobs, dict):
        return out
    for raw_token_id, item in logprobs.items():
        try:
            token_id = int(raw_token_id)
        except (TypeError, ValueError):
            continue
        if token_id in wanted:
            out[token_id] = float(_field(item, "logprob"))
    return out


def read_logits(
    read: str, logprobs: Any, score_ids: list[int], spec: dict[str, Any], rationale: str
) -> tuple[float | None, dict]:
    values_by_id = extract_logprobs(logprobs, score_ids)
    if any((token_id not in values_by_id for token_id in score_ids)):
        return (
            None,
            {
                "rationale": rationale,
                "error": "missing token logprob",
                "seen_token_ids": sorted(values_by_id),
            },
        )
    values = [values_by_id[token_id] for token_id in score_ids]
    if read == "margin":
        return (values[0] - values[1], {"rationale": rationale, "logprobs": values})
    raise ValueError(f"Unsupported Cosmos readout: {read}")


def read_output_logits(
    read: str, output: Any, score_ids: list[int], spec: dict[str, Any], rationale: str
) -> tuple[float | None, dict]:
    logprobs = output_logprobs(output)
    if logprobs is None:
        return (None, {"rationale": rationale, "error": "missing token logprobs"})
    return read_logits(read, logprobs, score_ids, spec, rationale)


def read_sampling_params(read: str, score_ids: list[int]) -> Any:
    from vllm import SamplingParams

    if read == "margin":
        return SamplingParams(
            temperature=0.0, max_tokens=1, allowed_token_ids=score_ids, logprob_token_ids=score_ids
        )
    raise ValueError(f"Unsupported Cosmos readout: {read}")


def read_results(
    read: str, outputs: list[Any], score_ids: list[int], spec: dict[str, Any], rationales: list[str]
) -> list[tuple[float | None, dict]]:
    return [
        read_output_logits(read, output, score_ids, spec, rationale)
        for output, rationale in zip(outputs, rationales)
    ]


class VLMReranker(ScoringReranker):
    """Shared config and readout state for pointwise generative VLM rerankers."""

    def __init__(self, cfg: RerankerConfig, *, model_id: str, name: str | None = None) -> None:
        super().__init__(
            rerank_depth=cfg.rerank_depth,
            score_chunk_size=int(cfg.options.get("score_chunk_size", 256)),
        )
        if name is not None:
            self.name = name
        self.model_id = str(cfg.options.get("model_id", model_id))
        self.fps = float(cfg.fps)
        self.max_frames = int(cfg.max_frames)
        self.cot = cfg.options.get("reasoning", "none") == "cot"
        self.output_format = str(cfg.options.get("output_format", "logit_delta"))
        self.spec = output_spec(self.output_format, cot=self.cot)
        self.suffix = prompt_suffix(self.spec, cot=self.cot)
        image_prompt_mode = str(cfg.options.get("image_prompt_mode", "default"))
        if image_prompt_mode not in IMAGE_PROMPT_PREFIXES:
            raise ValueError(
                f"unknown image_prompt_mode {image_prompt_mode!r}; supported: {sorted(IMAGE_PROMPT_PREFIXES)}"
            )
        self.image_prompt_prefix = IMAGE_PROMPT_PREFIXES[image_prompt_mode]
        self.score_ids: list[int] = []

    def prompt_text(self, query: str) -> str:
        return RELEVANCE_PROMPT.format(query=query) + self.suffix

    def image_prompt_text(self, query: str) -> str:
        return (
            self.image_prompt_prefix
            + IMAGE_RELEVANCE_PROMPT.format(query=query)
            + self.suffix.replace("clip", "image")
        )

    def set_score_token_ids(self, tokenizer: Any) -> None:
        self.score_ids = score_token_ids(tokenizer, self.spec)

    @staticmethod
    def candidate_scores(results: list[tuple[float | None, dict]]) -> list[CandidateScore]:
        return [CandidateScore(score, meta) for score, meta in results]
