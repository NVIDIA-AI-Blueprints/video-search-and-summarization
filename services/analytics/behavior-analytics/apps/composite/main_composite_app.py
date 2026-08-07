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

import logging

from mdx.analytics.core.app.app_base import BaseApp
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import Behavior
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.stream.state.behavior.state_management import StateMgmt
from mdx.analytics.core.stream.state.behavior.state_management_g import StateMgmtG
from mdx.analytics.core.stream.state.frame.frame_state_management import FrameStateMgmt
from mdx.analytics.core.stream.state.video_embedding.video_embedding_state_mgmt import VideoEmbeddingStateMgmt
from mdx.analytics.core.transform.calibration.calibration_dynamic import CalibrationType
from mdx.analytics.core.transform.detection.anomaly_action_detection import AnomalyActionDetection
from mdx.analytics.core.transform.detection.collision_detection import CollisionDetection
from mdx.analytics.core.transform.event.roi_event import ROIEvent
from mdx.analytics.core.transform.event.tripwire_event import TripwireEvent
from mdx.analytics.core.utils.anomaly_util import AnomalyDetector
from mdx.analytics.core.utils.crs import CoordinateReferenceSystem
from mdx.analytics.core.utils.processing_stats import BatchStats
from mdx.analytics.core.utils.schema_util import (
    group_frames_by_sensor_id,
    group_messages_by_frame_id,
    group_video_embeddings_by_sensor_id,
    messages_to_map,
    nv_frame_to_messages,
)
from mdx.analytics.core.utils.space_utilization import SpaceAnalyzer

logger = logging.getLogger(__name__)


class CompositeApp(BaseApp):
    """
    One app whose capabilities are chosen by configuration, in any combination.

    The per-profile apps each hard-wire one set of processors, so a deployment that wants
    capabilities from two of them has no entrypoint to run -- the sets are only available in the
    combinations someone happened to ship. This registers all of them and lets configuration decide,
    so any subset can run together in one process. Reproducing a shipped profile is just the case
    where the subset happens to match one.

    The knobs below are drawn from different profiles -- embedding downsampling from search, space
    estimation from warehouse-3d, trajectory clustering from smart city -- and nothing stops you
    enabling them together. Every processor defaults to **zero** workers, which
    ``register_processor`` treats as "not registered", so a deployment is exactly the set of worker
    counts it sets. Setting none is not a no-op, but nor is it loud: ``app_runner`` logs
    ``FATAL - Error in app: No processors registered``, closes its listeners and returns, so the process exits 0 and
    an unconfigured deployment looks like a clean shutdown to anything but the log.

    Processors, each enabled by its own worker count:

    ============================== ================================ =================================
    Config key                     Processor                        Writes
    ============================== ================================ =================================
    numWorkersForBehaviorCreation  behaviors, events, detections    behavior, events, anomalies,
                                                                    incidents
    numWorkersForFrameEnhancement  frame transform + frame state    frames, incidents
    numWorkersForSpaceEstimation   space utilization                space utilization
    numWorkersForEmbedFiltering    video embedding downsampling     embedFiltered
    numWorkersForBehaviorClustering trajectory clustering           behavior (clustered)
    ============================== ================================ =================================

    Not every processor works in every coordinate system, and a mismatch is silent rather than loud.
    ``numWorkersForSpaceEstimation`` needs **cartesian 3D**: it reads ``object.bbox3d``, which only
    the 3D perception path populates, and projects it with ``transform_bbox3d_to_global_rois``, which
    only ``CalibrationE`` implements. ``DynamicCalibration`` forwards that call only if the
    underlying calibration has it and returns ``{}`` otherwise -- so under image or geographic
    calibration the worker polls, transforms every message and emits nothing, without raising. Enable
    it only for cartesian 3D; elsewhere it costs a process and produces no output, and nothing at
    runtime will say so.

    Frame enhancement and behavior clustering are unconstrained -- both work in image, cartesian and
    geographic coordinates -- and embedding downsampling reads no coordinates at all: it groups
    embeddings by sensor, so it needs no calibration file. Behavior creation is unconstrained only for
    the behaviors themselves; under geographic calibration :class:`StateMgmtG` builds no trip
    behaviors, so tripwire and ROI events are never produced and the events topic stays empty however
    the calibration defines them.

    Optional stages inside behavior creation, each off unless enabled:

    * ``anomalyDetectionEnable`` -- speed/stop anomalies, written to the anomalies topic
    * ``collisionDetection.enable`` (sensor anomaly config) -- collision incidents
    * ``actionDetectionEnable`` -- pose-action incidents, and action intervals on ``behavior.info``

    .. important::
       **Run exactly one behavior producer across your deployment.** Behaviors are written by
       ``numWorkersForBehaviorCreation`` here; if another app instance also produces behaviors for the
       same sensors, every behavior is written twice from processes with independent state. This app
       does not detect that -- it is a deployment constraint, not an enforced one.

    Which streams a deployment actually produces is decided by the topics its config defines: an
    undefined destination is a disabled output, logged once by the sink and then dropped. So a
    deployment that defines ``behavior`` and ``events`` but not ``anomaly`` simply produces no
    anomalies, without needing a flag to say so. Enabling a processor and omitting its destination is
    therefore silent, not an error -- worth knowing when an expected stream is empty.

    Calibration type selects the state manager, once, at construction: geographic gets
    :class:`StateMgmtG` with map matching, everything else gets :class:`StateMgmt`, which reads the
    coordinate system per trajectory. A deployment therefore covers one coordinate system, since a
    sensor cannot be both geographic and cartesian.

    :ivar StateMgmt state_mgmt: Behavior state manager for the configured coordinate system.
    :ivar FrameStateMgmt frame_state_mgmt: Per-sensor frame state, for frame-level incidents.
    :ivar CoordinateReferenceSystem | None crs: Built only for geographic coordinates or anomaly
        detection, since constructing one loads a road-network graph.
    """

    def __init__(self, config: AppConfig, calibration_path: str | None) -> None:
        """
        :param AppConfig config: Application configuration.
        :param str | None calibration_path: Path to the calibration file, if any.
        """
        super().__init__(config, calibration_path)

        self.calibration_type = self.calibration.get_calibration_type(calibration_path)
        anomaly_enabled = self.config.get_app_config("anomalyDetectionEnable", "false").lower() == "true"

        # Building a CRS loads a road-network graph and can reach out to OpenStreetMap, so only the
        # consumers that need one pay for it: geographic state management and anomaly detection.
        self.crs = (
            CoordinateReferenceSystem(config.coordinateReferenceSystem)
            if self.calibration_type == CalibrationType.GEO or anomaly_enabled
            else None
        )

        if self.calibration_type == CalibrationType.GEO:
            self.state_mgmt = StateMgmtG(self.config, self.calibration, self.crs)  # type: ignore
        else:
            self.state_mgmt = StateMgmt(self.config, self.calibration)  # type: ignore

        self.frame_state_mgmt = FrameStateMgmt(self.config)
        self.roi_event = ROIEvent(self.config, self.calibration)
        self.tripwire_event = TripwireEvent(self.config, self.calibration)
        self.space_analyzer = SpaceAnalyzer(self.config.space_analytics, self.calibration)
        self.last_space_analyzer_invocation: float = 0
        self._vid_embed_state_mgmt = VideoEmbeddingStateMgmt(self.config.video_embedding)

        # Optional detection stages. Built only when enabled so an unused stage costs nothing.
        self.anomaly_detector = AnomalyDetector(self.config, self.calibration_type) if anomaly_enabled else None
        collision_config = self.config.get_sensor_anomaly_config().collisionDetection
        self.collision_detection = (
            CollisionDetection(collision_config, self.calibration_type) if collision_config.enable else None
        )
        self.action_detector = (
            AnomalyActionDetection(self.config)
            if self.config.get_app_config("actionDetectionEnable", "false").lower() == "true"
            else None
        )

        # Every processor is off by default; a deployment is exactly the worker counts it sets.
        self.register_processor(
            self.read_raw, self.create_behaviors,
            int(self.config.get_app_config("numWorkersForBehaviorCreation", "0")))
        self.register_processor(
            self.read_raw, self.enhance_frames,
            int(self.config.get_app_config("numWorkersForFrameEnhancement", "0")))
        self.register_processor(
            self.read_raw, self.estimate_space,
            int(self.config.get_app_config("numWorkersForSpaceEstimation", "0")))
        self.register_processor(
            self.read_embed, self.process_chunk_embeddings,
            int(self.config.get_app_config("numWorkersForEmbedFiltering", "0")))
        self.register_processor(
            self.read_behavior, self.process_behavior_clustering,
            int(self.config.get_app_config("numWorkersForBehaviorClustering", "0")))

    def create_behaviors(self, frames: list[nvSchema.Frame], stats: BatchStats) -> None:
        """
        Build behaviors from a batch of frames, then run whichever detection stages are enabled.

        The detectors enrich ``behavior_batch.active_behaviors`` in place, so their edits reach whatever is
        written -- including under ``behaviorEmitOnce``, where the behavior published when a track
        ends is the last snapshot enriched here.

        :param list[nvSchema.Frame] frames: Raw frame batch.
        :param BatchStats stats: Batch processing statistics.
        :return: None
        """
        frames = self.calibration.filter_frames_by_sensor_id(frames)
        batch_messages = [
            msg
            for frame in frames
            for msg in nv_frame_to_messages(frame, object_filter=self.config.state_mgmt_filter)
        ]

        if not batch_messages:
            logger.debug(f"Batch {stats.batch_id} - No messages to process in batch.")
            return

        logger.info(f"Batch {stats.batch_id} - Transformed {len(frames)} frame(s) to {len(batch_messages)} message(s)")

        updated_messages = [self.calibration.transform(msg) for msg in batch_messages]
        if self.calibration_type == CalibrationType.GEO:
            updated_messages = self.calibration.filter_messages_by_roi(updated_messages)

        frames_by_id = group_messages_by_frame_id(updated_messages) if self.collision_detection else {}
        behavior_batch = self.state_mgmt.process_batch(messages_to_map(updated_messages))

        # Trip behaviors exist only for sensors whose calibration defines a tripwire or an ROI, so an
        # app started without a calibration file produces no events here however it is configured.
        events = []
        for trip in behavior_batch.trip_behaviors:
            events.extend(self.tripwire_event.get_events(trip))
            events.extend(self.roi_event.get_events(trip))

        anomalies, incidents = self._detect(behavior_batch.active_behaviors, frames, frames_by_id, stats)

        logger.info(f"Batch {stats.batch_id} - Created a total of {len(behavior_batch.active_behaviors)} behavior(s), "
                    f"writing {len(behavior_batch.behaviors_to_write)}")
        logger.info(f"Batch {stats.batch_id} - Created a total of {len(events)} event(s)")

        # Written unconditionally: a destination the config does not define is a disabled output,
        # so a deployment keeps the streams it wants by defining them and silently drops the rest.
        self.write_behaviors(behavior_batch.behaviors_to_write)
        self.write_events(events)
        self.write_anomalies(anomalies)
        self.write_incidents(incidents)

    def _detect(
        self,
        behaviors: list[Behavior],
        frames: list[nvSchema.Frame],
        frames_by_id: dict,
        stats: BatchStats,
    ) -> tuple[list[Behavior], list]:
        """
        Run the enabled detection stages over this batch's behaviors.

        :param list[Behavior] behaviors: Behaviors this batch produced; enriched in place.
        :param list[nvSchema.Frame] frames: Raw frames, needed by pose-action detection.
        :param dict frames_by_id: Messages grouped by frame ID, needed by collision detection.
        :param BatchStats stats: Batch processing statistics.
        :return tuple[list[Behavior], list]: Anomalies and incidents produced.
        """
        anomalies: list[Behavior] = []
        incidents: list = []

        if self.anomaly_detector:
            self.anomaly_detector.stop_detection.update_frames(frames_by_id)
            potential_collisions, anomalies = self.anomaly_detector.detect_batch(behaviors, self.crs)
            logger.info(f"Batch {stats.batch_id} - {len(anomalies)} anomaly(s) detected")

            if self.collision_detection:
                self.collision_detection.update_behaviors(behaviors)
                self.collision_detection.update_frames(frames_by_id)
                for object_id, sensor_id, behavior, triggers in potential_collisions:
                    self.collision_detection.update_potential_collision(object_id, sensor_id, behavior, triggers)
                incidents.extend(incident for incident, _ in self.collision_detection.get_collision_alerts())

            self.anomaly_detector.stop_detection.update_live_object(self.state_mgmt.live_object_ids())

        if self.action_detector:
            action_incidents, _ = self.action_detector.detect_batch(behaviors, frames)
            incidents.extend(action_incidents)
            self.action_detector.update_live_object(self.state_mgmt.live_object_ids())

        return anomalies, incidents

    def enhance_frames(self, frames: list[nvSchema.Frame], stats: BatchStats) -> None:
        """
        Transform frames via calibration and derive frame-level incidents.

        :param list[nvSchema.Frame] frames: Raw frame batch.
        :param BatchStats stats: Batch processing statistics.
        :return: None
        """
        frames = self.calibration.filter_frames_by_sensor_id(frames)
        enhanced_frames = [self.calibration.transform_frame(frame) for frame in frames]
        self.write_frames(enhanced_frames)

        for sensor_id, sensor_frames in group_frames_by_sensor_id(enhanced_frames).items():
            self.frame_state_mgmt.update_frames(sensor_id, sensor_frames)
            incidents = self.frame_state_mgmt.get_incidents(sensor_id)
            logger.info(f"Batch {stats.batch_id} - Created a total of {len(incidents)} incident(s) "
                        f"for sensor {sensor_id}")
            self.write_incidents(incidents)

    def estimate_space(self, frames: list[nvSchema.Frame], stats: BatchStats) -> None:
        """
        Run space utilization on the recent frame window, at the configured interval.

        :param list[nvSchema.Frame] frames: Raw frame batch.
        :param BatchStats stats: Batch processing statistics.
        :return: None
        """
        frames = self.calibration.filter_frames_by_sensor_id(frames)

        for sensor_id, grouped_frames in group_frames_by_sensor_id(frames).items():
            self.frame_state_mgmt.update_frames(sensor_id, grouped_frames)

        current_time = self.space_analyzer.get_event_time_from_frames(frames)
        if current_time - self.last_space_analyzer_invocation < self.config.space_analytics.invocationIntervalSec:
            return

        self.last_space_analyzer_invocation = current_time
        frame_state = self.frame_state_mgmt.get_state()
        last_x_frames: list[nvSchema.Frame] = []

        if isinstance(frame_state, dict):
            for val in frame_state.values():
                last_x_frames.extend(val.last_x_frames)
        elif frame_state:
            last_x_frames.extend(frame_state.last_x_frames)

        messages = [msg for frame in last_x_frames for msg in nv_frame_to_messages(frame)]
        updated_messages = [self.calibration.transform(msg) for msg in messages]

        logger.info(f"Batch {stats.batch_id} - Invoking space utilization.")
        outputs_nv, _ = self.space_analyzer.analyze(messages_to_map(updated_messages), pallet_width=1.0)
        self.write_space_utilization(outputs_nv)

    def process_chunk_embeddings(self, video_embeddings: list[nvSchema.VisionLLM], stats: BatchStats) -> None:
        """
        Downsample video embeddings per sensor and write the survivors.

        :param list[nvSchema.VisionLLM] video_embeddings: Raw video embedding batch.
        :param BatchStats stats: Batch processing statistics.
        :return: None
        """
        results = []

        for sensor_id, vid_embeddings in group_video_embeddings_by_sensor_id(video_embeddings).items():
            processed = self._vid_embed_state_mgmt.update_video_embeddings(sensor_id, vid_embeddings)
            results.extend(processed)
            logger.info(f"Batch {stats.batch_id}, sensor {sensor_id} - Video embeddings: "
                        f"received={len(vid_embeddings)}, final={len(processed)}")

        self.write_embed_filtered(results)

    def process_behavior_clustering(self, behaviors: list[Behavior], _: BatchStats) -> None:
        """
        Add trajectory cluster indices to behaviors read back from the behavior stream.

        :param list[Behavior] behaviors: Behaviors read from the behavior topic.
        :param BatchStats _: Unused.
        :return: None
        """
        self.write_behaviors_with_clustering(behaviors)

    def close(self) -> None:
        """
        Write anything held back before shutting down, then release resources.

        :return: None
        """
        pending_embeddings = self._vid_embed_state_mgmt.get_pending_video_embeddings()
        if pending_embeddings:
            logger.info(f"Flushing {len(pending_embeddings)} pending video embedding(s) before shutdown.")
            self.write_embed_filtered(pending_embeddings)

        pending_behaviors = self.state_mgmt.flush_behaviors()
        if pending_behaviors:
            logger.info(f"Flushing {len(pending_behaviors)} pending behavior(s) before shutdown.")
            self.write_behaviors(pending_behaviors)

        super().close()


if __name__ == '__main__':

    from mdx.analytics.core.app.app_runner import run

    run(CompositeApp)
