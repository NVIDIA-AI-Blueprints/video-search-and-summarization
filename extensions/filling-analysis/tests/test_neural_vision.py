# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Decision tests use generated masks/motion, never acceptance labels or times."""
import json
import unittest
from copy import deepcopy
from unittest.mock import patch

import numpy as np

from segmentation.measurements import measure_masks
from backend.neural_vision import (CALIBRATION_PATH, analyze_segmentation,
    associate_tracks, inspect_mask_cycle, stationary_observations)


def obs(t,level,center=.5,exterior=0,confidence=.95):
    return {'t':t,'measurement':{'level':level,'confidence':confidence,
        'geometry':{'center_x':center}},'box':[center-.1,.1,.2,.8],'exterior_pixels':exterior}


class NeuralMeasurementTests(unittest.TestCase):
    def setUp(self):
        self.b=np.zeros((200,200),bool);self.b[20:180,60:140]=True
        self.l=np.zeros_like(self.b);self.l[100:180,60:140]=True
        self.cal=json.loads(CALIBRATION_PATH.read_text())

    def test_height_uses_detected_geometry(self):
        r=measure_masks(self.b,self.l,.9,.95)
        self.assertAlmostEqual(r['level'],79/159,places=5)
        shifted=measure_masks(np.roll(self.b,10,axis=0),np.roll(self.l,10,axis=0),.9,.95)
        self.assertEqual(r['level'],shifted['level'])

    def test_missing_mask_is_unknown_not_empty(self):
        self.assertIsNone(measure_masks(self.b,np.zeros_like(self.b),.9,.9)['level'])
        self.assertIsNone(measure_masks(np.zeros_like(self.b),self.l,.9,.9)['level'])

    def test_jet_fragment_does_not_set_surface(self):
        self.l[25:100,99:101]=True
        self.assertAlmostEqual(measure_masks(self.b,self.l,.9,.95)['level'],79/159,places=5)

    def test_frame_edge_partial_and_low_confidence_unknown(self):
        b=self.b.copy();b[:,0]=1
        self.assertIsNone(measure_masks(b,self.l,.9,.9)['level'])
        self.assertIsNone(measure_masks(self.b,self.l,.9,.1)['level'])

    def cycle(self,final=.4,departure=True,exterior=False):
        stationary=[obs(i/4,.1+i*.05 if i<6 else final,exterior=300 if exterior and i>=9 else 0) for i in range(18)]
        track=stationary+[obs(4.5,final,.62)] if departure else stationary
        return inspect_mask_cycle(track,stationary,1,.73,self.cal,4)

    def test_settled_low_requires_departure_and_prior_rise(self):
        self.assertEqual(self.cycle()['status'],'underfill')
        self.assertEqual(self.cycle(departure=False)['status'],'uncertain')
        stationary=[obs(i/4,.3) for i in range(18)]
        r=inspect_mask_cycle(stationary+[obs(4.5,.3,.62)],stationary,1,.73,self.cal,4)
        self.assertEqual(r['status'],'uncertain')

    def test_low_while_still_rising_not_underfill(self):
        stationary=[obs(i/4,.1+i*.01) for i in range(18)]
        r=inspect_mask_cycle(stationary+[obs(4.5,.3,.62)],stationary,1,.73,self.cal,4)
        self.assertEqual(r['status'],'uncertain')

    def test_high_fill_does_not_imply_overflow(self):
        self.assertEqual(self.cycle(final=.99)['status'],'normal')
        self.assertEqual(self.cycle(final=.4,exterior=True)['status'],'overflow')

    def test_single_exterior_glint_does_not_become_overflow(self):
        stationary=[obs(i/4,.1+i*.05 if i<6 else .4,exterior=500 if i==12 else 0) for i in range(18)]
        r=inspect_mask_cycle(stationary+[obs(4.5,.4,.62)],stationary,1,.73,self.cal,4)
        self.assertEqual(r['status'],'underfill')

    def test_missing_final_masks_do_not_copy_earlier_level(self):
        stationary=[obs(i/4,.1+i*.05 if i<6 else None) for i in range(18)]
        r=inspect_mask_cycle(stationary+[obs(4.5,None,.62)],stationary,1,.73,self.cal,4)
        self.assertEqual(r['status'],'uncertain');self.assertIsNone(r['final_level'])

    def test_cycle_detection_is_time_shift_invariant(self):
        sequence=[obs(i/4,.3,.2+i*.05 if i<6 else .5 if i<25 else .5+(i-24)*.05) for i in range(32)]
        shifted=deepcopy(sequence)
        for o in shifted:o['t']+=123
        a=stationary_observations(sequence,self.cal);b=stationary_observations(shifted,self.cal)
        self.assertGreater(len(a),5)
        self.assertEqual([o['t']+123 for o in a],[o['t'] for o in b])
        self.assertEqual(len(associate_tracks(sequence+shifted)),2)

    def test_dense_24fps_rise_and_completion(self):
        stationary=[obs(i/24,min(.7,.1+i/24*.3)) for i in range(120)]
        r=inspect_mask_cycle(stationary+[obs(5.,.7,.62)],stationary,1,.73,self.cal,24)
        self.assertEqual(r['status'],'normal')
        self.assertAlmostEqual(r['final_level'],.7)

    def test_short_gap_and_no_seeded_cycle_count(self):
        first=[obs(i/4,.3) for i in range(24)]
        second=[obs(30+i/4,.3) for i in range(24)]
        self.assertEqual(len(associate_tracks(first)),1)
        self.assertEqual(len(associate_tracks(first+second)),2)

    def test_wrong_source_rejected_before_video_decode(self):
        with patch('backend.neural_vision._sha',return_value='f'*64):
            with self.assertRaisesRegex(ValueError,'SHA does not match'):
                analyze_segmentation({'source_sha256':'0'*64},'ignored.mp4')

    def test_unreviewed_source_rejected_before_video_decode(self):
        with patch('backend.neural_vision._sha',return_value='f'*64):
            with self.assertRaisesRegex(ValueError,'approved only'):
                analyze_segmentation({'source_sha256':'f'*64},'ignored.mp4')


if __name__=='__main__':unittest.main()
