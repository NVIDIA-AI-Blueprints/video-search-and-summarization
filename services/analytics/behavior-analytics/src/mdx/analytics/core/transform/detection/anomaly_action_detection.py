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

from datetime import datetime
from mdx.analytics.core.schema.config import (
    AppConfig,
    FallRiskConfig,
    LackMovementConfig,
)

from mdx.analytics.core.schema.models import Behavior, AnalyticsModule, Action, Incident, Place, Location, Coordinate
from mdx.analytics.core.schema.action.action_state import ActionState, FallRiskState
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.utils.schema_util import nv_action_to_action, get_timestamp_from_proto_ts
from enum import Enum
import json
import logging

logger = logging.getLogger(__name__)

class ActionType(Enum):
    LYING_DOWN = "Lying Down"
    STANDING = "Standing"
    SITTING = "Sitting"
    WALKING = "Walking"

class AnomalyActionDetection:
    """
    Module to detect anomalous actions and behaviors in video streams.

    This class provides functionality to:
    - Detect fall risk conditions based on pose and movement patterns
    - Monitor lack of movement over time periods
    - Track action states for objects across sensors
    - Generate anomaly alerts for detected conditions

    The class maintains state for:
    - Action history per sensor/object
    - Fall risk assessment state
    - Configuration settings for anomaly detection thresholds

    :ivar AppConfig app_config: Configuration object containing anomaly detection settings
    :ivar dict[str, dict[str, ActionState]] action_state: Nested dict mapping sensor_id -> object_id -> ActionState
    :ivar dict[str, dict[str, FallRiskState]] fall_risk_state: Nested dict mapping sensor_id -> object_id -> FallRiskState

    Examples::
        >>> config = AppConfig()
        >>> behavior_state_manager = StateMgmtIWithPose(config)
        >>> detector = AnomalyActionDetection(config)
        >>> behaviors = [
            behavior_state_manager.update_behavior(k, v) for k, v in updated_messages_map.items()
        ]
        >>> anomalies = detector.detect_batch(behaviors)
        >>> print(f"Detected {len(anomalies)} anomalies")
        >>> live_objects = behavior_state_manager.state.keys()
        >>> detector.update_live_object(live_objects)
    """
    def __init__(self, config: AppConfig) -> None:
        """
        Initialize the AnomalyActionDetection module.

        :param AppConfig config: Configuration object containing anomaly detection settings
        :return: None
        """
        self.app_config = config
        self.action_state = {}
        self.fall_risk_state = {}

    def detect_batch(
        self, list_of_behavior: list[Behavior], frames: list[nvSchema.Frame]
    ) -> tuple[list[Incident], list[Behavior]]:
        """
        Detect anomalies in a batch of behaviors.

        :param list[Behavior] list_of_behavior: List of behaviors to detect anomalies in
        :param list[nvSchema.Frame] frames: List of frames to process for anomaly detection
        :return tuple[list[Incident], list[Behavior]]: List of incidents with detected anomalies

        Examples::
            >>> config = AppConfig()
            >>> detector = AnomalyActionDetection(config)
            >>> behaviors = [
                behavior_state_manager.update_behavior(k, v) for k, v in updated_messages_map.items()
            ]
            >>> anomalies, behaviors = detector.detect_batch(behaviors, frames)
            >>> print(f"Detected {len(anomalies)} anomalies")
        """
        # Sort frames by timestamp using ToMilliseconds() for comparison
        frames.sort(key=lambda x: x.timestamp.ToMilliseconds())
        incidents_all = []
        for frame in frames:
            for obj in frame.objects:
                sensor_id = frame.sensorId
                object_id = obj.id
                object_type = obj.type
                if getattr(obj, "pose", None) and obj.pose.actions:
                    proto_action = obj.pose.actions[-1]
                    model_action = nv_action_to_action(proto_action)
                    timestamp = get_timestamp_from_proto_ts(frame.timestamp)
                    incidents = self.detect(sensor_id, object_id, model_action, timestamp, object_type)
                    incidents_all.extend(incidents)
        places = {}
        for behavior in list_of_behavior:
            action_intervals = []
            if behavior.sensor.id not in places:
                places[behavior.sensor.id] = {}
            if behavior.object.id not in places[behavior.sensor.id]:
                places[behavior.sensor.id][behavior.object.id] = behavior.place

            last_action = "Unknown"
            if behavior.sensor.id in self.action_state and behavior.object.id in self.action_state[behavior.sensor.id]:
                last_action = self.action_state[behavior.sensor.id][behavior.object.id].get_last_action()
                durations = self.action_state[behavior.sensor.id][behavior.object.id].get_durations()
                for action, intervals in self.action_state[behavior.sensor.id][behavior.object.id].get_action_intervals().items():
                    duration = durations[action]
                    action_intervals.append({
                        "action": action,
                        "duration_seconds": duration,
                        "intervals": [[start.timestamp(), end.timestamp()] for start, end in intervals]
                    })
            behavior.info["action_intervals"] = json.dumps(action_intervals)
            behavior.info["current_action"] = last_action
        
        for incident in incidents_all:
            if incident.sensorId in places and incident.objectIds[0] in places[incident.sensorId]:
                incident.place = places[incident.sensorId][incident.objectIds[0]]
            
        return incidents_all, list_of_behavior

    def create_incident(
        self,
        sensor_id: str,
        object_id: str,
        start_timestamp: datetime,
        end_timestamp: datetime,
        analytics_module: AnalyticsModule,
        incident_type: str,
    ) -> Incident:
        """
        Create an incident from a behavior.

        :param str sensor_id: ID of the sensor
        :param str object_id: ID of the object
        :param datetime start_timestamp: Start timestamp of the incident
        :param datetime end_timestamp: End timestamp of the incident
        :param AnalyticsModule analytics_module: Analytics module for the incident
        :param str incident_type: Type of the incident
        :return Incident: Incident object
        """
        incident = Incident(
            timestamp=start_timestamp,
            end=end_timestamp,
            sensorId=sensor_id,
            objectIds=[object_id],
            place=Place(
                id="",
                name="",
                type="",
                location=Location(
                    lat=0,
                    lon=0,
                    alt=0,
                ),
                coordinate=Coordinate(
                    x=0,
                    y=0,
                    z=0,
                ),
                info={},
            ),
            analyticsModule=analytics_module,
            category=incident_type,
            isAnomaly=True,
            info={
                "primary_object_id": object_id,
            },
            frameIds=[],
        )

        return incident


    def detect(
        self, 
        sensor_id: str, 
        object_id: str, 
        action: Action, 
        timestamp: datetime, 
        object_type: str
    ) -> list[Incident]:
        """
        Detect anomalies in a single behavior.

        :param str sensor_id: ID of the sensor
        :param str object_id: ID of the object
        :param Action action: Action of the object
        :param datetime timestamp: Timestamp of the object
        :param str object_type: Type of the object
        :return list[Incident]: List of incidents with detected anomalies

        Examples::
            >>> config = AppConfig()
            >>> detector = AnomalyActionDetection(config)
            >>> anomalies = detector.detect(behavior)
            >>> print(f"Detected {len(anomalies)} anomalies")
        """

        incidents = []
        sensor_anomaly_action_config = self.app_config.get_sensor_anomaly_action_config(sensor_id)
       
        if (object_type in sensor_anomaly_action_config.classes) and (
            sensor_id not in sensor_anomaly_action_config.ignoreSensors
        ):
            
            fall_risk_flag, fall_risk_description = self.detect_fall_risk(
                sensor_id, object_id, action, timestamp, sensor_anomaly_action_config.fallRisk
            )
            
            lack_movement_flag, lack_movement_description = self.detect_lack_movement(
                sensor_id, object_id, action, sensor_anomaly_action_config.lackMovement
            )
            
            self.update_action(sensor_id, object_id, action, timestamp, sensor_anomaly_action_config.actionThreshold)
            
            if fall_risk_flag:
                analytics_module = AnalyticsModule(
                    id="Fall Risk Anomaly Module",
                    description=fall_risk_description,
                    source="mdx",
                    version="3.0",
                    info={"clusterIndex": "-1"},
                )
                if sensor_id in self.fall_risk_state and object_id in self.fall_risk_state[sensor_id]:
                    start_time = self.fall_risk_state[sensor_id][object_id].get_start_time()
                    end_time = self.fall_risk_state[sensor_id][object_id].get_end_time()
                    incident = self.create_incident(sensor_id, object_id, start_time, end_time, 
                                                analytics_module, "fall_risk")
                    incidents.append(incident)
                else:
                    logger.warning(f"Fall risk state not found for {sensor_id}#{object_id}")
                

            if lack_movement_flag:
                analytics_module = AnalyticsModule(
                    id="Lack Movement Anomaly Module",
                    description=lack_movement_description,
                    source="mdx",
                    version="3.0",
                    info={"clusterIndex": "-1"},
                )
                if sensor_id in self.action_state and object_id in self.action_state[sensor_id]:
                    last_interval = self.action_state[sensor_id][object_id].get_last_interval(ActionType.LYING_DOWN.value)
                    if last_interval:
                        incident = self.create_incident(sensor_id, object_id, last_interval[0], last_interval[1], 
                                                        analytics_module, "lack_movement")
                        incidents.append(incident)
                    else:
                        logger.warning("Lack-movement flagged but no interval found for %s#%s", sensor_id, object_id)
                else:
                    logger.warning(f"Action state not found for {sensor_id}#{object_id}")

        return incidents

    def update_action(
        self,
        sensor_id: str,
        object_id: str,
        action: Action,
        timestamp: datetime,
        action_threshold: float
    ) -> None:
        """
        Update the action state for a specific sensor/object.

        :param str sensor_id: ID of the sensor
        :param str object_id: ID of the object
        :param Action action: Action to update the state for
        :param datetime timestamp: Timestamp of the action
        :return: None

        Examples::
            >>> config = AppConfig()
            >>> detector = AnomalyActionDetection(config)
            >>> sensor_id = "sensor1"
            >>> object_id = "object1"
            >>> action = Action(type="Lying Down", confidence=0.95)
            >>> timestamp = datetime.now()
            >>> detector.update_action(sensor_id, object_id, action, timestamp, action_threshold)
        """
        if sensor_id not in self.action_state:
            self.action_state[sensor_id] = {}
        if object_id not in self.action_state[sensor_id]:
            self.action_state[sensor_id][object_id] = ActionState(sensor_id, object_id, action_threshold)
        self.action_state[sensor_id][object_id].update_action(action, timestamp)

    def remove_sensor(self, sensor_id: str) -> None:
        """
        Remove a sensor from the action state.

        :param str sensor_id: ID of the sensor to remove
        :return: None
        """
        if sensor_id in self.action_state:
            del self.action_state[sensor_id]
        if sensor_id in self.fall_risk_state:
            del self.fall_risk_state[sensor_id]

    def remove_object(self, sensor_id: str, object_id: str) -> None:
        """
        Remove an object from the action state.

        :param str sensor_id: ID of the sensor
        :param str object_id: ID of the object to remove
        :return: None
        """
        if sensor_id in self.action_state and object_id in self.action_state[sensor_id]:
            del self.action_state[sensor_id][object_id]
        if sensor_id in self.fall_risk_state and object_id in self.fall_risk_state[sensor_id]:
            del self.fall_risk_state[sensor_id][object_id]

    def update_live_object(self, live_objects: list[str]) -> None:
        """
        Update the live object state.

        :param list[str] live_objects: List of live objects
        :return: None
        """
        live_object_dict = {}
        for live_object in live_objects:
            parts = live_object.split(" #-# ")
            if len(parts) != 2:
                logger.warning(f"Malformed live object string: {live_object}")
                continue
            sensor_id, object_id = parts

            if sensor_id not in live_object_dict:
                live_object_dict[sensor_id] = set()
            live_object_dict[sensor_id].add(object_id)
        
        sensor_ids = list(self.action_state.keys())
        for sensor_id in sensor_ids:
            if sensor_id not in live_object_dict:
                self.remove_sensor(sensor_id)
            else:
                object_ids = list(self.action_state[sensor_id].keys())
                for object_id in object_ids:
                    if object_id not in live_object_dict[sensor_id]:
                        self.remove_object(sensor_id, object_id)

    def detect_lack_movement(
        self, 
        sensor_id: str, 
        object_id: str, 
        action: Action, 
        config: LackMovementConfig
    ) -> tuple[bool, str]:
        """
        Detect lack of movement in a behavior.

        :param str sensor_id: ID of the sensor
        :param str object_id: ID of the object
        :param Action action: Action of the object
        :param LackMovementConfig config: Configuration object containing lack of movement detection settings
        :return tuple[bool, str]: Tuple containing a boolean flag and a description of the detection

        Examples::
            >>> config = AppConfig()
            >>> detector = AnomalyActionDetection(config)
            >>> lack_movement_flag, lack_movement_description = detector.detect_lack_movement(sensor_id, object_id, action, config.lackMovement)
        """
        description = ""
        
        if sensor_id not in self.action_state:
            return False, description

        if object_id not in self.action_state[sensor_id]:
            return False, description

        action_state = self.action_state[sensor_id][object_id]
        last_action = action_state.get_last_action()
        current_action = action.type

        if last_action == "Unknown":
            return False, description
        
        if last_action == ActionType.LYING_DOWN.value:
            if current_action == ActionType.LYING_DOWN.value:
                lying_down_last_interval = action_state.get_last_interval(ActionType.LYING_DOWN.value)
                if lying_down_last_interval is None:
                    return False, description
                start_time = lying_down_last_interval[0]
                end_time = lying_down_last_interval[1]
                duration = (end_time - start_time).total_seconds()
                if duration >= config.durationThreshold:
                    description = f"Lack Movement {sensor_id}#{object_id}, duration: {duration} seconds"
                    return True, description

        return False, description

    def detect_fall_risk(
        self, 
        sensor_id: str, 
        object_id: str, 
        action: Action, 
        timestamp: datetime,
        config: FallRiskConfig
    ) -> tuple[bool, str]:
        """
        Detect fall risk in a behavior.

        :param str sensor_id: ID of the sensor
        :param str object_id: ID of the object
        :param Action action: Action of the object
        :param datetime timestamp: Timestamp of the object
        :param FallRiskConfig config: Configuration object containing fall risk detection settings
        :return tuple[bool, str]: Tuple containing a boolean flag and a description of the detection

        Examples::
            >>> config = AppConfig()
            >>> detector = AnomalyActionDetection(config)
            >>> fall_risk_flag, fall_risk_description = detector.detect_fall_risk(sensor_id, object_id, action, timestamp, config.fallRisk)
        """
        description = ""
        if sensor_id not in self.action_state:
            return False, description
        if object_id not in self.action_state[sensor_id]:
            return False, description
        
        action_state = self.action_state[sensor_id][object_id]
        last_action = action_state.get_last_action()
        current_action = action.type
        
        if last_action == "Unknown":
            return False, description

        if last_action == ActionType.LYING_DOWN.value:
            return self._handle_from_lying_down_fr(sensor_id, object_id, current_action, timestamp, config)
        else:
            return self._handle_from_other_action_fr(sensor_id, object_id, current_action, timestamp)

    def _handle_from_lying_down_fr(
        self, 
        sensor_id: str, 
        object_id: str, 
        current_action: str, 
        timestamp: datetime, 
        config: FallRiskConfig
    ) -> tuple[bool, str]:
        """
        Handle fall risk detection when transitioning from lying down state.
        
        :param str sensor_id: ID of the sensor
        :param str object_id: ID of the object
        :param str current_action: Current action of the object
        :param datetime timestamp: Timestamp of the object
        :param FallRiskConfig config: Configuration object containing fall risk detection settings
        :return tuple[bool, str]: Tuple containing a boolean flag and a description of the detection
        """
        if current_action != ActionType.LYING_DOWN.value:
            # Person moved from lying down - potential fall risk start
            return self._init_start_confirmation_fr(sensor_id, object_id, timestamp, config)
        else:
            # Person still lying down - handle end confirmation
            return self._continue_end_confirmation_fr(sensor_id, object_id)

    def _handle_from_other_action_fr(
        self, 
        sensor_id: str, 
        object_id: str, 
        current_action: str, 
        timestamp: datetime
    ) -> tuple[bool, str]:
        """
        Handle fall risk detection when transitioning from non-lying down state.
        
        :param str sensor_id: ID of the sensor
        :param str object_id: ID of the object
        :param str current_action: Current action of the object
        :param datetime timestamp: Timestamp of the object
        :return tuple[bool, str]: Tuple containing a boolean flag and a description of the detection
        """
        if sensor_id not in self.fall_risk_state or object_id not in self.fall_risk_state[sensor_id]:
            return False, ""
            
        if current_action == ActionType.LYING_DOWN.value:
            # Person went to lying down - handle confirmation reset
            return self._init_end_confirmation_fr(sensor_id, object_id)
        else:
            # Person not lying down - continue fall risk start confirmation or continue alert
            return self._continue_confirm_start_or_continue_fr(sensor_id, object_id, timestamp)

    def _init_start_confirmation_fr(
        self, 
        sensor_id: str, 
        object_id: str, 
        timestamp: datetime, 
        config: FallRiskConfig
    ) -> tuple[bool, str]:
        """
        Initialize new fall risk start confirmation.
        If a fall risk state already exists and is interrupted by a small lying down interval,
        continue trigger alert.

        :param str sensor_id: ID of the sensor
        :param str object_id: ID of the object
        :param datetime timestamp: Timestamp of the object
        :param FallRiskConfig config: Configuration object containing fall risk detection settings
        :return tuple[bool, str]: Tuple containing a boolean flag and a description of the detection
        """
        if sensor_id not in self.fall_risk_state:
            self.fall_risk_state[sensor_id] = {}
            
        if object_id not in self.fall_risk_state[sensor_id]:
            # Initialize new fall risk state
            self.fall_risk_state[sensor_id][object_id] = FallRiskState(
                sensor_id, object_id, timestamp, config.nConfirmStart, config.nConfirmEnd
            )
            self.fall_risk_state[sensor_id][object_id].update_start_time(timestamp)
            self.fall_risk_state[sensor_id][object_id].reset_n_confirm_start()
            self.fall_risk_state[sensor_id][object_id].increase_n_confirm_start()
            self.fall_risk_state[sensor_id][object_id].reset_n_confirm_end()
            return False, "Init new fall risk, confirm start"
        else:
            # Fall risk already exists - interrupt by small lying down interval - continue trigger alert
            self.fall_risk_state[sensor_id][object_id].update_fall_risk(True, timestamp)
            return True, f"Fall Risk {sensor_id}#{object_id}, Continue alert"

    def _continue_end_confirmation_fr(
        self, 
        sensor_id: str, 
        object_id: str
    ) -> tuple[bool, str]:
        """
        Handle end confirmation when person remains lying down.
        If a fall risk state already exists and is interrupted by a small lying down interval:
        - Stop tracking and alert if the lying down interval is longer than the threshold
        - Continue tracking but do not alert if the lying down interval is shorter than the threshold

        :param str sensor_id: ID of the sensor
        :param str object_id: ID of the object
        :return tuple[bool, str]: Tuple containing a boolean flag and a description of the detection
        """
        if sensor_id in self.fall_risk_state and object_id in self.fall_risk_state[sensor_id]:
            if self.fall_risk_state[sensor_id][object_id].is_confirmed_end():
                del self.fall_risk_state[sensor_id][object_id]
                return False, f"Fall Risk {sensor_id}#{object_id}, stop alert"
            else:
                self.fall_risk_state[sensor_id][object_id].increase_n_confirm_end()
                return False, "Wait for end confirm"
        else:
            return False, "Remain lying down"

    def _init_end_confirmation_fr(
        self, 
        sensor_id: str, 
        object_id: str, 
    ) -> tuple[bool, str]:
        """
        Initialize new fall risk end confirmation.
        If a fall risk state is confirmed, init end confirmation.
        If a fall risk state is not confirmed, stop tracking.

        :param str sensor_id: ID of the sensor
        :param str object_id: ID of the object
        :return tuple[bool, str]: Tuple containing a boolean flag and a description of the detection
        """
        fall_risk_state = self.fall_risk_state[sensor_id][object_id]
        
        if fall_risk_state.is_confirmed_start():
            fall_risk_state.reset_n_confirm_end()
            fall_risk_state.increase_n_confirm_end()
            fall_risk_state.reset_n_confirm_start()
            return False, "Init end confirmation"
        else:
            return False, "Stop fall-risk tracking"

    def _continue_confirm_start_or_continue_fr(
        self, 
        sensor_id: str, 
        object_id: str, 
        timestamp: datetime
    ) -> tuple[bool, str]:
        """
        Continue fall risk confirmation when person is not lying down.
        If a fall risk state is confirmed, continue alert.
        If a fall risk state is not confirmed, continue confirm start.

        :param str sensor_id: ID of the sensor
        :param str object_id: ID of the object
        :param datetime timestamp: Timestamp of the object
        :return tuple[bool, str]: Tuple containing a boolean flag and a description of the detection
        """
        fall_risk_state = self.fall_risk_state[sensor_id][object_id]
        
        if fall_risk_state.is_confirmed_start():
            fall_risk_state.update_fall_risk(True, timestamp)
            return True, f"Fall Risk {sensor_id}#{object_id}, Continue alert"
        else:
            fall_risk_state.increase_n_confirm_start()
            return False, "Wait for start confirmation"
