# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Release input-contract regression tests; run in the RT-Embed environment."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import torch
from common.chunk_info import ChunkInfo
from models.base_vlm_model import InputConfig
from torchvision.transforms import InterpolationMode, Resize
from vlm_pipeline.video_file_frame_getter import (
    DefaultFrameSelector,
    VideoFileFrameGetter,
    resize_file_frames,
    select_file_indices,
)


class FilePreprocessingTests(unittest.TestCase):
    def test_short_clip_repeats_frames_and_preserves_timestamps(self):
        a, b = object(), object()
        frames, pts = select_file_indices([a, b], [1.5, 1.6], 8)
        self.assertEqual(frames, [a] * 4 + [b] * 4)
        self.assertEqual(pts, [1.5] * 4 + [1.6] * 4)

    def test_rounding_matches_supplier_rule_and_keeps_endpoints(self):
        # Half-way indices distinguish round-to-even from floor sampling.
        frames, pts = select_file_indices(list(range(6)), list(range(6)), 3)
        self.assertEqual(frames, [0, 2, 5])
        self.assertEqual(pts, frames)

    def test_single_frame_repeated_to_requested_count(self):
        self.assertEqual(select_file_indices([9], [2.5], 8), ([9] * 8, [2.5] * 8))

    def test_invalid_selection_fails_instead_of_embedding_partial_data(self):
        for frames, pts, count in (([], [], 8), ([1], [], 8), ([1], [0], 0)):
            with (
                self.subTest(frames=frames, pts=pts, count=count),
                self.assertRaises(ValueError),
            ):
                select_file_indices(frames, pts, count)

    def test_resize_matches_supplier_and_supports_mixed_shapes(self):
        expected_resize = Resize(
            (8, 8), interpolation=InterpolationMode.BILINEAR, antialias=True
        )
        frames = [
            torch.arange(48 * 64 * 3, dtype=torch.int32)
            .remainder(256)
            .byte()
            .reshape(48, 64, 3),
            torch.full((6, 11, 3), 128, dtype=torch.uint8),
        ]
        actual = resize_file_frames(frames, (8, 8))
        for original, resized in zip(frames, actual):
            expected = expected_resize(original.permute(2, 0, 1)).permute(1, 2, 0)
            self.assertTrue(torch.equal(resized, expected))
            self.assertEqual(resized.dtype, torch.uint8)
            self.assertEqual(resized.device, original.device)
        unfiltered = Resize(
            (8, 8), interpolation=InterpolationMode.BILINEAR, antialias=False
        )
        self.assertFalse(
            torch.equal(
                actual[0], unfiltered(frames[0].permute(2, 0, 1)).permute(1, 2, 0)
            )
        )

    def test_same_size_is_not_resampled(self):
        frame = torch.zeros((8, 8, 3), dtype=torch.uint8)
        self.assertIs(resize_file_frames([frame], (8, 8))[0], frame)

    def test_resize_rejects_wrong_dtype_or_channel_layout(self):
        for frame in (
            torch.zeros((5, 5, 3)),
            torch.zeros((3, 5, 5), dtype=torch.uint8),
        ):
            with self.assertRaises(ValueError):
                resize_file_frames([frame], (8, 8))

    def test_other_models_keep_preprocessing_defaults(self):
        config = InputConfig()
        self.assertFalse(config.file_index_sampling)
        self.assertFalse(config.file_resize_antialias)

    def test_release_adapter_declares_supplier_file_preprocessing(self):
        path = (
            Path(__file__).resolve().parents[2]
            / "src/models/custom/samples/cosmos-embed1-release/inference.py"
        )
        spec = importlib.util.spec_from_file_location("release_input_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "config.json").write_text(
                json.dumps({"num_video_frames": 8, "resolution": 448, "embed_dim": 768})
            )
            config = module.CosmosEmbedModel.get_input_config(directory)
        self.assertEqual(
            (config.num_frames, config.width, config.height), (8, 448, 448)
        )
        self.assertTrue(config.file_index_sampling)
        self.assertTrue(config.file_resize_antialias)

    def test_live_preprocessing_does_not_apply_file_resize(self):
        getter = VideoFileFrameGetter(DefaultFrameSelector(8), data_type_int8=True)
        getter._file_resize_active = True
        getter._file_resize_size = (8, 8)
        getter._is_live = True
        frames = getter._preprocess([torch.zeros((12, 15, 3), dtype=torch.uint8)])
        self.assertEqual(tuple(frames.shape), (1, 12, 15, 3))

    def test_incompatible_selector_fails_before_decoding_and_restores_owner(self):
        selector = DefaultFrameSelector(1, use_fps_for_chunking=True)
        getter = VideoFileFrameGetter(selector, file_index_sampling=True)
        with self.assertRaisesRegex(ValueError, "fixed positive"):
            getter.get_frames(
                ChunkInfo(file="unused.mp4", start_pts=0, end_pts=1_000_000_000)
            )
        self.assertIs(getter._frame_selector, selector)

    def test_decode_failure_restores_selector_and_releases_pipeline(self):
        selector = DefaultFrameSelector(8)
        getter = VideoFileFrameGetter(selector, file_index_sampling=True)
        getter.destroy_pipeline = Mock()

        def fail_decode(*_args):
            self.assertTrue(getter._frame_selector.selects_all_frames)
            raise RuntimeError("decoder failed")

        getter._create_pipeline = fail_decode
        with (
            patch(
                "vlm_pipeline.video_file_frame_getter.MediaFileInfo.get_info",
                return_value=SimpleNamespace(video_codec="H264"),
            ),
            self.assertRaisesRegex(RuntimeError, "decoder failed"),
        ):
            getter.get_frames(
                ChunkInfo(file="broken.mp4", start_pts=0, end_pts=1_000_000_000)
            )
        self.assertIs(getter._frame_selector, selector)
        self.assertFalse(getter._file_resize_active)
        getter.destroy_pipeline.assert_called_once()


if __name__ == "__main__":
    unittest.main()
