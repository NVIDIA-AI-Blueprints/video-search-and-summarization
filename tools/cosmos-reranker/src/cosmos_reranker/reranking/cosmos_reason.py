# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
import base64
from typing import Any
from PIL import Image
from .config import RerankerConfig
from .base import Candidate, CandidateScore
from .frames import parallel_map, sampled_video_array
from .common import (
    VLMReranker,
    answer_tail,
    homogeneous_candidate_modality,
    read_results,
    read_sampling_params,
)

_NANO_REPO = "nvidia/Cosmos3-Nano"


class CosmosReasonReranker(VLMReranker):
    """Pointwise CR3 reranker using offline vLLM chat."""

    name = "cosmos_reason"

    def __init__(
        self, cfg: RerankerConfig, *, default_model_id: str = _NANO_REPO, name: str | None = None
    ) -> None:
        super().__init__(cfg, model_id=default_model_id, name=name)
        self.jpeg_quality = int(cfg.options.get("jpeg_quality", 95))
        self.lora_path = str(cfg.options.get("lora_path") or "").strip()
        image_min_pixels = cfg.options.get("image_min_pixels")
        image_max_pixels = cfg.options.get("image_max_pixels")
        image_processor_kwargs: dict[str, int] = {}
        if image_min_pixels is not None:
            image_processor_kwargs["min_pixels"] = int(image_min_pixels)
        if image_max_pixels is not None:
            image_processor_kwargs["max_pixels"] = int(image_max_pixels)
        self.image_mm_processor_kwargs = image_processor_kwargs or None
        self.video_mm_processor_kwargs = {"do_sample_frames": False}
        self.media_io_kwargs = {
            "video": {"num_frames": -1, "fps": self.fps, "do_sample_frames": False}
        }
        from vllm import LLM

        self.llm = LLM(
            model=self.model_id,
            limit_mm_per_prompt={"image": 1, "video": 1},
            media_io_kwargs=self.media_io_kwargs,
            gpu_memory_utilization=float(cfg.options.get("gpu_memory_utilization", 0.85)),
            tensor_parallel_size=int(cfg.options.get("tensor_parallel_size", 1)),
            max_model_len=int(cfg.options.get("max_model_len", 32768)),
            enforce_eager=bool(cfg.options.get("enforce_eager", False)),
            hf_overrides=cfg.options.get("hf_overrides"),
            enable_lora=bool(self.lora_path),
            enable_tower_connector_lora=bool(cfg.options.get("enable_tower_connector_lora", False)),
            max_lora_rank=int(cfg.options.get("max_lora_rank", 16)),
            logprobs_mode=str(cfg.options.get("logprobs_mode", "raw_logprobs")),
        )
        if self.lora_path:
            from vllm.lora.request import LoRARequest

            self.lora_request = LoRARequest("pas_cr3", 1, self.lora_path)
        else:
            self.lora_request = None
        self.set_score_token_ids(self.llm.get_tokenizer())

    def _video_data_url(self, candidate: Candidate) -> str:
        sample = sampled_video_array(candidate, self.fps, self.max_frames)
        import cv2

        encoded_frames: list[str] = []
        for frame in sample.frames:
            ok, buffer = cv2.imencode(
                ".jpg",
                cv2.cvtColor(frame, cv2.COLOR_RGB2BGR),
                [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality],
            )
            if not ok:
                raise ValueError("Could not JPEG-encode CR3 sampled frame")
            encoded_frames.append(base64.b64encode(buffer).decode("ascii"))
        return "data:video/jpeg;base64," + ",".join(encoded_frames)

    def _image_data_url(self, candidate: Candidate) -> str:
        if candidate.image_path is None:
            raise ValueError("Candidate has no image_path for CR3 image scoring")
        with Image.open(candidate.image_path) as image:
            media_type = Image.MIME.get(image.format or "")
        if not media_type:
            raise ValueError(f"Could not determine image MIME type: {candidate.image_path}")
        encoded = base64.b64encode(candidate.image_path.read_bytes()).decode("ascii")
        return f"data:{media_type};base64,{encoded}"

    def _messages(self, query: str, candidate: Candidate) -> list[dict[str, Any]]:
        if candidate.image_path is not None:
            image_url = self._image_data_url(candidate)
            prompt = self.image_prompt_text(query)
            return [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_url}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ]
        video_url = self._video_data_url(candidate)
        prompt = self.prompt_text(query)
        return [
            {
                "role": "user",
                "content": [
                    {"type": "video_url", "video_url": {"url": video_url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ]

    def score_pairs(self, pairs: list[tuple[str, Candidate]]) -> list[CandidateScore]:
        if not pairs:
            return []
        modality = homogeneous_candidate_modality([candidate for _query, candidate in pairs])
        messages = parallel_map(lambda item: self._messages(item[0], item[1]), pairs)
        mm_processor_kwargs = (
            self.video_mm_processor_kwargs
            if modality == "video"
            else self.image_mm_processor_kwargs
        )
        return self.candidate_scores(
            self._score_batch(messages, mm_processor_kwargs=mm_processor_kwargs)
        )

    def _score_batch(
        self,
        messages_list: list[list[dict[str, Any]]],
        *,
        mm_processor_kwargs: dict[str, Any] | None = None,
    ) -> list[tuple[float | None, dict]]:
        if not messages_list:
            return []
        rationales = ["" for _ in messages_list]
        read = self.spec["read"]
        prefix = self.spec["prefix"]
        read_messages_list = [
            [
                *messages,
                {"role": "assistant", "content": answer_tail(rationale, cot=False) + prefix},
            ]
            for messages, rationale in zip(messages_list, rationales)
        ]
        params = read_sampling_params(read, self.score_ids)
        outputs = self._chat(
            read_messages_list,
            params,
            mm_processor_kwargs=mm_processor_kwargs,
            add_generation_prompt=False,
            continue_final_message=True,
        )
        return read_results(read, outputs, self.score_ids, self.spec, rationales)

    def _chat(
        self,
        messages_list: list[list[dict[str, Any]]],
        sampling_params,
        *,
        mm_processor_kwargs: dict[str, Any] | None,
        add_generation_prompt: bool = True,
        continue_final_message: bool = False,
    ):
        kwargs: dict[str, Any] = {}
        if mm_processor_kwargs is not None:
            kwargs["mm_processor_kwargs"] = mm_processor_kwargs
        return self.llm.chat(
            messages_list,
            sampling_params,
            use_tqdm=False,
            add_generation_prompt=add_generation_prompt,
            continue_final_message=continue_final_message,
            lora_request=self.lora_request,
            **kwargs,
        )
