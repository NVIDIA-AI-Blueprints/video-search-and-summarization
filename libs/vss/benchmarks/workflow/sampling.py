# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Explicit benchmark metadata overlay; native frame decoding is unchanged.

This qualifies a common sampling recipe, not the default GPU duration probe.
Only the isolated diagnostic worker registers these named adapter wrappers.
"""

import math
import re

from benchmarks.media import MAX_INPUT_BYTES, file_hash
from vss.ingestion import register_backend
from vss.ingestion.backend import CPUVLMBackend, GPUVLMBackend
from vss.video_processing import register_decoder


def prepare(profile, admission):
    if profile.sampling_contract == "backend_native":
        return profile.captioning, {"contract": "backend_native"}
    if profile.captioning not in {"cpu_vlm", "gpu_vlm"} or not isinstance(
        admission, dict
    ):
        raise ValueError("shared_sampling_admission")
    duration = admission.get("duration_s")
    if (
        admission.get("source_sha256") != profile.video_sha256
        or admission.get("selected_stream") != "v:0"
        or admission.get("duration_quality") not in {"video", "container"}
        or not re.fullmatch(r"[a-f0-9]{64}", str(admission.get("oracle_sha256", "")))
        or type(duration) not in {int, float}
        or not math.isfinite(duration)
        or not 0 < duration <= 600
        or abs(duration - profile.duration_s) > 0.1
        or any(
            type(admission.get(k)) is not int or admission[k] <= 0
            for k in ("width", "height")
        )
        or not 0 < profile.video.stat().st_size <= MAX_INPUT_BYTES
        or file_hash(profile.video) != profile.video_sha256
    ):
        raise ValueError("shared_sampling_admission")
    evidence = {
        "contract": profile.sampling_contract,
        "metadata_source": "preflight_ffprobe_v:0",
        "native_decoder": "opencv" if profile.captioning == "cpu_vlm" else "pynv",
        "instrumentation_note": "named wrapper may use unknown facade backend labels",
        "source_sha256": admission["source_sha256"],
        "oracle_sha256": admission["oracle_sha256"],
        "duration_s": duration,
        "external_duration_quality": admission["duration_quality"],
        "native_probe_observations": [],
        "default_gpu_probe_fixed": False,
    }
    if profile.captioning == "cpu_vlm":
        from vss.video_processing.decoders.opencv import OpenCVBackend as Native

        base = CPUVLMBackend
    else:
        from vss.video_processing.decoders.pynv import PyNvBackend as Native

        base = GPUVLMBackend

    class Session:
        def __init__(self, native):
            self.native = native

        def probe(self):
            facts = self.native.probe()
            if (facts.width, facts.height) != (admission["width"], admission["height"]):
                raise ValueError("shared_sampling_geometry")
            evidence["native_probe_observations"].append(
                facts.model_dump(mode="json", exclude={"source_id"})
            )
            # Keep original provenance classification. The external source and
            # original facts above distinguish this from verified native facts.
            return facts.model_copy(
                update={
                    "duration_s": duration,
                    "warnings": facts.warnings
                    + (
                        "Benchmark duration overlay from admitted FFprobe; "
                        "native duration unchanged",
                    ),
                }
            )

        def seek(self, timestamp_ns):
            return self.native.seek(timestamp_ns)

        def read_frames(self, batch_size):
            return self.native.read_frames(batch_size)

        def close(self):
            return self.native.close()

    class Backend(Native):
        def open(self, path, options):
            return Session(super().open(path, options))

    decoder_name = "m8_shared_" + profile.captioning

    class Pipeline(base):
        def __init__(self):
            super().__init__()
            # M7's fixed CPU/GPU constructors choose their native decoder.
            # This private benchmark adapter explicitly selects the admitted
            # metadata overlay after construction, preserving their VLM setup.
            self.decoder = decoder_name

    register_decoder(decoder_name, Backend, replace=True)
    register_backend(decoder_name, Pipeline)
    return decoder_name, evidence
