# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy

import pytest

from streaming.exterior import ExteriorHistory
from streaming.online import OnlineCycles
from streaming.tests.test_live import CALIBRATION, instance
from streaming.tests.test_exterior import row


def tracker():
    history = ExteriorHistory(24, CALIBRATION['exterior'])
    return OnlineCycles(CALIBRATION, .72803, 24, exterior_history=history, epoch=1), history


def observe(tracker, history, i, pixels=0, observed=None):
    history.add(row(i*3750, i, pixels=pixels))
    tracker.update(i/24, [instance(i/24)] if observed is None else observed, pixels, i)
    return tracker.current(i/24)


def test_precise_high_height_is_withheld_on_first_exterior_observation():
    t, history = tracker()
    for i in range(144): observe(t, history, i)
    high = instance(6); high['measurement']['level'] = .94
    current = observe(t, history, 144, 282, [high])
    assert current['phase'] == 'measurement_obscured'
    assert current['level'] is None and current['measurement']['level'] is None
    assert not current['overflow']['observed']  # One frame is not an overflow verdict.
    assert t.track[-1]['measurement']['level'] == .94  # No clamping/smoothing of actual model output.


def test_live_overflow_is_causal_latched_and_not_counted_until_departure():
    t, history = tracker()
    for i in range(144): observe(t, history, i)
    # Reader is ahead of the inference frame. Future exterior evidence must not leak.
    for i in range(144,151): history.add(row(i*3750,i))
    t.update(6.,[instance(6)],282,144)
    assert not t.current(6.)['overflow']['observed']
    t.update(6.25,[instance(6.25)],282,150)
    current=t.current(6.25)
    assert current['phase']=='overflow_observed' and current['level'] is None
    assert current['overflow']['first_observed_pts_seconds']==6.
    assert t.cycle_number==0 and current['track_id']=='cycle-01'
    current=observe(t,history,151,0,[])
    assert current['phase']=='overflow_observed' and current['track_id']=='cycle-01'
    assert t.interrupt('connection lost')[0]['completed'] is False
    assert t.current()['overflow']['observed'] is False


def test_normal_measurement_remains_exact_but_departure_has_no_percentage():
    t,history=tracker()
    for i in range(193): current=observe(t,history,i)
    assert current['quality']['readable'] and current['level']==.72803
    for i in range(193,213): current=observe(t,history,i,1000)
    assert current['phase']=='departing' and current['level'] is None
    assert not current['overflow']['observed']  # Bottle moving through exterior ROI is not a spill.


@pytest.mark.parametrize('defect', ['missing','low_confidence','collapsed_contour','outside_liquid'])
def test_unreliable_current_masks_do_not_publish_a_precise_height(defect):
    t,history=tracker()
    for i in range(144):observe(t,history,i)
    bad=copy.deepcopy(instance(6))
    if defect=='missing':bad['measurement']['level']=None
    elif defect=='low_confidence':bad['measurement']['confidence']=.4
    elif defect=='collapsed_contour':bad['box'][3]*=.6
    else:bad['mask_quality']={'liquid_outside_fraction':.3}
    current=observe(t,history,144,0,[bad])
    assert current['phase']=='measurement_obscured' and current['level'] is None
    assert not current['quality']['readable'] and current['track_id']=='cycle-01'
    assert t.cycle_number==0


def test_cycle_number_continues_across_new_epoch_without_old_overflow():
    t=OnlineCycles(CALIBRATION,.72803,24,cycle_number=305)
    t.update(0,[instance(0)],0,1)
    assert t.current(0)['track_id']=='cycle-306'
    assert not t.current(0)['overflow']['observed']
