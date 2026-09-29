# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounded per-bottle tracks; no completed-file analysis or prerecorded schedule."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from .quality import CurrentQuality


def _reference_module():
    path = Path(__file__).parent / "reference/neural_vision.py"
    if not path.is_file():  # Repository-only unit tests; the image carries an unchanged copy.
        path = Path(__file__).parent.parent / "backend/neural_vision.py"
    spec = importlib.util.spec_from_file_location("streaming_reference_cycles", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reference = _reference_module()


class OnlineCycles:
    def __init__(self, calibration, reference_level, fps, maximum_track_seconds=90., exterior_history=None, epoch=None, cycle_number=0):
        self.calibration = calibration
        self.reference_level = reference_level
        self.fps = fps
        self.maximum_track_seconds = maximum_track_seconds
        self.track = []
        self.cycle_number = cycle_number
        self.last_frame_time = None
        self.coverage_gaps = 0
        self.exterior_history, self.epoch = exterior_history, epoch
        self.quality = CurrentQuality(calibration, exterior_history, epoch)

    def _finish(self, reason, interrupted=False):
        track, self.track = self.track, []
        self.quality = CurrentQuality(self.calibration, self.exterior_history, self.epoch)
        gaps, self.coverage_gaps = self.coverage_gaps, 0
        if not track:
            return []
        # Very short peripheral mask fragments do not establish an inspection cycle.
        if track[-1]["t"] - track[0]["t"] < .25:
            return []
        self.cycle_number += 1
        stationary = reference.stationary_observations(track, self.calibration)
        if stationary and not interrupted:
            inspection_stationary = ([{**row, "exterior_pixels": 0} for row in stationary]
                                     if self.exterior_history is not None else stationary)
            result = reference.inspect_mask_cycle(track, inspection_stationary, self.cycle_number,
                                                   self.reference_level, self.calibration, self.fps)
            if self.exterior_history is not None:
                burst = self.exterior_history.burst(self.epoch, stationary[0]["t"], stationary[-1]["t"])
                if burst:
                    result.update(status="overflow", overflow_time=burst["start"], overflow_evidence=burst,
                                  reason="Persistent exterior liquid was observed in consecutive decoded frames within the model-established stationary interval; this is the separate calibrated color signal.",
                                  evidence_start=max(track[0]["t"], burst["start"]-.75),
                                  evidence_end=min(track[-1]["t"], burst["start"]+3.),
                                  confidence=min(1., burst["minimum_exterior_pixels"]/(self.calibration["exterior"]["pixels_min"]*2)))
        else:
            result = {"id": f"cycle-{self.cycle_number:02d}", "label": f"Bottle {self.cycle_number}",
                      "start_time": track[0]["t"], "end_time": track[-1]["t"], "status": "uncertain",
                      "final_level": None, "measurement_time": None, "overflow_time": None,
                      "departure_time": None, "confidence": 0., "evidence_start": track[0]["t"],
                      "evidence_end": track[-1]["t"],
                      "reason": "Live observations did not establish a complete stationary filling cycle."}
        # Loss of input or detection is not proof of physical bottle departure.
        completed = not interrupted and result.get("departure_time") is not None
        if not completed:
            result["observed_status"] = result["status"]
            result.update(status="uncertain", final_level=None,
                          reason="Cycle incomplete: no observed departure before " + reason + ".")
        result.update(finalized=True, completed=completed, closure_reason=reason,
                      coverage_gap_count=gaps, observed_frames=len(track),
                      reference_level=self.reference_level, tolerance=self.calibration["tolerance"],
                      measurement_engine="rfdetr", overflow_engine="calibrated-exterior-color-signal-v1")
        return [result]

    def interrupt(self, reason):
        events = self._finish(reason, interrupted=True)
        self.last_frame_time = None
        return events

    def update(self, t, instances, exterior_pixels, frame_id):
        events = []
        if self.last_frame_time is not None:
            elapsed = t - self.last_frame_time
            if elapsed <= 0 or elapsed > .5:
                events.extend(self._finish("timestamp discontinuity", interrupted=True))
            elif elapsed > 1.6 / self.fps:
                self.coverage_gaps += 1
        self.last_frame_time = t
        candidates = [x for x in instances if x.get("bottle_confidence", 0) >= .4
                      and x.get("measurement", {}).get("geometry")]
        instance = max(candidates, key=lambda x: x["bottle_confidence"], default=None)
        if instance is None:
            self.quality.observe(None, t)
            if self.track and t - self.track[-1]["t"] > 1.25:
                events.extend(self._finish("bottle absent in arriving frames"))
            return events
        measurement, box = instance["measurement"], instance["box"]
        if self.track:
            previous = self.track[-1]
            jump = abs(measurement["geometry"]["center_x"] - previous["measurement"]["geometry"]["center_x"])
            if jump > max(.3, 3 * max(box[2], previous["box"][2])):
                events.extend(self._finish("new detected bottle track"))
            elif t - self.track[0]["t"] > self.maximum_track_seconds:
                events.extend(self._finish("bounded track duration exceeded", interrupted=True))
        row = {"t": t, "measurement": measurement, "box": box,
               "mask_quality": instance.get("mask_quality", {}),
               "exterior_pixels": exterior_pixels, "frame_id": frame_id}
        self.track.append(row)
        self.quality.observe(row, t)
        return events

    def current(self, now=None):
        if not self.track:
            return {"track_id": None, "phase": "waiting", "provisional": True, "level": None,
                    "confidence": 0., "reference_level": self.reference_level,
                    "quality": {"readable": False, "reason": "No active bottle track."},
                    "overflow": {"observed": False, "engine": "calibrated-exterior-color-signal-v1", "provisional": True,
                                 "first_observed_pts_seconds": None}}
        row = self.track[-1]
        now = row['t'] if now is None else now
        quality = self.quality.assess(row, now)
        level = row["measurement"].get("level")
        phase = "entering" if level is not None else "unreadable"
        recent = [x for x in self.track if row["t"] - x["t"] <= 1.]
        good = [x for x in recent if x["measurement"].get("level") is not None]
        if good and good[-1]["t"] - good[0]["t"] >= self.calibration["settled_seconds_min"]:
            levels = [x["measurement"]["level"] for x in good]
            if max(levels) - min(levels) <= self.calibration["settled_level_range_max"]:
                phase = "settled"
            elif levels[-1] - levels[0] >= self.calibration["observed_rise_min"]:
                phase = "filling"
        # Current display quality does not alter the raw track used by final inspection.
        current_measurement = {**row["measurement"], "level": quality["level"],
                               "confidence": quality["confidence"]}
        if not quality['quality']['readable']:
            current_measurement.update(surface=[], measurement_state=quality['phase'])
        return {"track_id": f"cycle-{self.cycle_number+1:02d}", "phase": phase, "provisional": True, "level": level,
                "confidence": row["measurement"].get("confidence", 0.), "reference_level": self.reference_level,
                "measurement": current_measurement, "track_start": self.track[0]["t"],
                "observed_frames": len(self.track), "coverage_gap_count": self.coverage_gaps, **quality}
