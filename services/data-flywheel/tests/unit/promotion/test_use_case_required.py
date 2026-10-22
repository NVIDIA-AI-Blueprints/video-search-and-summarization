# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Every promotion record carries a use case, so nothing downstream has to guess.

Two sibling use cases share one ``promotions`` collection, and a record with no
use case would be graded by whatever the reader defaults to — the AOI FAR
evaluator scoring ITS detection data, with no downstream signal that anything is
wrong. Every layer now fails closed on that shape, and each is pinned below:

* WRITE side — the field is required with no default, so constructing a record
  without one RAISES. No document can be written without one.
* READ side — ``from_mongo`` applies no shim. A document missing the field fails
  validation loudly instead of silently becoming "aoi". Deployments are always
  fresh, so such a document is a writer bug, not an old row to be repaired.
* BEAT side — the online-eval poll skips a use-case-less record with an ERROR
  rather than defaulting, so no verdict is ever written from the wrong
  evaluator.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.api.promotion.models import PromotionRecord


# ----------------------------------------------------------------------
# WRITE side — the constructor refuses outright
# ----------------------------------------------------------------------
def test_constructing_a_record_without_a_use_case_raises():
    with pytest.raises(ValidationError):
        PromotionRecord(model_name="m", candidate_version="2")


def test_an_empty_use_case_is_refused_too():
    # Empty is worse than absent: a reader testing only for the KEY would find
    # "" present and flow on with a use case that resolves to no evaluator,
    # while the field looks stated on the record. min_length=1 is what makes the
    # two shapes fail the same way.
    with pytest.raises(ValidationError):
        PromotionRecord(model_name="m", candidate_version="2", use_case="")


def test_a_stated_use_case_is_preserved_verbatim():
    # The guard that matters regardless: nothing normalizes or rewrites a use
    # case that WAS stated.
    record = PromotionRecord(model_name="m", candidate_version="2", use_case="its")
    assert record.use_case == "its"
    assert record.to_mongo()["use_case"] == "its"


# ----------------------------------------------------------------------
# READ side — no shim: a document with no use case is rejected, not guessed
# ----------------------------------------------------------------------
def test_from_mongo_rejects_a_document_with_no_use_case():
    # The contract this file exists to pin. Defaulting here would hand an ITS
    # record to the AOI evaluator with nothing anywhere saying so; raising makes
    # the writer bug impossible to miss.
    with pytest.raises(ValidationError):
        PromotionRecord.from_mongo(
            {"promotion_id": "promo-no-use-case", "model_name": "m", "candidate_version": "2"}
        )


def test_from_mongo_rejects_a_blank_use_case_too():
    # Same asymmetry as the write side: present-but-blank must fail exactly like
    # absent, or the "did you state it?" check is trivially satisfiable.
    with pytest.raises(ValidationError):
        PromotionRecord.from_mongo(
            {
                "promotion_id": "promo-blank",
                "model_name": "m",
                "candidate_version": "2",
                "use_case": "",
            }
        )


def test_from_mongo_never_overwrites_a_stated_use_case():
    record = PromotionRecord.from_mongo(
        {"model_name": "m", "candidate_version": "2", "use_case": "its"}
    )
    assert record is not None
    assert record.use_case == "its"


def test_from_mongo_still_returns_none_for_an_empty_document():
    # The empty-doc path is a "nothing found" signal from the caller's find(),
    # not a malformed record — it must stay None rather than start raising now
    # that the use_case shim is gone.
    assert PromotionRecord.from_mongo({}) is None
