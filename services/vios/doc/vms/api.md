# Nvidia VMS
## Version: 0.7.3

**License:** Copyright (c) 2021, NVIDIA CORPORATION.  All rights reserved.

### /getLiveStreamUriList

#### GET
##### Summary

Get list of live RTSP urls

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | json object array | [ object ] |
| default | error | [Error](#error) |

### /getIceCandidate

#### GET
##### Summary

Get list of ICE Candidates

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| peerid | query |  | Yes | string |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | json object array | [ object ] |
| default | error | [Error](#error) |

### /getReplayStreamUriList

#### GET
##### Summary

Get list of replay RTSP urls

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | json object array | [ object ] |
| default | error | [Error](#error) |

### /getPeerConnectionList

#### GET
##### Summary

Get peer connection list

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | json object array | [ object ] |
| default | error | [Error](#error) |

### /getIceServers

#### GET
##### Summary

Get URL or URLs of the servers to be used for ICE negotiations. These are typically STUN and/or TURN servers

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | json object | object |
| default | error | [Error](#error) |

### /version

#### GET
##### Summary

Get API specification version

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | get API specification version | string |
| default | error | [Error](#error) |

### /help

#### GET
##### Summary

Get supported API list

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | json array | [ string ] |
| default | error | [Error](#error) |

### /addIceCandidate

#### POST
##### Summary

Add new remote candidate to the RTCPeerConnection's remote description

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| body | body |  | Yes | object |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | boolean value | boolean |
| default | error | [Error](#error) |

### /stream/seek

#### POST
##### Summary

Seek forward (+10s), seek backward (-10s), rewind and fast forward

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| body | body | json object containing seek information | Yes |  |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | boolean value | boolean |
| default | error | [Error](#error) |

### /stream/pause

#### POST
##### Summary

Pause the recorded stream playback

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| body | body |  | Yes |  |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | boolean value | boolean |
| default | error | [Error](#error) |

### /stream/resume

#### POST
##### Summary

Resume the recorded stream playback

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| body | body |  | Yes |  |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | boolean value | boolean |
| default | error | [Error](#error) |

### /stream/stop

#### POST
##### Summary

Stop webrtc streaming

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| body | body |  | Yes |  |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | boolean value | boolean |
| default | error | [Error](#error) |

### /stream/switch

#### POST
##### Summary

Switch stream type

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| body | body |  | Yes |  |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | boolean value | boolean |
| default | error | [Error](#error) |

### /stream/start

#### POST
##### Summary

Start webrtc streaming

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| body | body |  | Yes |  |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | json object | object |
| default | error | [Error](#error) |

### /stream/status

#### GET
##### Summary

Get stream error status and playback status like PLAYING, PAUSED, NOT PLAYING

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| peerid | query |  | Yes | string |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | json object |  |
| default | error | [Error](#error) |

### /stream/stats

#### GET
##### Summary

Get stream stats like frame rate, total encoded and decoded frames

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| peerid | query |  | Yes | string |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | json object |  |
| default | error | [Error](#error) |

### /device/new

#### POST
##### Summary

Add device manually providing RTSP url OR device IP address and device details

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| body | body | url field can be omitted in case of add device using IP address and ip, username and password fields can be omitted in case of add device using RTSP url | Yes |  |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | boolean value | boolean |
| default | error | [Error](#error) |

### /device/{deviceId}/info

#### GET
##### Summary

Get information for particular device

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| deviceId | path |  | Yes | string |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | json object |  |
| default | error | [Error](#error) |

#### POST
##### Summary

Set information for particular device

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| deviceId | path |  | Yes | string |
| body | body | json object | Yes | object |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | boolean value | boolean |
| default | error | [Error](#error) |

### /device/list

#### GET
##### Summary

Get list of devices with some details

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | json object array | [ object ] |
| default | error | [Error](#error) |

### /device/{deviceId}/record

#### GET
##### Summary

Get device record states

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| deviceId | path |  | Yes | string |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | boolean value | object |
| default | error | [Error](#error) |

#### POST
##### Summary

Start/Stop Recording

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| deviceId | path |  | Yes | string |
| body | body | json object | Yes |  |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | boolean value | boolean |
| default | error | [Error](#error) |

#### DELETE
##### Summary

Delete recorded video between specified time

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| deviceId | path |  | Yes | string |
| body | body | json object | Yes | object |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | deleted files size in MB | object |
| default | error | [Error](#error) |

### /device/{deviceId}/record/files

#### GET
##### Summary

Get video file details for particular device

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| deviceId | path |  | Yes | string |
| start_time | query |  | Yes | string |
| end_time | query |  | Yes | string |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | json object array | [ object ] |
| default | error | [Error](#error) |

### /device/{deviceId}/record/schedule

#### GET
##### Summary

Get device record schdule for particular device

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| deviceId | path |  | Yes | string |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | json object | [ object ] |
| default | error | [Error](#error) |

#### POST
##### Summary

Sets/Update device record schdule for particular device

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| deviceId | path |  | Yes | string |
| body | body | json object | Yes | [ object ] |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | boolean value | boolean |
| default | error | [Error](#error) |

#### DELETE
##### Summary

Un-schedule device record schdule for particular device

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| deviceId | path |  | Yes | string |
| body | body | json object array | Yes | [ object ] |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | boolean value | boolean |
| default | error | [Error](#error) |

### /device/{deviceId}/record/size

#### GET
##### Summary

Get total recorded video size

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| deviceId | path |  | Yes | string |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | json object |  |
| default | error | [Error](#error) |

### /device/{deviceId}/record/timelines

#### GET
##### Summary

Get timeline of recorded video between specified time

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| deviceId | path |  | Yes | string |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | json object array | [  ] |
| default | error | [Error](#error) |

### /device/{deviceId}/credentials

#### POST
##### Summary

Sets/updates device username and password

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| deviceId | path |  | Yes | string |
| body | body | json object | Yes | object |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | boolean value | boolean |
| default | error | [Error](#error) |

### /device/scan

#### POST
##### Summary

Make vms to scan devices from network

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | boolean value | boolean |
| default | error | [Error](#error) |

### /device/{deviceId}/settings

#### GET
##### Summary

Get device Image, Encode parameters like brightness, saturation, bitrate etc

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| deviceId | path |  | Yes | string |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | json object | object |

#### POST
##### Summary

Set/Update device image and encode parameters supported by device

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| deviceId | path |  | Yes | string |
| body | body | json object | Yes | object |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | boolean value | boolean |
| default | error | [Error](#error) |

### /device/{deviceId}/settings/Encode

#### GET
##### Summary

Get device encode settings

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| deviceId | path |  | Yes | string |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | json object | object |

### /device/{deviceId}/settings/Image

#### GET
##### Summary

Get device image settings

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| deviceId | path |  | Yes | string |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | json object | object |

### /device/status

#### GET
##### Summary

Get device error and state for all devices

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | json object | object |
| default | error | [Error](#error) |

### /device/{deviceId}/status

#### GET
##### Summary

Get device error and state for particular device

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| deviceId | path |  | Yes | string |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | json object | object |
| default | error | [Error](#error) |

### /device/{deviceId}

#### DELETE
##### Summary

Remove device entry from vms

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| deviceId | path |  | Yes | string |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | boolean value | boolean |
| default | error | [Error](#error) |

### /device/{deviceId}/replace

#### POST
##### Summary

Replace inactive device

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| deviceId | path |  | Yes | string |
| body | body | json object | Yes | object |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | null |  |
| default | error | [Error](#error) |

### /device/qos

#### GET
##### Summary

Get qos stats

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | qos stats of each device<br><br>**Example** (*application/json*):<br><pre>[<br>  {<br>    "avg_fps": 30.27503809528606,<br>    "avg_framecount": 29.972287622865036,<br>    "bitrate_kbps_avg": 6612.712848322057,<br>    "bitrate_kbps_max": 10062.911645925,<br>    "bitrate_kbps_min": 1852.331733149473,<br>    "elapsed_measurement_time": 3579.019229,<br>    "errors": "",<br>    "inter_packet_gap_ms_avg": 1.6741093426524636,<br>    "inter_packet_gap_ms_max": 49.057,<br>    "inter_packet_gap_ms_min": 0.004,<br>    "name": "HIKVISION%20DS-2CD2045FWD-I",<br>    "packet_loss_percentage_avg": 0,<br>    "packet_loss_percentage_max": 0,<br>    "packet_loss_percentage_min": 0,<br>    "rtsp_url": "rtsp://10.24.219.235/live/efa354fd-6519-4f4c-894d-c7df876e8c4f",<br>    "timestamp": "2021-12-01T06:21:01Z",<br>    "total_packets_lost": 0,<br>    "total_packets_received": 2138198<br>  },<br>  {<br>    "avg_fps": 25.99473551549843,<br>    "avg_framecount": 25.742126551089385,<br>    "bitrate_kbps_avg": 12390.564338271713,<br>    "bitrate_kbps_max": 26563.242197183623,<br>    "bitrate_kbps_min": 1795.4952091368145,<br>    "elapsed_measurement_time": 3573.884585,<br>    "errors": "",<br>    "inter_packet_gap_ms_avg": 0.8864725820262719,<br>    "inter_packet_gap_ms_max": 990.581,<br>    "inter_packet_gap_ms_min": 0.004,<br>    "name": "Sony",<br>    "packet_loss_percentage_avg": 0,<br>    "packet_loss_percentage_max": 0,<br>    "packet_loss_percentage_min": 0,<br>    "rtsp_url": "rtsp://10.24.219.235/live/f26a6ce6-dff9-47cb-a509-993f45b97271",<br>    "timestamp": "2021-12-01T06:21:01Z",<br>    "total_packets_lost": 0,<br>    "total_packets_received": 4031534<br>  },<br>  {<br>    "num_active_rtsp_connections": 2<br>  }<br>]</pre><br><br>**Example** (*required*):<br><pre>[<br>  "avg_fps",<br>  "avg_framecount",<br>  "bitrate_kbps_avg",<br>  "bitrate_kbps_max",<br>  "bitrate_kbps_min",<br>  "elapsed_measurement_time",<br>  "errors",<br>  "inter_packet_gap_ms_avg",<br>  "inter_packet_gap_ms_max",<br>  "inter_packet_gap_ms_min",<br>  "name",<br>  "packet_loss_percentage_avg",<br>  "packet_loss_percentage_max",<br>  "packet_loss_percentage_min",<br>  "rtsp_url",<br>  "timestamp",<br>  "total_packets_lost",<br>  "total_packets_received",<br>  "num_active_rtsp_connections"<br>]</pre> |  |
| default | error | [Error](#error) |

### /vms/record/size

#### GET
##### Summary

Get total recorded video size

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | json object | object |
| default | error | [Error](#error) |

### /vms/settings

#### GET
##### Summary

Get various vms specific settings

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | **VMS settings description** \| Setting \| Description \| \| ------- \| --------\| \| device_discovery_interfaces   \| network interface to be used in device discovery   \| \| device_discovery_timeout_secs   \| device discovery timeout in seconds   \| \| enable_perf_logging   \| enable-disable VMS perf logging   \| \| enable_stream_monitoring   \| enable-disable stream monitoring   \| \| http_port   \| http port to use   \| \| max_devices_supported   \| maximum number of devices that the user can add   \| \| max_webrtc_connections   \| maximum number of peer connections allowed   \| \| onvif_request_timeout_secs   \| onvif request timeout in seconds   \| \| recorded_video_dir_root   \| directory where recorded files are kept   \| \| redis_server_env_var   \| redis server environment variable   \| \| rtsp_preferred_network_iface   \| preferred network interface for rtsp   \| \| storage_lower_threshold_percentage   \| storage manager will delete files and fall back to lower threshold percentage   \| \| storage_monitoring_frequency_secs   \| frequency at which storage manager checks the storage size   \| \| storage_upper_threshold_percentage   \| storage manager will start deleting files once upper threshold is reached. The deletion will continue until no videos are left or lower threshold percentage is reached   \| \| stream_monitor_interval_secs   \| stream monitor interval in seconds   \| \| stunurl   \| stun url addresses   \| \| turnurl   \| turn url addresses   \| \| total_video_storage_size_MB   \| maximum storage available for recordings   \| \| video_metadata_query_batch_size_num_frames   \| batch size for fetching video metadata   \| \| video_metadata_server   \| video metadata server address   \| \| vms_db_path   \| directory where vms database will be created   \| \| vms_ip   \| vms ip address   \| \| webservice_access_control_list   \| limit the web service to specified subnets or ip addresses   \| \| qos_data_capture_interval_sec \| interval to capture qos stats \| \| qos_data_publish_interval_sec \| interval to publish qos stats \| \| qos_logfile_path \| location of qos log file \| \| recorded_video_total_size_MB \| VMS record size \| \| server_domain_name \| domain name of server \|  | object |
| default | error | [Error](#error) |

### /debug/stats/

#### GET
##### Summary

Get system stats

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | json object | object |
| default | error | [Error](#error) |

### /debug/device/unplug

#### POST
##### Summary

Simulate device plug-unplug

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| body | body |  | Yes | object |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | boolean value | boolean |
| default | error | [Error](#error) |

### /debug/device/plug

#### POST
##### Summary

Simulate device plug-unplug

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| body | body |  | Yes | object |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | boolean value | boolean |
| default | error | [Error](#error) |

### /debug/device/status

#### GET
##### Summary

Check if device is plugged or unplugged

##### Parameters

| Name | Located in | Description | Required | Schema |
| ---- | ---------- | ----------- | -------- | ---- |
| ip | query |  | Yes | string |

##### Responses

| Code | Description | Schema |
| ---- | ----------- | ------ |
| 200 | json object | object |

### Models

#### Ice_servers

| Name | Type | Description | Required |
| ---- | ---- | ----------- | -------- |
| urls | [ string ] |  | Yes |

#### Position

| Name | Type | Description | Required |
| ---- | ---- | ----------- | -------- |
| depth | string |  | Yes |
| direction | string |  | Yes |
| field_of_view | string |  | Yes |
| gps | [Gps](#gps) |  | Yes |

#### Gps

| Name | Type | Description | Required |
| ---- | ---- | ----------- | -------- |
| latitude | string |  | Yes |
| longitude | string |  | Yes |

#### Get_encode

| Name | Type | Description | Required |
| ---- | ---- | ----------- | -------- |
| Bitrate | [Range_setting](#range_setting) |  | No |
| Encoding | [Enum_setting](#enum_setting) |  | No |
| EncodingInterval | [Range_setting](#range_setting) |  | No |
| FrameRate | [Range_setting](#range_setting) |  | No |
| GovLength | [Range_setting](#range_setting) |  | No |
| H264Profile | [Enum_setting](#enum_setting) |  | No |
| Quality | [Range_setting](#range_setting) |  | No |
| Resolution | [Resolution_setting](#resolution_setting) |  | No |

#### Get_image

| Name | Type | Description | Required |
| ---- | ---- | ----------- | -------- |
| BacklightCompensationLevel | [Range_setting](#range_setting) |  | No |
| BacklightCompensationMode | [Enum_setting](#enum_setting) |  | No |
| Brightness | [Range_setting](#range_setting) |  | No |
| ColorSaturation | [Range_setting](#range_setting) |  | No |
| Contrast | [Range_setting](#range_setting) |  | No |
| ExposureGain | [Range_setting](#range_setting) |  | No |
| ExposureMaxGain | [Range_setting](#range_setting) |  | No |
| ExposureMode | [Enum_setting](#enum_setting) |  | No |
| ExposurePriority | [Enum_setting](#enum_setting) |  | No |
| ExposureTime | [Range_setting](#range_setting) |  | No |
| ExposureWindow | [Enum_setting](#enum_setting) |  | No |
| IrCutFilterMode | [Enum_setting](#enum_setting) |  | No |
| MaxExposureTime | [Range_setting](#range_setting) |  | No |
| MinExposureTime | [Range_setting](#range_setting) |  | No |
| Sharpness | [Range_setting](#range_setting) |  | No |
| WhiteBalanceMode | [Enum_setting](#enum_setting) |  | No |
| WhiteBalanceYbGain | [Range_setting](#range_setting) |  | No |
| WhiteBalanceYrGain | [Range_setting](#range_setting) |  | No |
| WideDynamicRangeLevel | [Range_setting](#range_setting) |  | No |
| WideDynamicRangeMode | [Enum_setting](#enum_setting) |  | No |

#### Range_setting

| Name | Type | Description | Required |
| ---- | ---- | ----------- | -------- |
| Max | string |  | Yes |
| Min | string |  | Yes |
| Value | string |  | Yes |

#### Enum_setting

| Name | Type | Description | Required |
| ---- | ---- | ----------- | -------- |
| AllowedValues | [ string ] |  | Yes |
| Value | string |  | Yes |

#### Resolution_setting

| Name | Type | Description | Required |
| ---- | ---- | ----------- | -------- |
| AllowedValues | [  ] |  | Yes |
| Value | object |  | Yes |

#### Set_encode

| Name | Type | Description | Required |
| ---- | ---- | ----------- | -------- |
| Bitrate | string |  | No |
| Encoding | string |  | No |
| EncodingInterval | string |  | No |
| FrameRate | string |  | No |
| GovLength | string |  | No |
| H264Profile | string |  | No |
| Quality | string |  | No |
| Resolution | object |  | No |

#### Set_image

| Name | Type | Description | Required |
| ---- | ---- | ----------- | -------- |
| BacklightCompensationLevel | string |  | No |
| BacklightCompensationMode | string |  | No |
| Brightness | string |  | No |
| ColorSaturation | string |  | No |
| Contrast | string |  | No |
| ExposureGain | string |  | No |
| ExposureMaxGain | string |  | No |
| ExposureMode | string |  | No |
| ExposurePriority | string |  | No |
| ExposureTime | string |  | No |
| ExposureWindow | string |  | No |
| IrCutFilterMode | string |  | No |
| MaxExposureTime | string |  | No |
| MinExposureTime | string |  | No |
| Sharpness | string |  | No |
| WhiteBalanceMode | string |  | No |
| WhiteBalanceYbGain | string |  | No |
| WhiteBalanceYrGain | string |  | No |
| WideDynamicRangeLevel | string |  | No |
| WideDynamicRangeMode | string |  | No |

#### Streams

| Name | Type | Description | Required |
| ---- | ---- | ----------- | -------- |
| additionalProperties | object |  | No |

#### Device_settings

| Name | Type | Description | Required |
| ---- | ---- | ----------- | -------- |
| Device ID | string | _Example:_ `"1c74d195-3fcd-48fa-b8a8-e64fe8ae2b1c"` | Yes |
| Encoding | string | _Example:_ `"H264"` | Yes |
| Encoding Profile | string | _Example:_ `"Baseline"` | Yes |
| Resolution | object |  | Yes |
| frameRate | string | _Example:_ `"30"` | Yes |

#### Stream_stats

| Name | Type | Description | Required |
| ---- | ---- | ----------- | -------- |
| additionalProperties | object |  | No |

#### Options

| Name | Type | Description | Required |
| ---- | ---- | ----------- | -------- |
| rtptransport | string | _Enum:_ `"udp"`, `"tcp"` | Yes |
| timeout | integer | _Example:_ `60` | Yes |
| streamId | string | _Example:_ `"0b6b62ea-b49a-49dc-906a-0010be31b7d7"` | Yes |

#### Error

| Name | Type | Description | Required |
| ---- | ---- | ----------- | -------- |
| error_code | string | _Example:_ `"VMSInternalError"` | Yes |
| error_message | string | _Example:_ `"VMS internal processing error"` | Yes |
