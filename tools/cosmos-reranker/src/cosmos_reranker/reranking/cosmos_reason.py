# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import base64
import threading
from typing import Any

from numpy.typing import NDArray
from PIL import Image

from .base import Candidate, CandidateScore
from .common import (
    VLMReranker,
    answer_tail,
    homogeneous_candidate_modality,
    read_results,
    read_sampling_params,
)
from .config import RerankerConfig
from .frames import VideoSamplingError, parallel_map, sampled_video_array

_NANO_REPO = "nvidia/Cosmos3-Nano"

_PREDECODE_CACHE: dict[str, NDArray] = {}
_PREDECODE_LOCK = threading.Lock()


def _install_predecode_loader_patch() -> None:
    """Serve stashed pre-decoded arrays inside vLLM's video loader.

    vLLM re-decodes every video/jpeg base64 frame with serial PIL inside its
    frontend. When frames are pre-decoded (frame store or predecode handoff),
    the identical arrays are handed over instead, so the frontend does no
    serial JPEG work. Idempotent; only active when the option is set.
    """
    if getattr(_install_predecode_loader_patch, "patched", False):
        return
    _install_predecode_loader_patch.patched = True  # type: ignore[attr-defined]

    import numpy as np
    from vllm.multimodal.media.video import VideoMediaIO

    original = VideoMediaIO.load_base64

    def patched(self, media_type, data):
        if media_type.lower() != "video/jpeg" or "," not in data:
            return original(self, media_type, data)
        num_frames = self.num_frames
        parts = data.split(",", num_frames)[:num_frames] if num_frames > 0 else data.split(",")
        with _PREDECODE_LOCK:
            cached = {p: _PREDECODE_CACHE.pop(p) for p in parts if p in _PREDECODE_CACHE}
        if not cached:
            return original(self, media_type, data)

        def load_frame(part):
            if part in cached:
                return cached[part]
            return np.asarray(self.image_io.load_base64("image/jpeg", part))

        frames = np.stack([load_frame(p) for p in parts])
        total = int(frames.shape[0])
        fps = float(self.kwargs.get("fps", 1))
        frames_indices = self.kwargs.get("frames_indices")
        if frames_indices is not None and len(frames_indices) != total:
            raise ValueError("frames_indices length mismatch")
        total_num_frames = self.kwargs.get("total_num_frames", total)
        if total_num_frames < total:
            raise ValueError("total_num_frames < frames sent")
        duration = self.kwargs.get("duration")
        if duration is None:
            duration = total_num_frames / fps if fps > 0 else 0.0
        metadata = {
            "total_num_frames": total_num_frames,
            "fps": fps,
            "duration": duration,
            "video_backend": "jpeg_sequence",
            "frames_indices": frames_indices or list(range(total)),
            "do_sample_frames": self.kwargs.get("do_sample_frames", False),
        }
        return frames, metadata

    VideoMediaIO.load_base64 = patched


def _stash_predecoded(parts, arrays) -> None:
    with _PREDECODE_LOCK:
        _PREDECODE_CACHE.update(zip(parts, arrays))

class CosmosReasonReranker(VLMReranker):
    """Pointwise CR3 reranker using offline vLLM chat."""

    name = "cosmos_reason"

    def __init__(
        self, cfg: RerankerConfig, *, default_model_id: str = _NANO_REPO, name: str | None = None
    ) -> None:
        super().__init__(cfg, model_id=default_model_id, name=name)
        self.jpeg_quality = int(cfg.options.get("jpeg_quality", 95))
        self.lora_path = str(cfg.options.get("lora_path") or "").strip()
        self.predecode_handoff = bool(cfg.options.get("predecode_handoff", False))
        store_path = str(cfg.options.get("frame_store") or "").strip()
        self.frame_store = None
        if store_path:
            from .frame_store import FrameStore

            self.frame_store = FrameStore(store_path, jpeg_quality=self.jpeg_quality)
        if self.predecode_handoff or self.frame_store is not None:
            _install_predecode_loader_patch()
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

    def _video_data_url(self, candidate: Candidate) -> tuple[str, float]:
        if candidate.video_path is None:
            raise ValueError("Candidate has no video_path; cannot sample video frames")
        store = self.frame_store
        if store is not None and store.covers(
            candidate.video_path, candidate, self.fps, self.max_frames
        ):
            video_url, arrays, sample_fps = store.serve(
                candidate.video_path, candidate, self.fps, self.max_frames
            )
            if self.predecode_handoff:
                _stash_predecoded(video_url.split(",", 1)[1].split(","), arrays)
            return video_url, sample_fps

        sample = sampled_video_array(candidate, self.fps, self.max_frames)
        import cv2

        encoded_frames: list[str] = []
        decoded_frames: list = []
        for frame in sample.frames:
            ok, buffer = cv2.imencode(
                ".jpg",
                cv2.cvtColor(frame, cv2.COLOR_RGB2BGR),
                [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality],
            )
            if not ok:
                raise VideoSamplingError("Could not JPEG-encode CR3 sampled frame")
            part = base64.b64encode(buffer).decode("ascii")
            encoded_frames.append(part)
            if self.predecode_handoff:
                from io import BytesIO

                from numpy import asarray

                decoded_frames.append(asarray(Image.open(BytesIO(buffer.tobytes()))))
        if store is not None:
            store.put_sample(candidate.video_path, sample)
        if self.predecode_handoff:
            _stash_predecoded(encoded_frames, decoded_frames)
        return "data:video/jpeg;base64," + ",".join(encoded_frames), sample.sample_fps

    def _image_data_url(self, candidate: Candidate) -> str:
        if candidate.image_path is None:
            raise ValueError("Candidate has no image_path for CR3 image scoring")
        with Image.open(candidate.image_path) as image:
            media_type = Image.MIME.get(image.format or "")
        if not media_type:
            raise ValueError(f"Could not determine image MIME type: {candidate.image_path}")
        encoded = base64.b64encode(candidate.image_path.read_bytes()).decode("ascii")
        return f"data:{media_type};base64,{encoded}"

    def _messages(
        self, query: str, candidate: Candidate
    ) -> tuple[list[dict[str, Any]], float | None]:
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
            ], None
        video_url, sample_fps = self._video_data_url(candidate)
        prompt = self.prompt_text(query)
        return [
            {
                "role": "user",
                "content": [
                    {"type": "video_url", "video_url": {"url": video_url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ], sample_fps

    def _set_video_fps(self, fps: float) -> None:
        """Set vLLM's timestamp rate to the rate of the supplied JPEG frames."""
        try:
            self.media_io_kwargs["video"]["fps"] = fps
            self.llm.llm_engine.model_config.multimodal_config.media_io_kwargs["video"]["fps"] = fps
        except (AttributeError, KeyError, TypeError) as error:
            raise RuntimeError(
                "Cannot set the video fps reported to vLLM; use a compatible vLLM version"
            ) from error

    def score_pairs(self, pairs: list[tuple[str, Candidate]]) -> list[CandidateScore]:
        if not pairs:
            return []
        modality = homogeneous_candidate_modality([candidate for _query, candidate in pairs])
        mm_processor_kwargs = (
            self.video_mm_processor_kwargs
            if modality == "video"
            else self.image_mm_processor_kwargs
        )
        prepared = parallel_map(lambda item: self._messages(item[0], item[1]), pairs)
        if modality == "image":
            return self.candidate_scores(
                self._score_batch(
                    [messages for messages, _fps in prepared],
                    mm_processor_kwargs=mm_processor_kwargs,
                )
            )

        # vLLM's media fps is per chat call. Group equal sampled rates so the
        # timestamps it creates match the spacing of the explicit JPEG frames.
        grouped: dict[float, list[tuple[int, list[dict[str, Any]]]]] = {}
        for index, (messages, sample_fps) in enumerate(prepared):
            if sample_fps is None:
                raise RuntimeError("Video candidate has no sampled frame rate")
            grouped.setdefault(sample_fps, []).append((index, messages))
        results: list[tuple[float | None, dict] | None] = [None] * len(pairs)
        for sample_fps, items in grouped.items():
            self._set_video_fps(sample_fps)
            scored = self._score_batch(
                [messages for _, messages in items],
                mm_processor_kwargs=mm_processor_kwargs,
            )
            for (index, _messages), result in zip(items, scored):
                results[index] = result
        if any(result is None for result in results):
            raise RuntimeError("vLLM returned an incomplete video score batch")
        return self.candidate_scores([result for result in results if result is not None])

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
