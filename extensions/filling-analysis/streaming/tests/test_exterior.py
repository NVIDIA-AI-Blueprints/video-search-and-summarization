# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from fractions import Fraction

import numpy as np
import pytest

from streaming.exterior import ExteriorHistory
from streaming.online import OnlineCycles
from streaming.tests.test_live import CALIBRATION, instance


def row(pts, seq, epoch=1, pixels=282, image=None):
    return {"connection_epoch":epoch,"frame_seq":seq,"pts":pts,"time_base":"1/90000",
            "pts_seconds":pts/90000,"t":pts/90000,"exterior_pixels":pixels,
            "received_at":"2026-09-23T00:00:00+00:00", "image":image}


@pytest.mark.parametrize("last_adjustment", [-1, 0, 1])
def test_seven_frames_allow_at_most_one_real_clock_tick(last_adjustment):
    history = ExteriorHistory(24, CALIBRATION["exterior"])
    for i in range(7):
        history.add(row(i*3750 + (last_adjustment if i == 6 else 0), i))
    burst = history.burst(1,0,1)
    assert burst and burst["observation_count"] == 7
    assert burst["timestamp_tolerance_seconds"] == pytest.approx(1/90000)
    assert burst["observed_span_seconds"] == pytest.approx(.25+last_adjustment/90000)


def test_two_ticks_short_and_six_frames_do_not_pass():
    for timestamps in [[i*3750 for i in range(6)], [i*3750-(2 if i==6 else 0) for i in range(7)]]:
        history=ExteriorHistory(24,CALIBRATION["exterior"])
        for i,pts in enumerate(timestamps): history.add(row(pts,i))
        assert history.burst(1,0,1) is None


def test_decoder_gap_and_reconnect_cannot_stitch_persistence():
    history=ExteriorHistory(24,CALIBRATION["exterior"])
    for i in [0,1,2,4,5,6]: history.add(row(i*3750,i))
    assert history.burst(1,0,1) is None
    history.clear()
    for i in range(7): history.add(row(i*3750,i,epoch=1 if i<3 else 2))
    assert history.burst(1,0,1) is None and history.burst(2,0,1) is None


def test_gpu_dropped_frames_do_not_discard_full_decoder_overflow():
    history=ExteriorHistory(24,CALIBRATION["exterior"])
    tracker=OnlineCycles(CALIBRATION,.72803,24,exterior_history=history,epoch=1)
    events=[]
    for i in range(253):
        t=i/24
        history.add(row(i*3750-(1 if i==150 else 0),i,pixels=282 if 144<=i<=150 else 0))
        # GPU queue misses both overflow endpoints; decoded exterior evidence remains complete.
        if i in {144,150}:continue
        events.extend(tracker.update(t,[instance(t)] if t<=9 else [],282 if 144<=i<=150 else 0,i))
    assert len(events)==1 and events[0]["status"]=="overflow" and events[0]["completed"]
    burst=events[0]["overflow_evidence"]
    assert burst["observation_count"]==7 and burst["sampling"]=="every-decoded-frame-before-GPU-queue"
    assert burst["observed_span_seconds"]==pytest.approx(.25-1/90000)


def test_no_fallback_from_sparse_model_samples_when_fullrate_gate_fails():
    history=ExteriorHistory(24,CALIBRATION["exterior"])
    tracker=OnlineCycles(CALIBRATION,.72803,24,exterior_history=history,epoch=1)
    events=[]
    for i in range(253):
        t=i/24
        if i!=147: history.add(row(i*3750,i,pixels=282 if 144<=i<=150 else 0))
        events.extend(tracker.update(t,[instance(t)] if t<=9 else [],282 if 144<=i<=150 else 0,i))
    assert len(events)==1 and events[0]["status"]=="normal"


def test_diagnostic_saves_only_actual_burst_frames_and_metadata(tmp_path):
    history=ExteriorHistory(24,CALIBRATION["exterior"])
    image=np.zeros((16,16,3),np.uint8)
    for i in range(7):history.add(row(i*3750,i,image=image))
    burst=history.burst(1,0,1)
    result=history.save_diagnostic(burst,tmp_path/'burst')
    assert result["available"] and result["frame_count"]==7
    assert len(list((tmp_path/'burst').glob('*.jpg')))==7
    assert (tmp_path/'burst/provenance.json').is_file()
