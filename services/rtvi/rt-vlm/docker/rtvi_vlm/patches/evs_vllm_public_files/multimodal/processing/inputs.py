# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
from collections.abc import Mapping
from dataclasses import dataclass, field

from vllm.logger import init_logger

from ..hasher import MultiModalHasher
from ..inputs import MultiModalHashes
from ..parse import MultiModalDataItems, MultiModalUUIDItems

logger = init_logger(__name__)


@dataclass
class ProcessorInputs:
    """
    Represents the keyword arguments to
    [`vllm.multimodal.processing.BaseMultiModalProcessor.apply`][].
    """

    prompt: str | list[int]
    mm_data_items: MultiModalDataItems
    mm_uuid_items: MultiModalUUIDItems | None = None
    hf_processor_mm_kwargs: Mapping[str, object] = field(default_factory=dict)
    tokenization_kwargs: Mapping[str, object] = field(default_factory=dict)

    # Keys excluded from mm_hash when a UUID is explicitly provided.
    # In disaggregated EPD, the encode (E) and prefill-decode (PD)
    # instances pass different EVS-specific kwargs but the content is
    # the same.  Excluding these keys ensures E and PD produce the
    # same mm_hash for ECConnector cache matching.
    _UUID_HASH_EXCLUDED_KEYS = frozenset(
        {
            "evs_stream_id",
            "evs_actual_total_tokens",
            "num_tokens_per_frame",
            "evs_timestamps",
            "do_sample_frames",
            "size",
        }
    )

    def get_mm_hashes(self, model_id: str) -> MultiModalHashes:
        mm_data_items = self.mm_data_items
        mm_uuid_items = self.mm_uuid_items or {}
        hf_processor_mm_kwargs = self.hf_processor_mm_kwargs

        mm_hashes: MultiModalHashes = {}
        hasher = MultiModalHasher

        for modality, data_items in mm_data_items.items():
            if modality in mm_uuid_items:
                uuid_items = mm_uuid_items[modality]

                # For None entries, compute a hash; otherwise, use provided ID.
                hashes: list[str] = []
                for i, item in enumerate(data_items.get_all_items_for_hash()):
                    uuid_item = uuid_items[i]

                    # NOTE: Even if a uuid_item is provided, we still compute a hash
                    # if `hf_processor_mm_kwargs` is provided.
                    # This is because the processed multimodal inputs can be different
                    # depending on the processor kwargs.
                    #
                    # When a UUID IS provided, exclude EVS-specific keys that
                    # differ between encode and generate instances but don't
                    # change the actual multimodal content.
                    if uuid_item is not None:
                        hash_kwargs = {
                            k: v
                            for k, v in hf_processor_mm_kwargs.items()
                            if k not in self._UUID_HASH_EXCLUDED_KEYS
                        }
                    else:
                        hash_kwargs = dict(hf_processor_mm_kwargs)

                    if uuid_item is None or hash_kwargs:
                        # NOTE: use provided hash string to hash with kwargs
                        # if available for better performance.
                        item = uuid_item if uuid_item is not None else item
                        computed = hasher.hash_kwargs(
                            model_id=model_id,
                            **{modality: item},
                            **hash_kwargs,
                        )
                        hashes.append(computed)
                    else:
                        hashes.append(uuid_item)

                mm_hashes[modality] = hashes
            else:
                mm_hashes[modality] = [
                    hasher.hash_kwargs(
                        model_id=model_id,
                        **{modality: item},
                        **hf_processor_mm_kwargs,
                    )
                    for item in data_items
                ]

        return mm_hashes
