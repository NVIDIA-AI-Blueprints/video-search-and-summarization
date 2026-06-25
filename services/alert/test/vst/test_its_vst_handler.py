# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import pytest
from datetime import datetime, timezone, timedelta

from vst.its_vst_handler import ITS_VST_HANDLER


class FakeDatetime(datetime):
    fixed_now = None

    @classmethod
    def now(cls, tz=None):
        # Return a fixed, timezone-aware 'now'
        return cls.fixed_now


@pytest.fixture(autouse=True)
def fix_now(monkeypatch):
    # Freeze 'now' used inside vst.its_vst_handler
    FakeDatetime.fixed_now = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    monkeypatch.setattr("vst.its_vst_handler.datetime", FakeDatetime)


def make_cfg(anchor=None, M=10):
    cfg = {"vst_config": {
        "base_url": "http://localhost:30888",
        "sensor_list_endpoint": "/vst/api/v1/sensor/streams",
        "segment_duration_seconds": M,
    }}
    if anchor is not None:
        cfg["vst_config"]["segment_anchor"] = anchor
    return cfg


def build_handler(anchor=None, M=10):
    return ITS_VST_HANDLER(make_cfg(anchor=anchor, M=M))


def test_end_anchor_interval_gt_M():
    # segment_anchor=end, M>0, interval > M → expect [E−M, E]
    h = build_handler(anchor="end", M=10)
    s = "2025-01-02T03:03:40.000Z"  # 25s before E
    e = "2025-01-02T03:03:55.000Z"  # <= fixed now
    start_eff, end_eff = h._compute_effective_time_window(s, e)
    assert end_eff == "2025-01-02T03:03:55Z"
    assert start_eff == "2025-01-02T03:03:45Z"


def test_end_anchor_interval_le_M():
    # segment_anchor=end, M>0, interval ≤ M → expect [S, E]
    h = build_handler(anchor="end", M=10)
    s = "2025-01-02T03:03:50.000Z"
    e = "2025-01-02T03:03:55.000Z"
    start_eff, end_eff = h._compute_effective_time_window(s, e)
    assert (start_eff, end_eff) == ("2025-01-02T03:03:50Z", "2025-01-02T03:03:55Z")


def test_end_anchor_missing_start():
    # With new constraint: S and E must be provided -> expect error
    h = build_handler(anchor="end", M=10)
    s = ""  # missing/invalid start
    e = "2025-01-02T03:03:55.000Z"
    with pytest.raises(Exception):
        h._compute_effective_time_window(s, e)


def test_end_anchor_end_in_future_clamped_to_now():
    # segment_anchor=end, M>0, E in future → clamp to now; expect [now−M, now]
    h = build_handler(anchor="end", M=10)
    s = "2025-01-02T03:03:40.000Z"
    e = "2025-01-02T03:05:00.000Z"  # in the future vs fixed now
    start_eff, end_eff = h._compute_effective_time_window(s, e)
    assert end_eff == "2025-01-02T03:04:05Z"     # clamped to fixed now
    assert start_eff == "2025-01-02T03:03:55Z"   # now - 10s


def test_end_anchor_minimum_one_second_enforced():
    # segment_anchor=end, M>0, computed <1s → enforce ≥1s
    # S == E triggers available_secs = 0 → corrected to [E-1s, E]
    h = build_handler(anchor="end", M=10)
    s = "2025-01-02T03:03:55.000Z"
    e = "2025-01-02T03:03:55.000Z"
    start_eff, end_eff = h._compute_effective_time_window(s, e)
    assert (start_eff, end_eff) == ("2025-01-02T03:03:54Z", "2025-01-02T03:03:55Z")


def test_back_compat_anchor_absent_M_gt_0_defaults_to_end():
    # anchor absent, M>0 → defaults to end anchor: [E−M, E]
    h = build_handler(anchor=None, M=10)
    s = "2025-01-02T03:03:40.000Z"  # 25s before E
    e = "2025-01-02T03:03:55.000Z"  # 10s before now (03:04:05Z)
    start_eff, end_eff = h._compute_effective_time_window(s, e)
    # end-anchored: [E-10, E] = [03:03:45, 03:03:55]
    assert start_eff == "2025-01-02T03:03:45Z"
    assert end_eff == "2025-01-02T03:03:55Z"


def test_M_le_0_pass_through_with_minimum_one_second():
    # M≤0 (any anchor) → pass-through [S, E], with 1s minimum
    h = build_handler(anchor="end", M=0)
    s = "2025-01-02T03:03:50.000Z"
    e = "2025-01-02T03:03:50.000Z"  # zero-length
    start_eff, end_eff = h._compute_effective_time_window(s, e)
    assert (start_eff, end_eff) == ("2025-01-02T03:03:50Z", "2025-01-02T03:03:51Z")


def test_anchor_start_M_gt_0_start_plus_M():
    # anchor="start", M>0 → [S, S+M] when S+M <= now (no clamping needed)
    h = build_handler(anchor="start", M=10)
    s = "2025-01-02T03:03:40.000Z"  # 25s before now
    e = "2025-01-02T03:05:00.000Z"  # ignored in start mode
    start_eff, end_eff = h._compute_effective_time_window(s, e)
    # S+M = 03:03:50 which is before now (03:04:05), no clamping
    assert start_eff == "2025-01-02T03:03:40Z"
    assert end_eff == "2025-01-02T03:03:50Z"


def test_anchor_start_M_le_0_pass_through_with_clamp():
    # anchor="start", M≤0 → pass-through [S, E] with 1s minimum (no clamping in pass-through mode)
    h = build_handler(anchor="start", M=0)
    s = "2025-01-02T03:04:00.000Z"
    e = "2025-01-02T03:05:00.000Z"  # in future, but pass-through preserves original
    start_eff, end_eff = h._compute_effective_time_window(s, e)
    # Pass-through mode (M<=0) preserves original string format
    assert start_eff == s
    assert end_eff == e


def test_start_anchor_end_in_future_clamped_to_min_end_now():
    # anchor="start", M>0, S+M > now → clamp effective_end to min(end, now)
    # Fixed now = 2025-01-02T03:04:05Z
    h = build_handler(anchor="start", M=10)
    s = "2025-01-02T03:04:00.000Z"  # 5 seconds before now
    e = "2025-01-02T03:04:03.000Z"  # 2 seconds before now, so end < now
    start_eff, end_eff = h._compute_effective_time_window(s, e)
    # S+M = 03:04:10Z, but clamp to min(end, now) = min(03:04:03, 03:04:05) = 03:04:03
    assert start_eff == "2025-01-02T03:04:00Z"
    assert end_eff == "2025-01-02T03:04:03Z"  # clamped to end (since end < now)


def test_start_anchor_end_in_future_clamped_to_now():
    # anchor="start", M>0, S+M > now, end > now → clamp effective_end to now
    # Fixed now = 2025-01-02T03:04:05Z
    h = build_handler(anchor="start", M=10)
    s = "2025-01-02T03:04:00.000Z"  # 5 seconds before now
    e = "2025-01-02T03:05:00.000Z"  # in the future (end > now)
    start_eff, end_eff = h._compute_effective_time_window(s, e)
    # S+M = 03:04:10Z, but clamp to min(end, now) = min(03:05:00, 03:04:05) = 03:04:05
    assert start_eff == "2025-01-02T03:04:00Z"
    assert end_eff == "2025-01-02T03:04:05Z"  # clamped to now (since now < end)


def test_start_anchor_start_at_now_clamps_and_enforces_minimum():
    # anchor="start", M>0, S == now → effective_end clamped to now, min 1s enforced
    # Fixed now = 2025-01-02T03:04:05Z
    h = build_handler(anchor="start", M=10)
    s = "2025-01-02T03:04:05.000Z"  # exactly at now
    e = "2025-01-02T03:04:05.000Z"
    start_eff, end_eff = h._compute_effective_time_window(s, e)
    # S+M = 03:04:15Z, clamped to 03:04:05Z (now)
    # But this makes start == end, which triggers min 1s enforcement
    # _ensure_minimum_with_fixed_end moves start back 1s
    assert start_eff == "2025-01-02T03:04:04Z"  # moved back 1s for min duration
    assert end_eff == "2025-01-02T03:04:05Z"


def test_start_anchor_partial_clamp():
    # anchor="start", M>0, S+M > end < now → clamp to end
    # Fixed now = 2025-01-02T03:04:05Z
    h = build_handler(anchor="start", M=10)
    s = "2025-01-02T03:03:58.000Z"  # 7 seconds before now
    e = "2025-01-02T03:04:00.000Z"  # 5 seconds before now (end < now)
    start_eff, end_eff = h._compute_effective_time_window(s, e)
    # S+M = 03:04:08Z, min(end, now) = min(03:04:00, 03:04:05) = 03:04:00
    # So eff_end = min(S+M, capped_end) = min(03:04:08, 03:04:00) = 03:04:00
    assert start_eff == "2025-01-02T03:03:58Z"
    assert end_eff == "2025-01-02T03:04:00Z"  # clamped to end (since end < now)



def test_middle_anchor_interval_gt_M():
    # anchor="middle", M>0, interval > M → centered M-second window
    h = build_handler(anchor="middle", M=10)
    s = "2025-01-02T03:03:40.000Z"
    e = "2025-01-02T03:03:55.000Z"
    start_eff, end_eff = h._compute_effective_time_window(s, e)
    # diff=15, center floor at 47, window [42, 52]
    assert (start_eff, end_eff) == ("2025-01-02T03:03:42Z", "2025-01-02T03:03:52Z")


def test_middle_anchor_interval_le_M():
    # anchor="middle", M>0, interval ≤ M → pass-through [S, E]
    h = build_handler(anchor="middle", M=10)
    s = "2025-01-02T03:03:50.000Z"
    e = "2025-01-02T03:03:55.000Z"
    start_eff, end_eff = h._compute_effective_time_window(s, e)
    assert (start_eff, end_eff) == ("2025-01-02T03:03:50Z", "2025-01-02T03:03:55Z")


def test_middle_anchor_end_in_future_clamped_and_centered():
    # anchor="middle", M>0, E in future → clamp to now, center if possible
    h = build_handler(anchor="middle", M=10)
    s = "2025-01-02T03:03:40.000Z"
    e = "2025-01-02T03:05:00.000Z"  # future vs fixed now (03:04:05Z)
    start_eff, end_eff = h._compute_effective_time_window(s, e)
    # After clamping E=03:04:05Z, center floor at 03:03:52, window [47,57]
    assert (start_eff, end_eff) == ("2025-01-02T03:03:47Z", "2025-01-02T03:03:57Z")


def test_middle_anchor_odd_M_asymmetric_split():
    # anchor="middle", M odd → floor/ceil split across center
    h = build_handler(anchor="middle", M=9)
    s = "2025-01-02T03:03:40.000Z"
    e = "2025-01-02T03:03:59.000Z"
    start_eff, end_eff = h._compute_effective_time_window(s, e)
    # diff=19, center floor at 49, halfLow=4, window [45,54]
    assert (start_eff, end_eff) == ("2025-01-02T03:03:45Z", "2025-01-02T03:03:54Z")


# ==================== Per-alert-type anchor tests ====================

def test_per_alert_type_anchor_overrides_global_config():
    """Per-alert-type anchor should override global vst_config.segment_anchor."""
    # Global config has "end" anchor
    h = build_handler(anchor="end", M=10)
    s = "2025-01-02T03:03:40.000Z"
    e = "2025-01-02T03:03:55.000Z"

    # Without per-alert-type override: uses global "end" anchor
    start_eff_global, end_eff_global = h._compute_effective_time_window(s, e)
    assert end_eff_global == "2025-01-02T03:03:55Z"
    assert start_eff_global == "2025-01-02T03:03:45Z"  # end-anchored: [E-10, E]

    # With per-alert-type override: uses "start" anchor instead
    start_eff_override, end_eff_override = h._compute_effective_time_window(
        s, e, alert_type_anchor="start"
    )
    assert start_eff_override == "2025-01-02T03:03:40Z"  # start-anchored: preserves original start (normalized)
    assert end_eff_override == "2025-01-02T03:03:50Z"  # [S, S+10]


def test_per_alert_type_anchor_end_override():
    """Per-alert-type 'end' anchor should work when passed explicitly."""
    # Global config has "start" anchor
    h = build_handler(anchor="start", M=10)
    s = "2025-01-02T03:03:40.000Z"
    e = "2025-01-02T03:03:55.000Z"

    # Override with "end" anchor
    start_eff, end_eff = h._compute_effective_time_window(s, e, alert_type_anchor="end")
    assert end_eff == "2025-01-02T03:03:55Z"
    assert start_eff == "2025-01-02T03:03:45Z"  # end-anchored: [E-10, E]


def test_per_alert_type_anchor_middle_override():
    """Per-alert-type 'middle' anchor should work when passed explicitly."""
    # Global config has "start" anchor
    h = build_handler(anchor="start", M=10)
    s = "2025-01-02T03:03:40.000Z"
    e = "2025-01-02T03:03:55.000Z"

    # Override with "middle" anchor
    start_eff, end_eff = h._compute_effective_time_window(s, e, alert_type_anchor="middle")
    # diff=15, center floor at 47, window [42, 52]
    assert (start_eff, end_eff) == ("2025-01-02T03:03:42Z", "2025-01-02T03:03:52Z")


def test_per_alert_type_anchor_none_falls_back_to_global():
    """When per-alert-type anchor is None, should fall back to global config."""
    h = build_handler(anchor="end", M=10)
    s = "2025-01-02T03:03:40.000Z"
    e = "2025-01-02T03:03:55.000Z"

    # Explicitly pass None - should use global "end" anchor
    start_eff, end_eff = h._compute_effective_time_window(s, e, alert_type_anchor=None)
    assert end_eff == "2025-01-02T03:03:55Z"
    assert start_eff == "2025-01-02T03:03:45Z"  # end-anchored


def test_per_alert_type_anchor_case_insensitive():
    """Per-alert-type anchor should be case-insensitive."""
    h = build_handler(anchor="start", M=10)
    s = "2025-01-02T03:03:40.000Z"
    e = "2025-01-02T03:03:55.000Z"

    # Test uppercase "END"
    start_eff, end_eff = h._compute_effective_time_window(s, e, alert_type_anchor="END")
    assert end_eff == "2025-01-02T03:03:55Z"
    assert start_eff == "2025-01-02T03:03:45Z"  # end-anchored

    # Test mixed case "Middle"
    start_eff2, end_eff2 = h._compute_effective_time_window(s, e, alert_type_anchor="Middle")
    # diff=15, center floor at 47, window [42, 52]
    assert (start_eff2, end_eff2) == ("2025-01-02T03:03:42Z", "2025-01-02T03:03:52Z")


def test_per_alert_type_anchor_invalid_falls_back_to_end():
    """Invalid per-alert-type anchor should fall back to 'end'."""
    h = build_handler(anchor=None, M=10)  # No global config
    s = "2025-01-02T03:03:40.000Z"
    e = "2025-01-02T03:03:55.000Z"

    # Pass invalid anchor - should fall back to "end"
    start_eff, end_eff = h._compute_effective_time_window(s, e, alert_type_anchor="invalid")
    assert start_eff == "2025-01-02T03:03:45Z"  # end-anchored: [E-10, E]
    assert end_eff == "2025-01-02T03:03:55Z"


def test_determine_anchor_and_duration_with_override():
    """_determine_anchor_and_duration should use per-alert-type anchor when provided."""
    h = build_handler(anchor="end", M=10)

    # Without override - uses global
    anchor, duration = h._determine_anchor_and_duration()
    assert anchor == "end"
    assert duration == 10

    # With override - uses per-alert-type
    anchor_override, duration_override = h._determine_anchor_and_duration(alert_type_anchor="start")
    assert anchor_override == "start"
    assert duration_override == 10

    # With None override - falls back to global
    anchor_none, duration_none = h._determine_anchor_and_duration(alert_type_anchor=None)
    assert anchor_none == "end"
    assert duration_none == 10
