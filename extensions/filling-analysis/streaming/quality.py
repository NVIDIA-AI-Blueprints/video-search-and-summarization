# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Causal display quality; never replace model masks or finalized measurements."""
from __future__ import annotations

from collections import deque
from statistics import median


class CurrentQuality:
    """One track's display state, using only observations already received."""

    def __init__(self, calibration, exterior_history=None, epoch=None):
        self.calibration = calibration
        self.history, self.epoch = exterior_history, epoch
        self.rows = deque(maxlen=512)
        self.stationary = None
        self.departing = False
        self.overflow = None

    def observe(self, row, now):
        if row is not None:
            self.rows.append(row)
            while self.rows and now-self.rows[0]['t'] > 3.:
                self.rows.popleft()
            geometry = row['measurement']['geometry']
            if self.stationary:
                distance = abs(geometry['center_x']-self.stationary['center_x'])
                if distance > .35*self.stationary['width']:
                    self.departing = True
            else:
                window = [x for x in self.rows if now-x['t'] <= self.calibration['stationary_seconds_min']+.15]
                if len(window) >= 3 and window[-1]['t']-window[0]['t'] >= self.calibration['stationary_seconds_min']:
                    width = median(x['box'][2] for x in window)
                    first = [x['measurement']['geometry']['center_x'] for x in window if x['t']-window[0]['t'] <= .5]
                    last = [x['measurement']['geometry']['center_x'] for x in window if now-x['t'] <= .5]
                    speed = abs(median(last)-median(first))/(window[-1]['t']-window[0]['t'])
                    if speed <= self.calibration['stationary_speed_bottle_widths_per_second']*width:
                        self.stationary = {'start': window[0]['t'], 'center_x': median(last), 'width': width,
                            'height': median(x['box'][3] for x in window)}
        # Never consult reader observations beyond the displayed inference frame.
        # A brief missing mask can obscure the bottle during the actual burst.
        recent_presence = self.rows and now-self.rows[-1]['t'] <= .5
        if self.history is not None and self.stationary and not self.departing and recent_presence and not self.overflow:
            self.overflow = self.history.burst(self.epoch, self.stationary['start'], now)

    def assess(self, row, now):
        overflow = {'observed': self.overflow is not None,
                    'engine': 'calibrated-exterior-color-signal-v1', 'provisional': True,
                    'first_observed_pts_seconds': self.overflow['start_pts_seconds'] if self.overflow else None}
        def withheld(phase, reason):
            return {'phase': phase, 'level': None, 'confidence': 0.,
                    'quality': {'readable': False, 'reason': reason}, 'overflow': overflow}
        if self.departing:
            return withheld('departing', 'The detected bottle is leaving its established stationary position; its changing contour is not a reliable fill-height measurement.')
        if self.overflow:
            return withheld('overflow_observed', 'Persistent exterior liquid was observed for this bottle. Escaping liquid obscures the interior surface; no precise fill height is reported.')
        if row is None or abs(row['t']-now) > .000001:
            return withheld('measurement_obscured', 'No readable bottle and liquid mask is available in this frame.')
        measurement = row['measurement']
        if row['exterior_pixels'] >= self.calibration['exterior']['pixels_min']:
            return withheld('measurement_obscured', 'Exterior liquid-colored pixels are present; overflow persistence is not yet established and the interior height is withheld.')
        if self.stationary:
            # Detect a collapsed/occluded contour, not a high liquid level.
            if abs(row['box'][3]/self.stationary['height']-1.) > .15 or abs(row['box'][2]/self.stationary['width']-1.) > .25:
                return withheld('measurement_obscured', 'The detected bottle contour changed substantially from its stationary geometry.')
        if row.get('mask_quality', {}).get('liquid_outside_fraction', 0.) > .15:
            return withheld('measurement_obscured', 'A substantial part of the predicted liquid lies outside the detected bottle; the clipped boundary is not a reliable interior height.')
        if measurement.get('level') is None or measurement.get('confidence', 0.) < .65:
            return withheld('measurement_obscured', 'The RF mask boundary is unavailable or insufficiently confident: '+measurement.get('measurement_state', 'unknown')+'.')
        return {'level': measurement['level'], 'confidence': measurement['confidence'],
                'quality': {'readable': True, 'reason': 'A readable RF mask boundary is present in this frame.'}, 'overflow': overflow}
