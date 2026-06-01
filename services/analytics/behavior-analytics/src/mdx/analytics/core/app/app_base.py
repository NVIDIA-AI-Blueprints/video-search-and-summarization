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

from abc import ABC
from collections import defaultdict
from pydantic import BaseModel
from typing import Any
from collections.abc import Callable

from mdx.analytics.core.inference.inference_client import InferenceClient
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import Behavior, Incident
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.schema.proto import ext_pb2 as extSchema
from mdx.analytics.core.stream.sink.sink_base import StrBytesSerializer, ProtoBytesSerializer
from mdx.analytics.core.stream.sink.sink_factory import get_sink
from mdx.analytics.core.stream.source.source_base import StreamMessageProtoDeserializer
from mdx.analytics.core.stream.source.source_factory import get_source
from mdx.analytics.core.transform.calibration.calibration_dynamic import DynamicCalibration
from mdx.analytics.core.transform.config.config_applier import ConfigApplier
from mdx.analytics.core.transform.config.config_monitor import ConfigFileMonitor
from mdx.analytics.core.utils.schema_util import nv_behavior_to_behavior, convert_behavior_to_protobuf_behavior, convert_incident_to_protobuf_incident
from mdx.analytics.core.utils.processing_stats import BatchStats

logger = logging.getLogger(__name__)


class Processor(BaseModel):
    """Processor configuration model for handling stream processing workflows.
    
    Contains poller and handler functions along with worker configuration for
    processing streams of data in analytics applications.

    :ivar Callable[..., list[Any]] poller: Function to poll data from source
    :ivar Callable[[list[Any], BatchStats], None] handler: Function to handle polled data with batch statistics
    :ivar int num_workers: Number of worker threads for processing
    """

    poller: Callable[..., list[Any]]
    handler: Callable[[list[Any], BatchStats], None]
    num_workers: int = 1


class BaseApp(ABC):
    """Abstract base class for analytics applications.
    
    Provides common functionality for stream processing applications including
    calibration initialization, stream source/sink management, and data processing
    workflows for behaviors, events, incidents, and other analytics data.

    :ivar AppConfig config: Application configuration containing stream settings and parameters
    :ivar DynamicCalibration calibration: Dynamic calibration handler for the application
    :ivar StreamSink sink: Stream sink for writing output data
    :ivar StreamSource source: Stream source for reading input data
    """

    def __init__(
        self,
        config: AppConfig,
        calibration_path: str | None
    ) -> None:
        """Initialize the base application.

        :param AppConfig config: Application configuration containing stream settings and parameters
        :param str | None calibration_path: Path to calibration configuration file (optional)
        """

        self.config = config

        self.calibration = DynamicCalibration(self.config, calibration_path)
        self.calibration.start_listen()
        self.sink = get_sink(self.config)
        self.source = get_source(self.config)
        self._behavior_clustering = None
        self._processors = {}

        # ----- Dynamic-config wiring -------------------------------------
        # Mirrors calibration's split: main-process AppRunner runs the
        # ConfigListener that consumes notifications and writes files to
        # CONFIG_DIR; each worker (this BaseApp instance) runs a
        # ConfigFileMonitor that watches CONFIG_DIR and applies new files
        # via its own ConfigApplier. Main and workers run identical applier
        # logic on the same payload; results are deterministic.
        self._config_applier = ConfigApplier(self.config)
        self._config_monitor = ConfigFileMonitor(self._config_applier)
        self._config_monitor.start_listen()


    def register_processor(
        self,
        poller: Callable[..., list[Any]],
        handler: Callable[[list[Any], BatchStats], None],
        num_workers: int = 1
    ) -> None:
        """Register a processor with poller and handler functions.
        
        :param Callable[..., list[Any]] poller: Function to poll data from source
        :param Callable[[list[Any], BatchStats], None] handler: Function to handle polled data with batch statistics
        :param int num_workers: Number of worker threads for processing
        :return: None
        """

        # todo: type matching
        if num_workers > 0:
            self._processors[handler.__name__] = Processor(poller = poller, handler = handler, num_workers = num_workers)


    def get_processors(self) -> list[Processor]:
        """Get list of registered processors.
        
        :return list[Processor]: List of registered processor configurations
        """

        return list(self._processors.values())


    def write_behaviors(self, behaviors: list[Behavior]) -> None:
        """Write behavior data to the stream sink.
        
        Filters out None behaviors, optionally enriches with object recognition,
        converts to protobuf format and writes to the behavior stream.
        
        :param list[Behavior] behaviors: List of behavior objects to write
        :return: None
        """

        behaviors = list(filter(lambda b: b is not None, behaviors))
        behaviors_proto = [ convert_behavior_to_protobuf_behavior(behavior) for behavior in behaviors ]
        self.write_proto("behavior", behaviors_proto, lambda b: b.sensor.id)


    def write_events(self, events: list[Behavior]) -> None:
        """Write event data to the stream sink.
        
        Filters out None events, converts to protobuf format and writes to the events stream.
        
        :param list[Behavior] events: List of event behaviors to write
        :return: None
        """

        events = list(filter(lambda e: e is not None, events))

        self.write_proto(
            "events",
            [ convert_behavior_to_protobuf_behavior(event) for event in events ],
            lambda b: b.sensor.id
        )


    def write_incidents(self, incidents: list[Incident]) -> None:
        """Write incident data to the stream sink.
        
        Filters out None incidents, converts to protobuf format and writes to the incidents stream.
        
        :param list[Incident] incidents: List of incident objects to write
        :return: None
        """

        incidents_proto = [ convert_incident_to_protobuf_incident(incident) for incident in incidents if incident is not None ]
        self.write_proto("incidents", incidents_proto, lambda i: i.sensorId)


    def write_anomalies(self, anomalies: list[Behavior]) -> None:
        """Write anomaly data to the stream sink.
        
        Filters out None anomalies, converts to protobuf format and writes to the anomaly stream.
        
        :param list[Behavior] anomalies: List of anomaly behaviors to write
        :return: None
        """

        anomalies_proto = [ convert_behavior_to_protobuf_behavior(anomaly) for anomaly in anomalies if anomaly is not None ]
        self.write_proto("anomaly", anomalies_proto, lambda b: b.sensor.id)


    def write_space_utilization(self, output: list[extSchema.SpaceUtilization]) -> None:
        """Write space utilization data to the stream sink.
        
        :param list[extSchema.SpaceUtilization] output: List of space utilization protobuf objects to write
        :return: None
        """

        self.write_proto("spaceUtilization", output, lambda s: s.id)


    def write_frames(self, frames: list[nvSchema.Frame]) -> None:
        """Write frame data to the stream sink.
        
        :param list[nvSchema.Frame] frames: List of frame protobuf objects to write
        :return: None
        """

        self.write_proto("frames", frames, key_extractor = lambda f: f.sensorId)


    def write_embed_filtered(self, video_embeddings: list[nvSchema.VisionLLM]) -> None:
        """Write video embedding data to the stream sink.
        
        :param list[nvSchema.VisionLLM] video_embeddings: List of video embedding protobuf objects to write
        :return: None
        """

        self.write_proto("embedFiltered", video_embeddings, key_extractor = lambda v: v.sensor.id)

    def write_behaviors_with_clustering(self, behaviors: list[Behavior]) -> None:
        """Write behaviors with clustering information to the stream sink.
        
        Processes behaviors through clustering analysis and writes enriched behaviors
        with cluster indices to the behaviorPlus stream.
        
        :param list[Behavior] behaviors: List of behavior objects to cluster and write
        :return: None
        """

        if not self._behavior_clustering:
            self._behavior_clustering = BaseApp.BehaviorClustering(self.config)

        behaviors_proto_with_cluster_idx = self._behavior_clustering.get_behaviors_with_cluster_idx(behaviors)
        self.write_proto("behaviorPlus", behaviors_proto_with_cluster_idx, lambda b: b.sensor.id)


    def write_proto(self, dest_key: str, msgs: list[Any], key_extractor: Callable | None = None) -> None:
        """Write protobuf messages to a specified destination stream.
        
        :param str dest_key: Destination stream key identifier
        :param list[Any] msgs: List of protobuf messages to write
        :param Callable | None key_extractor: Optional function to extract keys from messages
        :return: None
        """

        self.sink.write(
            dest_key = dest_key,
            messages = msgs,
            value_serializer = ProtoBytesSerializer,
            key_extractor = key_extractor,
            key_serializer = StrBytesSerializer
        )

    def read_raw(self, group_id_suffix: str | None = None) -> list[nvSchema.Frame]:
        """Read raw frame data from the stream source.
        
        :param str | None group_id_suffix: Optional suffix for consumer group ID
        :return list[nvSchema.Frame]: List of raw frame protobuf objects
        """

        return self.source.poll(
            src_key = "raw",
            msg_deserializer = StreamMessageProtoDeserializer(nvSchema.Frame),
            group_id_suffix = group_id_suffix
        )


    def read_behavior(self, group_id_suffix: str | None = None) -> list[Behavior]:
        """Read behavior data from the stream source.
        
        :param str | None group_id_suffix: Optional suffix for consumer group ID
        :return list[Behavior]: List of behavior objects deserialized from protobuf
        """

        def _msg_deserializer(msg):
            behavior_proto = StreamMessageProtoDeserializer(extSchema.Behavior)(msg)
            return nv_behavior_to_behavior(behavior_proto)

        return self.source.poll(
            src_key = "behavior",
            msg_deserializer = _msg_deserializer,
            group_id_suffix = group_id_suffix
        )


    def read_events(self, group_id_suffix: str | None = None) -> list[Behavior]:
        """Read event data from the stream source.
        
        :param str | None group_id_suffix: Optional suffix for consumer group ID
        :return list[Behavior]: List of event behaviors deserialized from protobuf
        """

        def _msg_deserializer(msg):
            behavior_proto = StreamMessageProtoDeserializer(extSchema.Behavior)(msg)
            return nv_behavior_to_behavior(behavior_proto)

        return self.source.poll(
            src_key = "events",
            msg_deserializer = _msg_deserializer,
            group_id_suffix = group_id_suffix
        )


    def read_anomaly(self, group_id_suffix: str | None = None) -> list[Behavior]:
        """Read anomaly data from the stream source.
        
        :param str | None group_id_suffix: Optional suffix for consumer group ID
        :return list[Behavior]: List of anomaly behaviors deserialized from protobuf
        """

        def _msg_deserializer(msg):
            behavior_proto = StreamMessageProtoDeserializer(extSchema.Behavior)(msg)
            return nv_behavior_to_behavior(behavior_proto)

        return self.source.poll(
            src_key = "anomaly",
            msg_deserializer = _msg_deserializer,
            group_id_suffix = group_id_suffix
        )


    def read_embed(self, group_id_suffix: str | None = None) -> list[nvSchema.VisionLLM]:
        """Read raw video embedding data from the stream source.
        
        :param str | None group_id_suffix: Optional suffix for consumer group ID
        :return list[nvSchema.VisionLLM]: List of raw video embedding data protobuf objects
        """

        return self.source.poll(
            src_key = "embed",
            msg_deserializer = StreamMessageProtoDeserializer(nvSchema.VisionLLM),
            group_id_suffix = group_id_suffix
        )


    def close(self) -> None:
        """Close and cleanup all application resources.
        
        Properly closes stream source, sink, behavior clustering, and embedding
        components to release resources and connections.
        
        :return: None
        """

        for closeable in [
            self._config_monitor,
            self.source,
            self.sink,
            self._behavior_clustering,
            self.calibration,
        ]:

            if closeable and hasattr(closeable, 'close') and callable(getattr(closeable, 'close')):
                logger.info(f"Closing resources in {closeable.__class__.__name__}")
                closeable.close()


    class BehaviorClustering:
        """Behavior clustering service for grouping similar trajectory behaviors.
        
        Uses machine learning inference to cluster behaviors based on trajectory
        patterns and assigns cluster indices to behaviors.

        :ivar AppConfig _config: Application configuration containing inference settings
        :ivar int _min_trajectory_len_for_inference: Minimum trajectory length required for clustering
        :ivar InferenceClient | None _inference_client: Client for inference service
        """

        def __init__(self, config: AppConfig) -> None:
            """Initialize behavior clustering service.
            
            :param AppConfig config: Application configuration containing inference settings
            :return: None
            """
            
            self._config = config
            self._min_trajectory_len_for_inference = 20
            self._inference_client: InferenceClient | None = None


        def get_behaviors_with_cluster_idx(self, behaviors: list[Behavior]) -> list[extSchema.Behavior]:
            """Get behaviors enriched with cluster indices.
            
            Filters behaviors by minimum trajectory length, performs clustering inference,
            and returns behaviors with cluster information added.
            
            :param list[Behavior] behaviors: List of behavior objects to cluster
            :return list[extSchema.Behavior]: List of protobuf behaviors with cluster indices
            """

            sensor_behaviors: dict[str, list[extSchema.Behavior]] = defaultdict(list)

            for behavior in behaviors:
                behavior_p = convert_behavior_to_protobuf_behavior(behavior)
                if len(behavior_p.locations.coordinates) >= self._min_trajectory_len_for_inference:
                    sensor_behaviors[behavior_p.sensor.id].append(behavior_p)

            if not sensor_behaviors:
                return []

            behaviors_result: list[extSchema.Behavior] = []

            inference_client = self._get_inference_client()

            for sensor_id, behaviors_p in sensor_behaviors.items():

                if inference_client.server_ready():

                    output, modelVersion = inference_client.infer_with_protobuf(sensor_id, behaviors_p)

                    if output:
                        for i in range(len(output)):
                            behavior = behaviors_p[i]
                            cluster_index = output[i]

                            if cluster_index != i:
                                behavior.info.update({
                                    "cluster.modelVersion": modelVersion,
                                    "cluster.index": str(cluster_index),
                                })

                                behaviors_result.append(behavior)

            return behaviors_result


        def _get_inference_client(self) -> InferenceClient:
            """Get or create inference client for clustering.
            
            :return InferenceClient: Initialized inference client
            """
            if not self._inference_client:
                self._inference_client = InferenceClient(self._config.inference.url)

            return self._inference_client


        def close(self) -> None:
            """Close the inference client and release resources.
            
            :return: None
            """
            if self._inference_client:
                self._inference_client.close()
