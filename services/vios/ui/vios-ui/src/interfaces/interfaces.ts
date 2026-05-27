/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
import { ReactNode } from 'react';
import { AppBarProps as MuiAppBarProps } from '@mui/material/AppBar';
import { StreamType, StreamOverlayOptions } from 'vst-streaming-lib';

/**
 * VST UI Config
 */
export interface Config {
    sensorManagementEndpoint: string;
    streamRecorderEndpoint: string;
    storageManagementEndpoint: string;
    liveStreamEndpoint: string;
    replayStreamEndpoint: string;
    streambridgeEndpoint: string;
    mdatWebApiEndpoint: string;
    analyticsUIServerEndpoint: string;
    enableLogs: boolean;
}

interface Coordinates {
    x: string;
    y: string;
}

interface GeoLocation {
    latitude: string;
    longitude: string;
}

/**
 * Sensor Position Schema
 */
export interface SensorPosition {
    coordinates: Coordinates;
    depth: string;
    direction: string;
    fieldOfView: string;
    geoLocation: GeoLocation;
    origin: GeoLocation;
}

/**
 * VST Sensor matches the REST API schema
 */
export interface Sensor {
    firmwareVersion: string;
    hardware: string;
    hardwareId: string;
    isRemoteSensor: boolean;
    location: string;
    manufacturer: string;
    name: string;
    position: SensorPosition;
    remoteDeviceId: string;
    remoteDeviceLocation: string;
    remoteDeviceName: string;
    sensorId: string;
    sensorIp: string;
    serialNumber: string;
    state: string;
    tags: string;
    isAuthorized: boolean;
    isError: boolean;
    resolution: string;
    streamId?: string;
    isMain?: boolean;
}

/**
 * Sensor Selector Props for dropdown component
 */
export interface MultipleSensorSelectorProps {
    sensors: Sensor[];
    multiple?: boolean;
    selectedSensors?: Sensor[];
    onChange: (selectedSensors: Sensor[] | undefined) => void;
    label?: string;
}

/**
 * Single Sensor Selector Props for dropdown component
 */
export interface SingleSensorSelectorProps {
    sensors: Sensor[];
    multiple?: boolean;
    selectedSensors: Sensor | null;
    onChange: (selectedSensor: Sensor | null) => void;
    showSensorId?: boolean;
}

/**
 * Video player props
 */
export interface VideoPlayerProps {
    sensor?: Sensor;
    streamType: StreamType;
    videoElementId: string;
    onWebRTCStatsUpdate?: (stats: RTCStatsReport) => void;
    sensors?: Sensor[];
    onClose?: () => void;
}

/**
 * Widget props used in Dashboard page
 */
export interface WidgetProps {
    color?: 'primary' | 'secondary' | 'info' | 'success' | 'warning' | 'error';
    title: string;
    total: number;
    icon: ReactNode;
    sx?: React.CSSProperties;
    children?: ReactNode;
}

/**
 * Sensor Record Status
 */
export interface RecordStatus {
    id: string;
    recording_status: string;
}

/**
 * Sensor Record Status that matches the API schema
 */
export interface SensorRecordStatus {
    recordingStatus: string;
}

/**
 * Sensor Status matches the REST API schema
 */
export interface SensorStatus {
    errorCode: string;
    errorMessage: string;
    name: string;
    state: string;
}

/**
 * Sensor Status List
 */
export interface SensorStatusList {
    [key: string]: SensorStatus;
}

/**
 * Sensor Information Table Data used in Dashboard
 */
export interface SensorInformationTableData {
    name: string;
    sensorId: string;
    remoteDeviceName: string;
    remoteDeviceLocation: string;
    recording_status: string;
    state: string;
    errorMessage: string;
}

/**
 * Stream schema as per REST API
 */
export interface Stream {
    isMain: boolean;
    metadata: {
        bitrate: string;
        codec: string;
        framerate: string;
        govlength: string;
        resolution: string;
    };
    name: string;
    streamId: string;
    url: string;
    vodUrl: string;
}

/**
 * List of streams that matches the REST API schema
 */
export interface StreamList {
    [key: string]: Stream[];
}

/**
 * RTSP Table Data used in Dashboard
 */
export interface RTSPTableData {
    name: string;
    url: string;
    type: string;
    resolution: string;
}

/**
 * VST record size
 */
export interface VSTRecordSize {
    remainingStorageDays: number;
    sizeInMegabytes: number;
    state?: string;
}

/**
 * VST record size as per REST API
 */
export interface TotalVSTRecordSize {
    [key: string]: VSTRecordSize;
    total: VSTRecordSize;
}

/**
 * VST UI wrapper for adaptor
 */
export interface VstAdaptorWrapperProps {
    children: ReactNode;
}

/**
 * App bar props used in App Bar component
 */
export interface AppBarProps extends MuiAppBarProps {
    open?: boolean;
}

/**
 * Sensor Stream data used in zustand
 */
export interface SensorStreamData {
    id: string;
    name: string;
    streams: Stream[];
}

/**
 * Timeline entry for storage
 */
export interface StorageTimeline {
    endTime: string;
    sizeInMegabytes: number;
    startTime: string;
}

/**
 * Storage size for individual sensor
 */
export interface SensorStorageSize {
    sizeInMegabytes: number;
    state: string;
    timelines: StorageTimeline[];
}

/**
 * Total storage size information
 */
export interface TotalStorageSize {
    remainingStorageDays: number;
    sizeInMegabytes: number;
    totalAvailableStorageSize: number;
    totalDiskCapacity: number;
}

/**
 * Complete storage size response from API
 */
export interface StorageSizes {
    [key: string]: SensorStorageSize | TotalStorageSize;
    total: TotalStorageSize;
}

/**
 * API Error tracking
 */
export interface ApiError {
    timestamp: number;
    error: string;
    hasError: boolean;
}

/**
 * API Error state for tracking multiple API endpoints
 */
export interface ApiErrors {
    sensorList: ApiError | null;
    sensorStatus: ApiError | null;
    recordingStatus: ApiError | null;
    streams: ApiError | null;
    storageSize: ApiError | null;
}

/**
 * Zustand state
 */
export interface VSTUIState {
    sensorServiceSensors: Sensor[];
    replayServiceSensors: Sensor[];
    liveServiceSensors: Sensor[];
    streams: SensorStreamData[];
    emdxEndpoint?: string;
    vstAdaptorType?: string;
    vstVersion?: string;
    storageSizes?: StorageSizes;
    sensorStatus: Record<string, SensorStatus>;
    recordingStatus: Record<string, RecordStatus>;
    removedSensors: Sensor[];
    apiErrors: ApiErrors;
    isLoadingTimelines: boolean;

    // Service availability flags
    isSensormanagementServiceAvailable: boolean;
    isStoragemanagementServiceAvailable: boolean;
    isRecorderServiceAvailable: boolean;
    isLiveStreamServiceAvailable: boolean;
    isStreamBridgeServiceAvailable: boolean;
    isReplayServiceAvailable: boolean;

    // Service availability setters
    setSensormanagementServiceAvailable: (available: boolean) => void;
    setStoragemanagementServiceAvailable: (available: boolean) => void;
    setRecorderServiceAvailable: (available: boolean) => void;
    setLiveStreamServiceAvailable: (available: boolean) => void;
    setStreamBridgeServiceAvailable: (available: boolean) => void;
    setReplayServiceAvailable: (available: boolean) => void;
    checkAllServicesAvailability: () => Promise<{
        sensorManagement: boolean;
        storageManagement: boolean;
        recorder: boolean;
        liveStream: boolean;
        streamBridge: boolean;
        replay: boolean;
    }>;

    setSensorServiceSensors: (sensors: Sensor[]) => void;
    setReplayServiceSensors: (sensors: Sensor[]) => void;
    setLiveServiceSensors: (sensors: Sensor[]) => void;
    setEmdxEndpoint: (endpoint: string) => void;
    setVstAdaptorType: (adaptorType: string) => void;
    setVstVersion: (version: string) => void;
    addStreams: (sensorStreams: SensorStreamData | SensorStreamData[]) => void;
    setStorageSizes: (storageSizes: StorageSizes) => void;
    setSensorStatus: (sensorStatus: Record<string, SensorStatus>) => void;
    setRecordingStatus: (recordingStatus: Record<string, RecordStatus>) => void;
    setRemovedSensors: (removedSensors: Sensor[]) => void;
    setApiError: (endpoint: keyof ApiErrors, error: ApiError | null) => void;
    setIsLoadingTimelines: (isLoading: boolean) => void;
}

/**
 * Schedule interface as per REST schema
 */
export interface CronSchedule {
    endTime: string;
    startTime: string;
}

/**
 * Record Timelines
 */
export interface Timeline {
    endTime: string;
    startTime: string;
}

/**
 * For timeline component
 */

export interface CronScheduleArray extends Array<CronSchedule> {}

/**
 * Interfaces meant for Sensor Image and Encode settings that match the REST API schema
 */

export interface RangeField {
    Max: string;
    Min: string;
    Value: string;
}

export interface EnumField {
    AllowedValues: string[];
    Value: string;
}

export interface Resolution {
    Height: string;
    Width: string;
}

export interface EncodingOptions {
    Bitrate: RangeField;
    FrameRate: EnumField;
    GovLength: RangeField;
    Profiles: EnumField;
    Quality: RangeField;
    Resolution: {
        AllowedValues: Resolution[];
        Value: Resolution;
    };
    isActive?: boolean;
}

export interface EncodeSettings {
    Encoding: EnumField;
    Options: Array<{ [key: string]: EncodingOptions }>;
}

export interface ImageSettings {
    BacklightCompensationMode: EnumField;
    Brightness: RangeField;
    ColorSaturation: RangeField;
    Contrast: RangeField;
    ExposureMode: EnumField;
    IrCutFilterMode: EnumField;
    Sharpness: RangeField;
    WhiteBalanceMode: EnumField;
    WideDynamicRangeMode: EnumField;
}

export interface CameraProfile {
    Encode: EncodeSettings;
    Image: ImageSettings;
}

export interface CameraSettings {
    [key: string]: CameraProfile;
}

export interface EncodeValues {
    Bitrate?: string;
    Encoding?: string;
    FrameRate?: string;
    GovLength?: string;
    Profiles?: string;
    Quality?: string;
    Resolution?: {
        Height: string;
        Width: string;
    };
}

export interface ImageValues {
    BacklightCompensationMode?: string;
    Brightness?: string;
    ColorSaturation?: string;
    Contrast?: string;
    ExposureMode?: string;
    IrCutFilterMode?: string;
    Sharpness?: string;
    WhiteBalanceMode?: string;
    WideDynamicRangeMode?: string;
}

export interface EncodeAndImageValues {
    Encode?: EncodeValues;
    Image?: ImageValues;
}

export interface NetworkConfig {
    dhcpV4: string;
    dhcpV6: string;
    ipAddressV4: string;
    ipAddressV6: string;
    isIpv4Enabled: boolean;
    isIpv6Enabled: boolean;
    subnetMaskV4: string;
    subnetMaskV6: string;
}

/**
 * Payload for adding new sensor
 */
export interface AddNewSensorPayload {
    username: string;
    password: string;
    name: string;
    location: string;
    sensorUrl?: string;
    sensorIp?: string;
    tags?: string;
    verifyRtsp?: boolean;
}

/**
 * payload for replacing any sensor
 */
export interface ReplaceSensorPayload {
    sensorId: string;
}

/**
 * Live Stream config schema
 */
export interface LiveStreamConfig {
    WebrtcOutDefaultResolution: string;
    analyticServerAddress: string;
    coturnTurnUrlListWithSecret: string | null;
    enableDecLowLatencyMode: boolean;
    enableFrameDrop: boolean;
    enableGstDebugProbes: boolean;
    enablePerfLogging: boolean;
    enableUserCleanup: boolean;
    gpuIndices: string;
    httpPort: string;
    maxStreamsSupported: number;
    maxWebrtcInConnections: number;
    maxWebrtcOutConnections: number;
    multiUserExtraOptions: string;
    overlayTextFontType: string;
    prometheusPort: string;
    reverseProxyServerAddress: string;
    staticTurnUrlList: string[] | null;
    stunUrlList: string[];
    twilioAccountSid: string;
    twilioAuthToken: string;
    useCoturnAuthSecret: boolean;
    useHttpDigestAuthentication: boolean;
    useHttps: boolean;
    useMultiUser: boolean;
    useReverseProxy: boolean;
    useSoftwarePath: boolean;
    useTwilioStunTurn: boolean;
    useVideoMetadataProtobuf: boolean;
    useWebrtcOutInbuiltEncoder: string;
    videoMetadataQueryBatchSizeNumFrames: number;
    videoMetadataServer: string;
    vstDataPath: string;
    vstIp: string;
    webrtcLatencyMs: number;
    webrtcOutEnableInsertSpsPps: boolean;
    webrtcOutMinDrcInterval: number;
    webrtcOutSetIdrInterval: number;
    webrtcOutSetIframeInterval: number;
    webrtc_video_quality_tunning: {
        [key: string]: {
            bitrate_range: number[];
            bitrate_start: number;
            qp_range_I: number[];
            qp_range_P: number[];
        };
    };
    webrtcpeerConnTimeoutSec: number;
    webserviceAccessControlList: string;
}

/**
 * Sensor management config schema
 */
export interface SensorManagementConfig {
    defaultBitrateKbps: number;
    defaultEncodingInterval: number;
    defaultFramerate: number;
    defaultGovLength: number;
    defaultProfile: string;
    defaultQuality: number;
    defaultResolution: string;
    deviceDiscoveryFrequencySeconds: number;
    deviceDiscoveryInterfaces: string | null;
    deviceDiscoveryTimeoutSeconds: number;
    deviceLocation: string;
    deviceName: string;
    enableDebugApis: boolean;
    enableNotification: boolean;
    enablePrometheus: boolean;
    enableUserCleanup: boolean;
    httpPort: string;
    kafkaServerAddress: string;
    maxSensorsSupported: number;
    messageBrokerMetadataTopic: string;
    messageBrokerTopic: string;
    message_broker_payload_key: string;
    multiUserExtraOptions: string;
    ntpServers: string | null;
    nvNgcKey: string;
    nvOrgId: string;
    onvifRequestTimeoutSeconds: number;
    prometheusPort: string;
    redisServerEnvironmentVariable: string;
    remoteVstAddress: string;
    supportedAudioCodecs: string;
    supportedVideoCodecs: string;
    useHttpDigestAuthentication: boolean;
    useHttps: boolean;
    useMessageBroker: string;
    useMultiUser: boolean;
    use_sensor_ntp_time: boolean;
    vstDataPath: string;
    vstIp: string;
    webserviceAccessControlList: string;
}

/**
 * Sensor proxy config schema
 */
export interface SensorProxyConfig {
    enablePrometheus: boolean;
    enableProxyServerFrameIdSupport: boolean;
    enableQosMonitoring: boolean;
    enableRtspServerFrameIdSupport: boolean;
    enableStreamMonitoring: boolean;
    enableUserCleanup: boolean;
    httpPort: string;
    maxStreamsSupported: number;
    multiUserExtraOptions: string;
    prometheusPort: string;
    qosDataCaptureIntervalSec: number;
    qosDataPublishIntervalSec: number;
    qosLogfilePath: string;
    rtspInBaseUdpPortNum: number;
    rtspOutBaseUdpPortNum: number;
    rtspPreferredNetworkIface: string;
    rtspServerPort: number;
    rtspServerReclamationClientTimeoutSec: number;
    rtspStreamingOverTcp: boolean;
    rtsp_server_instances_count: number;
    serverDomainName: string;
    sessionMaxAgeSec: number;
    streamMonitorIntervalSecs: number;
    useHttpDigestAuthentication: boolean;
    useHttps: boolean;
    useMultiUser: boolean;
    useRtspAuthentication: boolean;
    use_sensor_ntp_time: boolean;
    vstDataPath: string;
    vstIp: string;
    webserviceAccessControlList: string;
}

/**
 * Sensor reocord config schema
 */
export interface SensorRecordConfig {
    alwaysRecording: boolean;
    enableUserCleanup: boolean;
    eventRecordLengthSecs: number;
    httpPort: string;
    multiUserExtraOptions: string;
    recordBufferLengthSecs: number;
    recordedVideoDirRoot: string;
    sessionMaxAgeSec: number;
    useHttpDigestAuthentication: boolean;
    useHttps: boolean;
    useMultiUser: boolean;
    vstDataPath: string;
}

/**
 * Sensor mangement config schema
 */
export interface StorageManagementConfig {
    enableAgingPolicy: boolean;
    enableUserCleanup: boolean;
    maxVideoDownloadSizeMB: number;
    multiUserExtraOptions: string;
    recordedVideoDirRoot: string;
    storageMonitoringFrequencySecs: number;
    storageThresholdPercentage: number;
    totalVideoStorageSizeMB: number;
    useHttpDigestAuthentication: boolean;
    useHttps: boolean;
    useMultiUser: boolean;
    vstDataPath: string;
    vstIp: string;
    webserviceAccessControlList: string;
}

/**
 * Replay stream config schema
 */
export interface ReplayStreamConfig {
    coturnTurnUrlListWithSecret: string | null;
    enableDecLowLatencyMode: boolean;
    enableFrameDrop: boolean;
    enableGstDebugProbes: boolean;
    enablePerfLogging: boolean;
    enableUserCleanup: boolean;
    gpuIndices: string;
    httpPort: string;
    maxStreamsSupported: number;
    maxWebrtcInConnections: number;
    maxWebrtcOutConnections: number;
    multiUserExtraOptions: string;
    prometheusPort: string;
    reverseProxyServerAddress: string;
    staticTurnUrlList: string[] | null;
    stunUrlList: string[];
    twilioAccountSid: string;
    twilioAuthToken: string;
    useCoturnAuthSecret: boolean;
    useHttpDigestAuthentication: boolean;
    useHttps: boolean;
    useMultiUser: boolean;
    useReverseProxy: boolean;
    useSoftwarePath: boolean;
    useTwilioStunTurn: boolean;
    useVideoMetadataProtobuf: boolean;
    useWebrtcOutInbuiltEncoder: string;
    videoMetadataQueryBatchSizeNumFrames: number;
    videoMetadataServer: string;
    vstDataPath: string;
    vstIp: string;
    webrtcLatencyMs: number;
    webrtcOutEnableInsertSpsPps: boolean;
    webrtcOutMinDrcInterval: number;
    webrtcOutSetIdrInterval: number;
    webrtcOutSetIframeInterval: number;
    webrtc_video_quality_tunning: {
        [key: string]: {
            bitrate_range: number[];
            bitrate_start: number;
            qp_range_I: number[];
            qp_range_P: number[];
        };
    };
    webrtcpeerConnTimeoutSec: number;
    webserviceAccessControlList: string;
}

export interface StreamQueryAndStatus {
    streamQuery: {
        ts: number;
    };
    streamStatus: {
        error: boolean;
        state: string;
    };
}

export interface WebRTCStats {
    fps: number;
    pli: number;
    nack: number;
    rtt: number;
    packetsLost: number;
    jitter: number;
    bitrate: number;
    fir: number;
    qualityLimitationReason?: string;
}

export interface TripwireJSON {
    deleteIfPresent: boolean;
    tripwires: Tripwire[];
    sensorId: string;
}

export interface Tripwire {
    id: string;
    name: string;
    wire: XYCoordinates[];
    direction: Direction;
}

export interface XYCoordinates {
    x: number;
    y: number;
}

export interface Direction {
    p1: XYCoordinates;
    p2: XYCoordinates;
    entry?: EntryExit;
    exit?: EntryExit;
}

export interface EntryExit {
    name: string;
}

export interface ROIJSON {
    deleteIfPresent: boolean;
    rois: ROI[];
    sensorId: string;
}

export interface ROI {
    id: string;
    name: string;
    coordinates: XYCoordinates[];
}

export type AdaptorType = 'streamer' | 'default';

export interface RuntimeStreamOverlayOptions {
    peerId: string;
    framerate: number;
    resolution: string; // widthxheight
    overlay?: StreamOverlayOptions;
}
