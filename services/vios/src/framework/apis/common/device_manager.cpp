/*
 * SPDX-FileCopyrightText: Copyright (c) 2021-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "device_manager.h"
#include "database.h"
#include "utils.h"
#include "vst_common.h"
#include "streamrecorder.h"

static string StreamTypeToString(StreamType streamType)
{
    switch (streamType)
    {
        case StreamType::Http:         return "Http";
        case StreamType::Hls:          return "Hls";
        case StreamType::Rtsp:         return "Rtsp";
        case StreamType::FileDownload: return "FileDownload";
        case StreamType::Udp:          return "Udp";
        case StreamType::Webrtc:       return "Webrtc";
        case StreamType::Native:       return "Native";
        case StreamType::NotSupported: return "NotSupported";
        default:                       return "Unknown";
    }
}

//DeviceConfig functions

DeviceConfig::DeviceConfig():recorded_video_root("./vst_video/")
            ,http_port("81")
            ,vst_data_path("./vst_data/")
            ,message_broker_topic("vst.event")
            ,message_broker_payload_key("sensor.id")
            ,message_broker_metadata_topic("test")
            ,redis_server_env_var("ROSIE_REDIS_SVC_SERVICE_HOST:6379")
            ,kafka_server_address("")
            ,mqtt_broker_address("")
            ,enable_notification(true)
            ,use_message_broker("")
            ,enable_notification_consumer(true)
            ,use_message_broker_consumer("")
            ,message_broker_topic_consumer("")
            ,video_metadata_server("")
            ,enable_gem_drawing(false)
            ,analytic_server_address("localhost:30080/emdat")
            ,calibration_file_path("")
            ,calibration_mode("synthetic")
            ,use_camera_groups(true)
            ,enable_recentering(true)
            ,floor_map_file_path("")
            ,overlay_3d_sensor_name("")
            ,overlay_text_font_type(DEFAULT_CUOSD_FONT_TYPE)
            ,bbox_tolerance_ms(0)
            ,enable_overlay_skip_frame(false)
            ,sensor_discovery_timeout(10)
            ,sensor_discovery_freq_secs(15)
            ,onvif_request_timeout_secs(10)
            ,onvif_sensor_time_sync_interval_secs(60)
            ,onvif_sensor_time_sync_compensation_ms(20)
            ,enable_perf_logging(true)
            ,max_webrtc_out_connections(8)
            ,max_webrtc_in_connections(8)
            ,storage_config_file("configs/vst_storage.json")
            ,total_video_storage_size_MB(10000)
            ,storage_threshold_percentage(95)
            ,storage_monitoring_frequency_secs(2)
            ,enable_aging_policy(true)
            ,max_video_download_size_MB(1000)
            ,always_recording(false)
            ,event_recording(false)
            ,event_record_length_secs (10)
            ,record_buffer_length_secs(2)
            ,use_software_path(false)
            ,use_software_encoder(false)
            ,use_webrtc_inbuilt_encoder("")
            ,webrtc_in_fixed_resolution("")
            ,webrtc_in_max_framerate(30)
            ,webrtc_in_video_bitrate_thresold_percentage(30)
            ,webrtc_in_passthrough(true)
            ,webrtc_sender_quality("pass_through")
            ,enable_rtsp_server_sei_metadata(false)
            ,enable_proxy_server_sei_metadata(false)
            ,enable_camera_auto_discovery(true)
            ,webservice_access_control_list("")
            ,rtsp_preferred_network_iface("")
            ,rtsp_server_port(-1)
            ,rtsp_server_instances_count(1)
            ,rtsp_server_use_socket_poll(false)
            ,rtcp_rtp_port_multiplex(true)
            ,rtsp_in_base_udp_port_num(-1)
            ,rtsp_out_base_udp_port_num(-1)
            ,rtsp_streaming_over_tcp(false)
            ,rtsp_server_reclamation_client_timeout_sec(-1)
            ,server_domain_name("")
            ,use_coturn_auth_secret(false)
            ,use_twilio_stun_turn(false)
            ,twilio_account_sid("")
            ,twilio_auth_token("")
            ,use_reverse_proxy(false)
            ,reverse_proxy_server_address("REVERSE_PROXY_SERVER_ADDRESS:100")
            ,use_sensor_ntp_time(false)
            ,max_sensors_supported(8)
            ,stream_monitor_interval_secs(2)
            ,udp_latency_ms(200)
            ,udp_drop_on_latency(false)
            ,webrtc_latency_ms(500)
            ,enable_frame_drop(false)
            ,video_metadata_query_batch_size_num_frames(300)
            ,enable_qos_monitoring(true)
            ,qos_logfile_path("./webroot/log/")
            ,qos_data_capture_interval_sec(1)
            ,qos_data_publish_interval_sec(5)
            ,enable_gst_debug_probes(true)
            ,rx_socket_buffer_size(DEFAULT_RX_SOCKET_BUFFER_SIZE)
            ,tx_socket_buffer_size(DEFAULT_TX_SOCKET_BUFFER_SIZE)
            ,tx_rtp_packet_size(DEFAULT_PROXYCLIENT_JITTER_BUFFER_SIZE_MS)
            ,enable_packet_pacing(false)
            ,rtp_packet_pace_time_us(1000)
            ,rtp_packet_batch_size(5)
            ,proxyclient_jitter_buffer_size_ms(200)
            ,enable_prometheus(false)
            ,prometheus_port(DEFAULT_PROMETHEUS_PORT)
            ,nv_streamer_directory_path("./")
            ,nv_streamer_loop_playback(true)
            ,nv_streamer_seekable(false)
            ,nv_streamer_sync_playback(false)
            ,nv_streamer_sync_file_count(-1)
            ,nv_streamer_max_upload_file_size_MB(10000)
            ,nv_streamer_rtsp_server_output_buffer_size_kb(800)
            ,default_bitrate(DEFAULT_BITRATE_KBPS)
            ,default_framerate(DEFAULT_FRAMERATE)
            ,default_resolution(DEFAULT_RESOLUTION)
            ,default_gov_length(DEFAULT_MAX_GOVLENGTH)
            ,default_profile(DEFAULT_PROFILE)
            ,default_quality(DEFAULT_QUALITY)
            ,default_encoding_interval(DEFAULT_ENCODING_INTERVAL)
            ,enable_highlighting_logs(true)
            ,enable_debug_apis(true)
            ,dump_webrtc_input_stats(true)
            ,enable_network_bandwidth_notification(false)
            ,enable_frameid_in_webrtc_stream(true)
            ,use_http_digest_authentication(true)
            ,use_multi_user(false)
            ,enable_user_cleanup(false)
            ,session_max_age_sec(DAYS_IN_SECONDS.count())
            ,use_https(true)
            ,use_rtsp_authentication(false)
            ,password_file_path("./.htpasswd")
            ,webrtc_peer_conn_timeout_sec(WEBRTC_PEER_CONN_TIMEOUT_SEC)
            ,use_video_metadata_protobuf(true)
            ,enable_grpc(true)
            ,grpc_server_port("50051")
            ,ai_bridge_endpoint("")
            ,webrtc_in_audio_sender_max_bitrate(128000)
            ,webrtc_in_video_degradation_preference("resolution")
            ,enable_websocket_pingpong(false)
            ,websocket_keep_alive_ms(5000)
            ,webrtc_in_video_sender_max_framerate(30)
            ,webrtc_out_enable_insert_sps_pps(false)
            ,webrtc_out_set_iframe_interval(0)
            ,webrtc_out_set_idr_interval (0)
            ,webrtc_out_min_drc_interval (5)
            ,webrtc_out_encode_fallback_option(WEBRTC_OUT_FALLBACK_SOFTWARE)
            ,remote_vst_address("")
            ,webrtc_port_range(Json::nullValue)
            ,webrtc_video_quality_tunning(Json::nullValue)
            ,device_name("")
            ,device_location("")
            ,enable_dec_low_latency_mode(false)
            ,enable_avsync_udp_input(false)
            ,use_standalone_udp_input(false)
            ,enable_silent_audio_in_udp_input(false)
            ,enable_udp_input_dump(false)
            ,enable_latency_logging(false)
            ,centralize_db_name("")
            ,centralize_db_username("")
            ,centralize_remote_db_password("")
            ,centralize_remote_db_hostaddr("")
            ,centralize_remote_db_port("")
            ,use_centralize_local_db(false)
            ,use_centralize_db(false)
            ,max_centralize_db_conn(10)
            ,restore_rtsp_streams_on_startup(false)
            ,webrtc_out_default_resolution("")
            ,enable_ipc_path(false)
            ,ipc_socket_path(DEFAULT_IPC_SOCKET_PATH)
            ,ipc_src_buffer_timestamp_copy (true)
            ,ipc_src_connection_attempts(5)
            ,ipc_src_connection_interval_us(1000000)
            ,ipc_sink_buffer_timestamp_copy(true)
            ,ipc_sink_buffer_copy(true)
            ,use_external_peerconnection(false)
            ,enable_mega_simulation(false)
            ,mega_simulation_delay_min_ms (0)
            ,mega_simulation_delay_max_ms (5000)
            ,mega_simulation_base_time("")
            ,default_file_expiry_minutes(DEFAULT_FILE_EXPIRY_MINUTES)
            ,ingress_endpoint(DEFAULT_INGRESS_ENDPOINT)
            ,use_webrtc_hw_dec(true)
            ,recorder_enable_frame_drop (true)
            ,recorder_max_frame_queue_size_bytes(16000000)
            ,recorder_low_latency(true)
            ,webrtc_out_enc_quality_tuning("ultra_low_latency")
            ,webrtc_out_enc_preset("ultra_fast")
            ,enable_drc (true)
            ,enable_loopback_multicast(false)
            ,tokkio_plugin_server_url("")
            ,update_record_details_in_sec(10)
            ,halo_safety_udp_port(-1)
            ,halo_safety_proximity_class("Forklift")
            ,halo_safety_text_size(0)
            ,halo_safety_active_text("")
            ,halo_safety_active_text_color("")
            ,halo_safety_active_text_bg_color("")
            ,halo_safety_inactive_text("")
            ,halo_safety_inactive_text_color("")
            ,halo_safety_inactive_text_bg_color("")
            ,enable_telemetry(false)
            ,otlp_endpoint("")
    {
    }

    void DeviceConfig::printInfo()
    {
        LOG2(info) << "\tHost HTTP port: "<< http_port << endl;
        LOG2(info) << "\tRecorded Video Root: "<< recorded_video_root << endl;
        LOG2(info) << "\tstunurl list: "<< vectorToString(stunurl_list) << endl;
        LOG2(info) << "\tstatic_turnurl_list: "<< vectorToString(static_turnurl_list) << endl;
        LOG2(info) << "\tuse_coturn_auth_secret: "<< use_coturn_auth_secret << endl;
        LOG2(info) << "\tuse_twilio_stun_turn: "<< use_twilio_stun_turn << endl;
#ifndef RELEASE
        LOG2(info) << "\tcoturn_turnurl_list_with_secret: "<< vectorToString(coturn_turnurl_list_with_secret) << endl;
        LOG2(info) << "\ttwilio_account_sid: "<< twilio_account_sid << endl;
        LOG2(info) << "\ttwilio_auth_token: "<< twilio_auth_token << endl;
#endif // !RELEASE
        LOG2(info) << "\tuse reverse proxy RP: "<< use_reverse_proxy << endl;
        LOG2(info) << "\treverse proxy server address: "<< reverse_proxy_server_address << endl;
        LOG2(info) << "\tNTP servers: "<< vectorToString(ntpServers) << endl;
        LOG2(info) << "\tUse sensor ntp time: "<< use_sensor_ntp_time << endl;
        LOG2(info) << "\tSensor Discovery Timeout(secs): "<< sensor_discovery_timeout << endl;
        LOG2(info) << "\tSensor Discovery Freq(secs): "<< sensor_discovery_freq_secs << endl;
        LOG2(info) << "\tOnvif request Timeout(secs): "<< onvif_request_timeout_secs << endl;
        LOG2(info) << "\tOnvif sensor time sync interval(secs): "<< onvif_sensor_time_sync_interval_secs << endl;
        LOG2(info) << "\tOnvif sensor time sync compensation(ms): "<< onvif_sensor_time_sync_compensation_ms << endl;
        LOG2(info) << "\tSensor Discovery Network Interfaces: "<< vectorToString(sensor_discovery_interfaces) << endl;
        LOG2(info) << "\tEnable perf logging: "<< enable_perf_logging << endl;
        LOG2(info) << "\tMax Webrtc output connections: "<< max_webrtc_out_connections << endl;
        LOG2(info) << "\tMax Webrtc input connections: "<< max_webrtc_in_connections << endl;
        LOG2(info) << "\tStorage Config file: "<< storage_config_file << endl;
        LOG2(info) << "\tTotal video storage size: "<< total_video_storage_size_MB << endl;
        LOG2(info) << "\tVST database path: "<< vst_data_path << endl;
        LOG2(info) << "\tstorage threshold percentage: " << storage_threshold_percentage << endl;
        LOG2(info) << "\tstorage monitoring frequency secs: " << storage_monitoring_frequency_secs << endl;
        LOG2(info) << "\tenable aging policy: " << enable_aging_policy << endl;
        LOG2(info) << "\tMax Video Download Size in MB: "<< max_video_download_size_MB << endl;
        LOG2(info) << "\tAlways Recording: "<< always_recording << endl;
        LOG2(info) << "\tEvent Recording: "<< event_recording << endl;
        LOG2(info) << "\tEvent Recording Length Secs: "<< event_record_length_secs << endl;
        LOG2(info) << "\tRecording Buffer Length Secs: "<< record_buffer_length_secs << endl;
        LOG2(info) << "\tUse Software Path: "<< use_software_path << endl;
        LOG2(info) << "\tUse Webrtc out inbuilt encoder: "<< use_webrtc_inbuilt_encoder << endl;
        LOG2(info) << "\tEnable rtsp server sei support: "<< enable_rtsp_server_sei_metadata << endl;
        LOG2(info) << "\tEnable Proxy server sei support: "<< enable_proxy_server_sei_metadata << endl;
        LOG2(info) << "\tWebrtc IN Resolution: "<< webrtc_in_fixed_resolution << endl;
        LOG2(info) << "\tWebrtc IN max frameRate: "<< webrtc_in_max_framerate << endl;
        LOG2(info) << "\tWebrtc IN video bitrate thresold: "<< webrtc_in_video_bitrate_thresold_percentage << endl;
        LOG2(info) << "\tWebrtc IN Pass-through mode: "<< webrtc_in_passthrough << endl;
        LOG2(info) << "\tWebrtc Sender Quality: "<< webrtc_sender_quality << endl;
        LOG2(info) << "\tIs camera auto discovery enabled ?: " << enable_camera_auto_discovery << endl;
        LOG2(info) << "\tWebservice allowed/denied Subnet/IP list?: " << webservice_access_control_list << endl;
        LOG2(info) << "\tRTSP preferred network interface: "<< rtsp_preferred_network_iface << endl;
        LOG2(info) << "\tRTSP server port: "<< rtsp_server_port << endl;
        LOG2(info) << "\tRTSP server instances: "<< rtsp_server_instances_count << endl;
        LOG2(info) << "\tRTSP server use socket poll: "<< rtsp_server_use_socket_poll << endl;
        LOG2(info) << "\tIncoming rtsp initial port: "<< rtsp_in_base_udp_port_num << endl;
        LOG2(info) << "\tRTCP RTP Port multiplex: "<< rtcp_rtp_port_multiplex << endl;
        LOG2(info) << "\tOutgoing rtsp initial port: "<< rtsp_out_base_udp_port_num << endl;
        LOG2(info) << "\trtsp streaming over tcp: "<< rtsp_streaming_over_tcp << endl;
        LOG2(info) << "\tRTSP server reclamation client timeout sec: "<< rtsp_server_reclamation_client_timeout_sec << endl;
        LOG2(info) << "\tServer domain name: "<< server_domain_name << endl;
        LOG2(info) << "\tEnable Notfication: "<< enable_notification << endl;
        LOG2(info) << "\tMessage Broker used: "<< use_message_broker << endl;
        LOG2(info) << "\tEnable Notfication Consumer: "<< enable_notification_consumer << endl;
        LOG2(info) << "\tMessage Broker used for consumer: "<< use_message_broker_consumer << endl;
        LOG2(info) << "\tNotification event: "<< message_broker_topic << endl;
        LOG2(info) << "\tMessage Broker payload key: "<< message_broker_payload_key << endl;
        LOG2(info) << "\tMessage Broker metadata topic: "<< message_broker_metadata_topic << endl;
        LOG2(info) << "\tMessage Broker topic for consumer: "<< message_broker_topic_consumer << endl;
        LOG2(info) << "\tRedis server env var: "<< redis_server_env_var << endl;
        LOG2(info) << "\tKafka Server address: "<< kafka_server_address << endl;
        LOG2(info) << "\tMQTT Broker address: "<< mqtt_broker_address << endl;
        LOG2(info) << "\tMax Sensors Supported: "<< max_sensors_supported << endl;
        LOG2(info) << "\tMax stream_monitor_interval (secs): "<< stream_monitor_interval_secs << endl;
        LOG2(info) << "\trtp_udp_port_range: " << rtp_udp_port_range << endl;
        LOG2(info) << "\tMax udp_latency_ms: "<< udp_latency_ms << endl;
        LOG2(info) << "\tudp_drop_on_latency: "<< udp_drop_on_latency << endl;
        LOG2(info) << "\twebRTC Latency ms: "<< webrtc_latency_ms << endl;
        LOG2(info) << "\tFrame Drop Enabled: "<< enable_frame_drop << endl;
        LOG2(info) << "\tVideo Metadata Server URL: " << video_metadata_server << endl;
        LOG2(info) << "\tCalibration File Path: " << calibration_file_path << endl;
        LOG2(info) << "\tCalibration Mode: " << calibration_mode << endl;
        LOG2(info) << "\tUse Camera Groups: " << use_camera_groups << endl;
        LOG2(info) << "\tEnable Recentering: " << enable_recentering << endl;
        LOG2(info) << "\tFloor Map File Path: " << floor_map_file_path << endl;
        LOG2(info) << "\t3D Overlay Sensor Name: " << overlay_3d_sensor_name << endl;
        LOG2(info) << "\tVideo Metadata Max results fetched: " << video_metadata_query_batch_size_num_frames << endl;
        LOG2(info) << "\tBbox tolerance in millisec: " << bbox_tolerance_ms << endl;
        LOG2(info) << "\tEnable Overlay Skip Frame: " << enable_overlay_skip_frame << endl;
        LOG2(info) << "\tVideo Use old specs for metadata parsing: " << use_video_metadata_protobuf << endl;
        LOG2(info) << "\tEnable QoS Monitoring: "<< enable_qos_monitoring << endl;
        LOG2(info) << "\tQoS logfile path: "<< qos_logfile_path << endl;
        LOG2(info) << "\tQoS data capture interval sec: "<< qos_data_capture_interval_sec << endl;
        LOG2(info) << "\tQoS data publish interval sec: "<< qos_data_publish_interval_sec << endl;
        LOG2(info) << "\tEnable GST debug Probes: "<< enable_gst_debug_probes << endl;
        LOG2(info) << "\tRecieve Socket Buffer Size: "<< rx_socket_buffer_size << endl;
        LOG2(info) << "\tSend Socket Buffer Size: "<< tx_socket_buffer_size << endl;
        LOG2(info) << "\tSend RTP Packet Size: "<< tx_rtp_packet_size << endl;
        LOG2(info) << "\tEnable Packet Pacing: "<< enable_packet_pacing << endl;
        LOG2(info) << "\tRTP Packet Pace Time us: "<< rtp_packet_pace_time_us << endl;
        LOG2(info) << "\tRTP Packet Batch Size: "<< rtp_packet_batch_size << endl;
        LOG2(info) << "\tProxyclient Jitter Buffer Size ms: "<< proxyclient_jitter_buffer_size_ms << endl;
        LOG2(info) << "\tEnable Prometheus: " << enable_prometheus << endl;
        LOG2(info) << "\tPrometheus Port: " << prometheus_port << endl;
        LOG2(info) << "\tNV Streamer directory path: "<< nv_streamer_directory_path << endl;
        LOG2(info) << "\tNV Streamer loop playback: "<< nv_streamer_loop_playback << endl;
        LOG2(info) << "\tNV Streamer max upload file size: "<< nv_streamer_max_upload_file_size_MB << endl;
        LOG2(info) << "\tNV Streamer seekable: "<< nv_streamer_seekable << endl;
        LOG2(info) << "\tNV Streamer Sync Playback: "<< nv_streamer_sync_playback << endl;
        LOG2(info) << "\tNV Streamer Sync File Count: "<< nv_streamer_sync_file_count << endl;
        LOG2(info) << "\tNV Streamer RTSP server buffer size kb: "<< nv_streamer_rtsp_server_output_buffer_size_kb << endl;
        LOG2(info) << "\tNV Streamer supported containers: "<< vectorToString(media_containers) << endl;
        LOG2(info) << "\tVideo codecs supported: "<< vectorToString(video_codecs) << endl;
        LOG2(info) << "\tAudio codecs supported: "<< vectorToString(audio_codecs) << endl;
        LOG2(info) << "\tBitrate Kbps: " << default_bitrate << endl;
        LOG2(info) << "\tFramerate: " << default_framerate << endl;
        LOG2(info) << "\tResolution: " << default_resolution << endl;
        LOG2(info) << "\tGOV Length: " << default_gov_length << endl;
        LOG2(info) << "\tProfile: " << default_profile << endl;
        LOG2(info) << "\tQuality: " << default_quality << endl;
        LOG2(info) << "\tEncoding Interval: " << default_encoding_interval << endl;
        LOG2(info) << "\tEnable Highlighting logs: " << enable_highlighting_logs << endl;
        LOG2(info) << "\tEnable debug options: " << enable_debug_apis << endl;
        LOG2(info) << "\tEnable dump webrtc input stats: " << dump_webrtc_input_stats << endl;
        LOG2(info) << "\tEnable network bandwidth notification: " << enable_network_bandwidth_notification << endl;
        LOG2(info) << "\tEnable frameId in webrtc stream: " << enable_frameid_in_webrtc_stream << endl;
        LOG2(info) << "\tUse HTTP digest authentication: " << use_http_digest_authentication << endl;
        LOG2(info) << "\tUse multi user: " << use_multi_user << endl;
        LOG2(info) << "\tEnable user cleanup: " << enable_user_cleanup << endl;
        LOG2(info) << "\tSession max age sec: " << session_max_age_sec << endl;
        LOG2(info) << "\tMulti user extra flags: " << vectorToString(multi_user_extra_options) << endl;
        LOG2(info) << "\tUse HTTPS authentication: " << use_https << endl;
        LOG2(info) << "\tNv org ID: " << nv_org_id << endl;
        LOG2(info) << "\tNv NGC kry: " << nv_ngc_key << endl;
        LOG2(info) << "\tUse RTSP authentication: " << use_rtsp_authentication << endl;
        if (webrtc_video_quality_tunning != Json::nullValue)
        {
            LOG2(info) << "\twebrtc_video_quality_tunning: " << webrtc_video_quality_tunning.toStyledString() << endl;
        }
        LOG2(info) << "\twebrtc_peer_conn_timeout_sec: " << webrtc_peer_conn_timeout_sec << endl;
        LOG2(info) << "\tenable_grpc: " << enable_grpc << endl;
        LOG2(info) << "\tgrpc_server_port: " << grpc_server_port << endl;
        LOG2(info) << "\tAI Bridge Endpoint: " << ai_bridge_endpoint << endl;
        LOG2(info) << "\twebrtc_in_audio_sender_max_bitrate: " << webrtc_in_audio_sender_max_bitrate << endl;
        LOG2(info) << "\twebrtc_in_video_degradation_preference: " << webrtc_in_video_degradation_preference << endl;
        LOG2(info) << "\tenable_websocket_pingpong: " << enable_websocket_pingpong << endl;
        LOG2(info) << "\twebsocket_keep_alive_ms: " << websocket_keep_alive_ms << endl;
        LOG2(info) << "\twebrtc_in_video_sender_max_framerate: " << webrtc_in_video_sender_max_framerate << endl;
        LOG2(info) << "\tgpu_indices: " << vectorToString(gpu_indices) << endl;
        LOG2(info) << "\tRemote VST address: " << remote_vst_address << endl;
        LOG2(info) << "\tEnable latency logging: "<< enable_latency_logging << endl;
        LOG2(info) << "\tWebRTC Out Encode Fallback Mechanism (software/pass_through): "<< webrtc_out_encode_fallback_option << endl;
#ifdef JETSON_PLATFORM
        LOG2(info) << "\tEnable IPC Path: "<< enable_ipc_path << endl;
        LOG2(info) << "\tIPC Socket Path: "<< ipc_socket_path << endl;
        LOG2(info) << "\tIPC Src Buffer Timestamp Copy: "<< ipc_src_buffer_timestamp_copy << endl;
        LOG2(info) << "\tIPC Src Connection Attempts: "<< ipc_src_connection_attempts << endl;
        LOG2(info) << "\tIPC Src Connection Interval: "<< ipc_src_connection_interval_us << endl;
        LOG2(info) << "\tIPC Sink Buffer Timestamp Copy: "<< ipc_sink_buffer_timestamp_copy << endl;
        LOG2(info) << "\tIPC Sink Buffer Copy: "<< ipc_sink_buffer_copy << endl;
#endif
        LOG2(info) << "\tEnable MEGA Simulation: "<< enable_mega_simulation << endl;
        LOG2(info) << "\tMEGA Simulation Min Delay: "<< mega_simulation_delay_min_ms << endl;
        LOG2(info) << "\tMEGA Simulation Max Delay: "<< mega_simulation_delay_max_ms << endl;
        LOG2(info) << "\tMEGA Simulation Base Time: "<< mega_simulation_base_time << endl;
        LOG2(info) << "\tDefault file expiry minutes: "<< default_file_expiry_minutes << endl;
        LOG2(info) << "\tDefault ingress endpoint: "<< ingress_endpoint << endl;
        LOG2(info) << "\tUse Webrtc HW decoder: "<< use_webrtc_hw_dec << endl;
        LOG2(info) << "\tEnable frame drop in Stream Recorder: "<< recorder_enable_frame_drop << endl;
        LOG2(info) << "\tMax frame queue size in Stream Recorder: "<< recorder_max_frame_queue_size_bytes << endl;
        LOG2(info) << "\tEnable low latency Recording pipeline: "<< recorder_low_latency << endl;
        LOG2(info) << "\tanalytic_server_address: " << analytic_server_address << endl;
        LOG2(info) << "\twebrtc_out_enc_quality_tuning (ultra_low_latency / low_latency / high_quality): " << webrtc_out_enc_quality_tuning << endl;
        LOG2(info) << "\twebrtc_out_enc_preset(ultra_fast / fast / slow): " << webrtc_out_enc_preset << endl;
        LOG2(info) << "\tenable drc: " << enable_drc << endl;
        LOG2(info) << "\tEnable loopback multicast: " << enable_loopback_multicast << endl;
        LOG2(info) << "\ttokkio plugin server url: " << tokkio_plugin_server_url << endl;
        LOG2(info) << "\tUpdate Record Details in sec: " << update_record_details_in_sec << endl;
        LOG2(info) << "\tHalo Safety UDP Port: " << halo_safety_udp_port << endl;
        LOG2(info) << "\tHalo Safety Proximity Class: " << halo_safety_proximity_class << endl;
        LOG2(info) << "\tHalo Safety Text Size: " << halo_safety_text_size << endl;
        LOG2(info) << "\tHalo Safety Active Text: " << halo_safety_active_text << endl;
        LOG2(info) << "\tHalo Safety Active Text Color: " << halo_safety_active_text_color << endl;
        LOG2(info) << "\tHalo Safety Active Text BG Color: " << halo_safety_active_text_bg_color << endl;
        LOG2(info) << "\tHalo Safety Inactive Text: " << halo_safety_inactive_text << endl;
        LOG2(info) << "\tHalo Safety Inactive Text Color: " << halo_safety_inactive_text_color << endl;
        LOG2(info) << "\tHalo Safety Inactive Text BG Color: " << halo_safety_inactive_text_bg_color << endl;

        LOG2(info) << "\tEnable Cloud Storage: " << enable_cloud_storage << endl;
        LOG2(info) << "\tCloud Storage Type: " << cloud_storage_type << endl;
        LOG2(info) << "\tCloud Storage Endpoint: " << cloud_storage_endpoint << endl;
        LOG2(info) << "\tCloud Storage Bucket: " << cloud_storage_bucket << endl;
        LOG2(info) << "\tCloud Storage Region: " << cloud_storage_region << endl;
        LOG2(info) << "\tCloud Storage Use SSL: " << cloud_storage_use_ssl << endl;
        LOG2(info) << "\tDownload Files Timeout Secs: " << download_files_timeout_secs << endl;
        LOG2(info) << "\tPicture API Timeout Secs: " << picture_api_timeout_secs << endl;
        LOG2(info) << "\tTelemetry Enabled: " << (enable_telemetry ? "true" : "false") << endl;
        LOG2(info) << "\tOTLP Endpoint: " << otlp_endpoint << endl;
    }

//DeviceManager functions

DeviceManager::DeviceManager() : name ("")
        , id ("")
        , ip ("")
        , user ("")
        , password ("")
        , port ("")
        , url ("")
        , type ("")
        , isOnline (false)
        , isRemoteDevice (false)
        , isRtspAdaptor (false)
        , isError (false)
        , enabled (false)
        , needRtspServer (false)
        , m_isRtspServerReady (false)
        , needStreamMonitoring (false)
        , needRecording (false)
        , needStorageMngt (false)
        , m_sensorControlobjectPair (nullptr, nullptr)
        , httpStatusCode (-1)
        , m_callback (nullptr)
        , m_lastAccessTime(0)
    {
        sensors.clear();
        m_sensorDiscoveryObjectPairList.clear();
    }

DeviceManager::~DeviceManager()
{
    LOG(info) << "Calling ~DeviceManager" << endl;

    clearSensorList();

    destroyControlObject_t delObject = (destroyControlObject_t) m_sensorControlobjectPair.second;
    if (m_sensorControlobjectPair.first != nullptr && delObject != nullptr)
    {
        delObject(m_sensorControlobjectPair.first);
        m_sensorControlobjectPair.first = nullptr;
        m_sensorControlobjectPair.second = nullptr;
    }

    LOG(info) << "Exited from ~DeviceManager" << endl;
}

void DeviceManager::registerCallback(cb_ptr_t cb)
{
    m_callback = cb;
}

map<string, shared_ptr<SensorInfo>, std::less<>> DeviceManager::getSensors()
{
    std::lock_guard<std::mutex> sensorsLock(m_sensorsMutex);
    return sensors;
}

int DeviceManager::getSensorsSize()
{
    std::lock_guard<std::mutex> sensorsLock(m_sensorsMutex);
    return sensors.size();
}

bool DeviceManager::isSpaceForNewSensor()
{
    if (type == TYPE_STREAMER)
    {
        LOG(info) << "streamer device, skipping limit check" << endl;
        return true;
    }

    const int max_supported_count = GET_CONFIG().max_sensors_supported;

    // Source the count from the shared DB so this path matches
    // checkMaxSensorsLimit on every build flavour. In scaled deployments
    // this also avoids cache drift between streamprocessing-ms and
    // sensor-ms (bug 6167064); in monolith it keeps a single source of
    // truth for the limit check.
    const int dbSensorCount = GET_DB_INSTANCE()->CountSensorDetails(getDeviceId());
    if (dbSensorCount >= 0)
    {
        const bool hasSpace = dbSensorCount < max_supported_count;
        LOG(info) << "source=DB, deviceId=" << getDeviceId()
                  << ", dbCount=" << dbSensorCount
                  << ", max=" << max_supported_count
                  << ", hasSpace=" << hasSpace << endl;
        return hasSpace;
    }
    LOG(warning) << "CountSensorDetails failed for deviceId=" << getDeviceId()
                 << ", falling back to in-memory cache count" << endl;

    // Count every cached sensor so this path agrees with CountSensorDetails
    // and checkMaxSensorsLimit. Filtering by SensorStatusOnline would let an
    // offline sensor silently free a slot.
    std::lock_guard<std::mutex> sensorsLock(m_sensorsMutex);
    const int current_sensor_count = static_cast<int>(sensors.size());
    const bool hasSpace = current_sensor_count < max_supported_count;
    LOG(info) << "source=cache, deviceId=" << getDeviceId()
              << ", cacheCount=" << current_sensor_count
              << ", max=" << max_supported_count
              << ", hasSpace=" << hasSpace << endl;
    return hasSpace;
}

void DeviceManager::clearSensorList()
{
    std::lock_guard<std::mutex> sensorsLock(m_sensorsMutex);
    sensors.clear();
}

string DeviceManager::createUniqueName(string name)
{
    int i = 0;
    string str = name;
    while(isNameExists(name))
    {
        ++i;
        name = str + string("_") + std::to_string(i);
    }
    return name;
}

// TODO: Don't take name and location from config. Do proper implementation
void DeviceManager::updateDeviceDetails()
{
    updateDeviceId();
    updateDeviceName();
    updateDeviceLocation();
}

// Runs once at VST boot time to update the device ID
void DeviceManager::updateDeviceId()
{
    int retries = RETRIES_FOR_DEVICE_ID;
    const string existingDeviceId = GET_DB_INSTANCE()->getLocalDeviceId();
    // Device ID is not set, try to set it.
    if (existingDeviceId.empty())
    {
        bool setSuccess = false;
        const string newDeviceId = generate_uuid();

        while (retries > 0)
        {
            int ret = GET_DB_INSTANCE()->setLocalDeviceId(newDeviceId);

            if (ret == 0)
            {
                id = newDeviceId;
                LOG(info) << "Device ID set: " << newDeviceId << endl;
                setSuccess = true;
                break;
            }
            else
            {
                // Setting Device ID failed. Try to see if another instance has already set the ID
                const string deviceIdFromDB = GET_DB_INSTANCE()->getLocalDeviceId();
                if (!deviceIdFromDB.empty())
                {
                    id = deviceIdFromDB;
                    LOG(info) << "Device ID fetched from DB after failed set: " << deviceIdFromDB << endl;
                    setSuccess = true;
                    break;
                }

                LOG(error) << "Failed to set unique Device ID. Retries left: " << (retries - 1) << endl;
                --retries;
            }
        }

        if (!setSuccess)
        {
            LOG(error) << "Unable to set unique Device ID after multiple attempts." << endl;
        }
    }
    else
    {
        id = existingDeviceId;
        LOG(info) << "Device ID already present: " << existingDeviceId << endl;
    }
}

void DeviceManager::updateDeviceName()
{
    if (id.empty())
    {
        LOG(error) << "Failed to set Device Name, ID is not set" << endl;
        return;
    }
    const string existingDeviceName =  GET_DB_INSTANCE()->getLocalDeviceName();
    // Update Device name in DB if its changed in config
    if (existingDeviceName.empty() || GET_CONFIG().device_name != existingDeviceName)
    {
        int ret = GET_DB_INSTANCE()->setLocalDeviceName(GET_CONFIG().device_name, id);
        if (ret != 0)
        {
            LOG(error) << "Unable to set Device Name" << endl;
            return;
        }
        name = GET_CONFIG().device_name;
    }
    else
    {
        name = existingDeviceName;
        LOG(info) << "Device Name already present: " << existingDeviceName << endl;
    }
}

void DeviceManager::updateDeviceLocation()
{
    if (id.empty())
    {
        LOG(error) << "Failed to set Device Location, ID is not set" << endl;
        return;
    }
    const string existingDeviceLocation =  GET_DB_INSTANCE()->getLocalDeviceLocation();
    // Update Device location in DB if its changed in config
    if (existingDeviceLocation.empty() || GET_CONFIG().device_location != existingDeviceLocation)
    {
        int ret = GET_DB_INSTANCE()->setLocalDeviceLocation(GET_CONFIG().device_location, id);
        if (ret != 0)
        {
            LOG(error) << "Unable to set Device Name" << endl;
            return;
        }
        location = GET_CONFIG().device_location;
    }
    else
    {
        location = existingDeviceLocation;
        LOG(info) << "Device Location already present: " << existingDeviceLocation << endl;
    }
}

void DeviceManager::addSensorList(vector<shared_ptr<SensorInfo>> list)
{
    std::lock_guard<std::mutex> sensorsLock(m_sensorsMutex);
    if ((type != TYPE_EVENT) && m_callback)
    {
        sensors.clear();
        for (unsigned int i = 0; i < list.size(); i++)
        {
            shared_ptr<SensorInfo> sensor = list[i];
            if (sensor->type != SENSOR_TYPE_CSI)
            {
                sensor->name = createUniqueName(sensor->name);
            }
            sensors[sensor->id] = sensor;
            m_callback(id, sensor, false);
        }
    }
}

string DeviceManager::addSensor(shared_ptr<SensorInfo>& sensor)
{
    string unique_id = sensor->id;
    std::lock_guard<std::mutex> sensorsLock(m_sensorsMutex);
    if((type == TYPE_VST || type == TYPE_STREAMER) && m_callback)
    {
        if (sensor->id.empty())
        {
            unique_id = generate_uuid();
            sensor->id = unique_id;
            sensor->name = createUniqueName(sensor->name);
        }

        sensors[unique_id] = sensor;
        // Not adding UDP sensor to DB
        // Not adding webcam/tokkio stream to DB
        if (sensor->type != SENSOR_TYPE_UDP && !(sensor->type == SENSOR_TYPE_WEBRTC && !sensor->isRemoteSensor))
        {
            m_callback(id, sensor, false);
        }
        else
        {
            LOG(info) << "skip adding sensor to DB" << endl;
        }
    }
    return unique_id;
}

bool DeviceManager::sensorExists(const string& id)
{
    std::lock_guard<std::mutex> sensorsLock(m_sensorsMutex);
    map<string, shared_ptr<SensorInfo>, std::less<>>::iterator it = sensors.find(id);
    return it != sensors.end();
}

shared_ptr<SensorInfo> DeviceManager::findSensor(const string& sensor_id)
{
    std::lock_guard<std::mutex> sensorsLock(m_sensorsMutex);
    shared_ptr<SensorInfo> sensor = nullptr;
    map<string, shared_ptr<SensorInfo>, std::less<>>::iterator it = sensors.end();
    for( it = sensors.begin(); it != sensors.end(); ++it )
    {
        sensor = it->second;
        if (sensor->sensorId == sensor_id)
        {
            return sensor;
        }
    }
    return nullptr;
}

bool DeviceManager::isNameExists(const string& name)
{
    map<string, shared_ptr<SensorInfo>, std::less<>>::iterator it;
    for( it = sensors.begin(); it != sensors.end(); ++it )
    {
        shared_ptr<SensorInfo> existing_sensor = it->second;
        if (name.compare(existing_sensor->name) == 0)
        {
            return true;
        }
    }
    return false;
}


int DeviceManager::updateSensorInfo(const SensorInfo& in_sensor)
{
    if (in_sensor.id.empty())
    {
        LOG(error) << "Invalid sensor ID provided for update" << endl;
        return -1;
    }

    int ret = 0;
    shared_ptr<SensorInfo> sensor;
    bool needsCallback = false;

    {
        sensor = getSensor(in_sensor.id);
        std::lock_guard<std::mutex> sensorsLock(m_sensorsMutex);
        if (sensor)
        {
            try
            {
                sensor->name = createUniqueName(in_sensor.name);
                sensor->name = (in_sensor.name.empty() == false) ? in_sensor.name : sensor->name;
                sensor->hardware = (in_sensor.hardware.empty() == false) ? in_sensor.hardware : sensor->hardware;
                sensor->manufacturer = (in_sensor.manufacturer.empty() == false) ? in_sensor.manufacturer : sensor->manufacturer;
                sensor->location = (in_sensor.location.empty() == false) ? in_sensor.location : sensor->location;
                sensor->tags = (in_sensor.tags.empty() == false) ? in_sensor.tags : sensor->tags;
                sensor->serial_number = (in_sensor.serial_number.empty() == false) ? in_sensor.serial_number : sensor->serial_number;
                sensor->firmware_version = (in_sensor.firmware_version.empty() == false) ? in_sensor.firmware_version : sensor->firmware_version;
                sensor->hardware_id = (in_sensor.hardware_id.empty() == false) ? in_sensor.hardware_id : sensor->hardware_id;
                sensor->position.direction = (in_sensor.position.direction.empty() == false) ? in_sensor.position.direction : sensor->position.direction;
                sensor->position.depth = (in_sensor.position.depth.empty() == false) ? in_sensor.position.depth : sensor->position.depth;
                sensor->position.fieldOfView = (in_sensor.position.fieldOfView.empty() == false) ? in_sensor.position.fieldOfView : sensor->position.fieldOfView;
                // update primary stream name if sensor name is changed
                sensor->position.origin.first = (in_sensor.position.origin.first.empty() == false) ? in_sensor.position.origin.first : sensor->position.origin.first;
                sensor->position.origin.second = (in_sensor.position.origin.second.empty() == false) ? in_sensor.position.origin.second : sensor->position.origin.second;
                sensor->position.geoLocation.first = (in_sensor.position.geoLocation.first.empty() == false) ? in_sensor.position.geoLocation.first : sensor->position.geoLocation.first;
                sensor->position.geoLocation.second = (in_sensor.position.geoLocation.second.empty() == false) ? in_sensor.position.geoLocation.second : sensor->position.geoLocation.second;
                sensor->position.coordinates.first = (in_sensor.position.coordinates.first.empty() == false) ? in_sensor.position.coordinates.first : sensor->position.coordinates.first;
                sensor->position.coordinates.second = (in_sensor.position.coordinates.second.empty() == false) ? in_sensor.position.coordinates.second : sensor->position.coordinates.second;

                std::vector<shared_ptr<StreamInfo>> streams = sensor->getStreams();
                if (streams.size() > 0)
                {
                    streams[0]->name = sensor->name;
                }

                needsCallback = (type == TYPE_VST || type == TYPE_MMS || type == TYPE_STREAMER) && m_callback;
            }
            catch (const std::exception& e)
            {
                LOG(error) << "Error updating sensor info: " << e.what() << endl;
                ret = -2;
            }
        }
        else
        {
            LOG(error) << "Sensor not found for update: " << in_sensor.id << endl;
            ret = -1;
        }
    }

    // Perform callback outside of lock
    if (needsCallback && ret == 0)
    {
        m_callback(id, sensor, true);
    }

    return ret;
}

void DeviceManager::setPTZInfoIntoSensorInfo(string id, map<PTZAction, ptzRange> ptz)
{
    std::lock_guard<std::mutex> sensorsLock(m_sensorsMutex);
    shared_ptr<SensorInfo> sensor;
    map<string, shared_ptr<SensorInfo>, std::less<>>::iterator it = sensors.find(id);
    if( it != sensors.end())
    {
        sensors[id]->ptzInfo = ptz;
    }
}

shared_ptr<SensorInfo> DeviceManager::getSensor(const string id)
{
    std::lock_guard<std::mutex> sensorsLock(m_sensorsMutex);
    map<string, shared_ptr<SensorInfo>, std::less<>>::iterator it = sensors.find(id);
    if( it != sensors.end())
    {
        return it->second;
    }
    return nullptr;
}

shared_ptr<StreamInfo> DeviceManager::getStream(const string sensor_id, const string stream_id)
{
    {
        std::lock_guard<std::mutex> sensorsLock(m_sensorsMutex);
        map<string, shared_ptr<SensorInfo>, std::less<>>::iterator it = sensors.find(sensor_id);
        if( it != sensors.end())
        {
            shared_ptr<SensorInfo> sensor = it->second;
            vector<shared_ptr<StreamInfo>> streams = sensor->streams;
            for (uint32_t i = 0; i < streams.size(); i++)
            {
                if(streams[i]->id == stream_id)
                {
                    return streams[i];
                }
            }
        }
    }

    shared_ptr<SensorInfo> sensor = getSensorInfo(sensor_id);
    if (sensor)
    {
        vector<shared_ptr<StreamInfo>> streams = sensor->getStreams();
        for (uint32_t i = 0; i < streams.size(); i++)
        {
            if(streams[i]->id == stream_id)
            {
                return streams[i];
            }
        }
    }

    return nullptr;
}

void DeviceManager::deleteSensor(const string id)
{
    LOG(info) << "delete sensor: " << id << endl;
    std::lock_guard<std::mutex> sensorsLock(m_sensorsMutex);
    map<string, shared_ptr<SensorInfo>, std::less<>>::iterator it;
    it = sensors.find(id);
    if( it != sensors.end())
    {
        LOG(info) << "deleted sensor.... " << id << endl;
        sensors.erase(it);
    }
    else
    {
        LOG(error) << "Sensor not found " << id << endl;
    }
}

void DeviceManager::removeStream(const string& url)
{
    LOG(info) << "remove stream: " << secureUrlForLogging(url) << endl;
    std::lock_guard<std::mutex> sensorsLock(m_sensorsMutex);
    map<string, shared_ptr<SensorInfo>, std::less<>>::iterator it;
    for( it = sensors.begin(); it != sensors.end(); ++it )
    {
        shared_ptr<SensorInfo>& sensor = it->second;
        vector<shared_ptr<StreamInfo>>& streams = sensor->getStreams();
        std::vector<shared_ptr<StreamInfo>>::iterator iter;
        for (iter = streams.begin(); iter != streams.end(); )
        {
            shared_ptr<StreamInfo> stream = *iter;
            if(stream->live_url == url)
            {
                iter =  streams.erase(iter);
                return;
            }
            else
            {
                ++iter;
            }
        }
    }
}

void DeviceManager::removeStream(const string& stream_id, const string& sensor_id)
{
    if (stream_id.empty() || sensor_id.empty())
    {
        LOG(error) << "Stream ID or sensor ID is empty" << endl;
        return;
    }

    std::lock_guard<std::mutex> sensorsLock(m_sensorsMutex);

    // Find the sensor by ID
    auto sensor_it = std::find_if(sensors.begin(), sensors.end(),
        [&sensor_id](const auto& sensor_pair)
        {
            return sensor_pair.second->id == sensor_id;
        });

    if (sensor_it == sensors.end())
    {
        LOG(warning) << "Sensor not found: " << sensor_id << endl;
        return; // Sensor not found
    }

    auto& sensor = sensor_it->second;
    auto& streams = sensor->getStreams();

    // Use remove_if with erase to efficiently remove the stream
    auto remove_condition = [&stream_id, &sensor_id](const auto& stream)
    {
        return stream->id == stream_id && stream->sensorId == sensor_id;
    };

    auto new_end = std::remove_if(streams.begin(), streams.end(), remove_condition);

    if (new_end != streams.end())
    {
        streams.erase(new_end, streams.end());
        LOG(info) << "Removed stream: " << stream_id << " from sensor: " << sensor_id << endl;
    }
}

void DeviceManager::removeStreamOrSensor(const string& stream_id)
{
    if (stream_id.empty())
    {
        LOG(error) << "Stream ID or sensor ID is empty" << endl;
        return;
    }

    string sensor_id;
    if (!getSensorIdFromStreamId(stream_id, sensor_id))
    {
        LOG(warning) << "Failed to get sensor ID from stream ID: " << stream_id << endl;
        return;
    }

    // If stream ID equals sensor ID, delete the entire sensor
    if (stream_id == sensor_id)
    {
        LOG(info) << "Stream ID equals sensor ID, deleting entire sensor: " << sensor_id << endl;
        deleteSensor(sensor_id);
    }
    else
    {
        // Otherwise, just remove the stream
        LOG(info) << "Removing stream: " << stream_id << " from sensor: " << sensor_id << endl;
        removeStream(stream_id, sensor_id);
    }
}

map<PTZAction, ptzRange> DeviceManager::getSensorPTZInfo(const string id)
{
    shared_ptr<SensorInfo> sensor = getSensor(id);
    std::lock_guard<std::mutex> sensorsLock(m_sensorsMutex);
    map<PTZAction, ptzRange> ptz;
    if(sensor)
    {
        ptz = sensor->ptzInfo;
    }
    return ptz;
}

void DeviceManager::updateSensorListFromDB()
{
    std::lock_guard<std::mutex> sensorsLock(m_sensorsMutex);
    map<string, shared_ptr<SensorInfo>, std::less<>>::iterator it;
    LOG(verbose) << "updateSensorListFromDB" << endl;
    if (type == TYPE_VST || type == TYPE_MMS || type == TYPE_STREAMER)
    {
        vector<SensorDetailsDBColumns> rowArray =  GET_DB_INSTANCE()->readSensorDetails(id);
        for (uint32_t i =0; i < rowArray.size(); i++)
        {
            SensorDetailsDBColumns row = rowArray[i];
            it = sensors.find(row.sensor_id_value);
            if(it != sensors.end())
            {
                continue;
            }

            shared_ptr<SensorInfo> sensor (new SensorInfo);
            GET_DB_INSTANCE()->getSensorInfoFromDB(sensor, row);

            LOG(info) << "Added sensor to cache from DB: " << sensor->id << endl;

            sensors[sensor->id] = sensor;
        }
    }
}

vector<shared_ptr<SensorInfo>> DeviceManager::getSensorList(bool fetchFromDB)
{
    vector<shared_ptr<SensorInfo>> list;
    vector<SensorDetailsDBColumns> rowArray;

    if (fetchFromDB && (type == TYPE_VST || type == TYPE_MMS || type == TYPE_STREAMER))
    {
        // Get data from DB first
        rowArray = GET_DB_INSTANCE()->readSensorDetails(id);
    }

    {
        std::lock_guard<std::mutex> sensorsLock(m_sensorsMutex);

        if (fetchFromDB && (type == TYPE_VST || type == TYPE_MMS || type == TYPE_STREAMER))
        {
            // Process DB results
            for (const auto& row : rowArray)
            {
                auto newSensor = std::make_shared<SensorInfo>();
                if (VmsErrorCode::NoError == GET_DB_INSTANCE()->getSensorInfoFromDB(newSensor, row))
                {
                    list.push_back(newSensor);
                }
            }

            // store all sensors in a map
            for (const auto& sensor : list)
            {
                /* Restore onvif service URLs */
                auto it = sensors.find(sensor->id);
                if (it != sensors.end() && it->second != nullptr)
                {
                    if (!it->second->serviceUrls.empty())
                    {
                        sensor->serviceUrls = it->second->serviceUrls;
                        sensor->serviceCapabilities = it->second->serviceCapabilities;
                    }
                }

                sensors[sensor->id] = sensor;
            }

            #if 0
            // TODO: WE need to implement properly when we support scaling streamprocessing service
            // Remove stale cache entries that no longer exist in DB
            std::set<string> dbSensorIds;
            for (const auto& sensor : list)
            {
                dbSensorIds.insert(sensor->id);
            }
            for (auto it = sensors.begin(); it != sensors.end(); )
            {
                const auto& cached = it->second;
                bool isNonDbSensor = cached &&
                    (cached->type == SENSOR_TYPE_UDP ||
                     (cached->type == SENSOR_TYPE_WEBRTC && !cached->isRemoteSensor));

                if (!isNonDbSensor && dbSensorIds.find(it->first) == dbSensorIds.end())
                {
                    LOG(info) << "Removing stale sensor from cache: " << it->first << endl;
                    it = sensors.erase(it);
                }
                else
                {
                    ++it;
                }
            }
            #endif
        }
        else
        {
            // Get sensors from cache
            for (const auto& pair : sensors)
            {
                list.push_back(pair.second);
            }
        }
    }

    return list;
}

vector<shared_ptr<StreamInfo>> DeviceManager::getStreamList(bool fetchFromDB)
{
    vector<shared_ptr<StreamInfo>> list;
    map<string, shared_ptr<SensorInfo>, std::less<>>::iterator it;
    if (fetchFromDB && (type == TYPE_VST || type == TYPE_MMS || type == TYPE_STREAMER))
    {
        vector<SensorDetailsDBColumns> rowArray = GET_DB_INSTANCE()->readSensorDetails(id);
        for (const auto& row : rowArray)
        {
            auto newSensor = std::make_shared<SensorInfo>();
            if (VmsErrorCode::NoError == GET_DB_INSTANCE()->getSensorInfoFromDB(newSensor, row))
            {
                vector<shared_ptr<StreamInfo>> streams = newSensor->getStreams();
                for(uint32_t j = 0; j < streams.size(); j++)
                {
                    if(streams[j])
                    {
                        list.push_back(streams[j]);
                    }
                }
            }
        }
    }
    else
    {
        std::lock_guard<std::mutex> sensorsLock(m_sensorsMutex);
        for( it = sensors.begin(); it != sensors.end(); ++it )
        {
            std::shared_ptr<SensorInfo> sensor = it->second;
            if(sensor)
            {
                vector<shared_ptr<StreamInfo>> streams = sensor->getStreams();
                for(uint32_t j = 0; j < streams.size(); j++)
                {
                    if(streams[j])
                    {
                        list.push_back(streams[j]);
                    }
                }
            }
        }
    }
    return list;
}

void DeviceManager::replaceSensor(const string& old_id, const string& new_id)
{
    std::lock_guard<std::mutex> sensorsLock(m_sensorsMutex);

    map<string, shared_ptr<SensorInfo>, std::less<>>::iterator it = sensors.find(new_id);
    if( it == sensors.end())
    {
        LOG(error) << "new sensor not found" << endl;
        return;
    }

    sensors[old_id] = sensors[new_id];
    sensors.erase(it);
}

void DeviceManager::printInfo()
{
    LOG2(info) << "\tAdaptor name: "<< name << endl;
    LOG2(info) << "\tAdaptor id: "<< id << endl;
    LOG2(info) << "\tAdaptor ip: "<< ip << endl;
    LOG2(info) << "\tAdaptor user: "<< maskSensitiveData(user, MaskType::USERNAME) << endl;
    LOG2(info) << "\tAdaptor password: "<< maskSensitiveData(password, MaskType::PASSWORD) << endl;
    LOG2(info) << "\tAdaptor port: "<< port << endl;
    LOG2(info) << "\tAdaptor type: "<< type << endl;
    LOG2(info) << "\tAdaptor url: "<< secureUrlForLogging(url) << endl;
    LOG2(info) << "\tAdaptor enabled: "<< enabled<< endl;
    LOG2(info) << "\tNeed RTSP Server?: "<< needRtspServer<< endl;
    LOG2(info) << "\tNeed Stream Monitoring?: "<< needStreamMonitoring<< endl;
    LOG2(info) << "\tNeed Recording?: "<< needRecording<< endl;
    LOG2(info) << "\tNeed Storage Management?: "<< needStorageMngt<< endl;
    LOG2(info) << "\tAdaptor isOnline: "<< isOnline << endl;
    LOG2(info) << "" << endl;
}

void DeviceManager::deviceManagerInit(ModuleId module_id)
{
    updateDeviceDetails();
    clearSensorList();
    registerCallback(vst_common::updateSensorDetailsToDB);
    updateSensorListFromDB();
}

string DeviceManager::getDeviceId()
{
    return id;
}

string DeviceManager::getDeviceName()
{
    return name;
}

string DeviceManager::getDeviceLocation()
{
    return location;
}

string DeviceManager::getDeviceType()
{
    return type;
}

bool DeviceManager::isDeviceRemote()
{
    return isRemoteDevice;
}

bool DeviceManager::getSensorIdFromStreamId(const string& streamId, string &sensorId)
{
    if (streamId.empty())
    {
        LOG(error) << "Stream ID is empty" << endl;
        return false;
    }

    std::vector<shared_ptr<SensorInfo>> sensors = getSensorList();
    for (shared_ptr<SensorInfo> sensor : sensors)
    {
        vector<shared_ptr<StreamInfo>> streams = sensor->getStreams();
        for (shared_ptr<StreamInfo> stream : streams)
        {
            if (stream->id == streamId)
            {
                sensorId = sensor->id;
                return true;
            }
        }
    }

    /* Get all streams from DB */
    vector<SensorStreamsDBColumns> streamArray = GET_DB_INSTANCE()->readAllStreams();
    for (uint32_t cnt = 0; cnt < streamArray.size(); cnt++)
    {
        if (streamArray[cnt].stream_id_value == streamId)
        {
            sensorId = streamArray[cnt].sensor_id_value;
            return true;
        }
    }

    LOG(error) << "Stream not found: " << streamId << endl;

    return false;
}

shared_ptr<SensorInfo> DeviceManager::getSensorInfo(const string& sensor_id, bool fetchFromDB)
{
    if (type == TYPE_EVENT)
    {
        return nullptr;
    }

    if (sensor_id.empty())
    {
        LOG(warning) << "Invalid sesnor id received" << endl;
        return nullptr;
    }

    if (!fetchFromDB)
    {
        // First try to get from cache
        shared_ptr<SensorInfo> sensor = getSensor(sensor_id);
        if (sensor)
        {
            return sensor;
        }
    }

    // If not in cache, try to get from DB
    SensorDetailsDBColumns row = GET_DB_INSTANCE()->readSensorDetails(getDeviceId(), sensor_id);
    if (!row.sensor_id_value.empty())
    {
        // Double check if sensor was added to cache while we were querying DB
        {
            if (!fetchFromDB)
            {
                std::lock_guard<std::mutex> sensorsLock(m_sensorsMutex);
                auto it = sensors.find(sensor_id);  // Changed to use sensor_id instead of row.sensor_id_value
                if (it != sensors.end())
                {
                    return it->second;
                }
            }
        }

        // Create new sensor from DB data
        auto newSensor = std::make_shared<SensorInfo>();
        if (VmsErrorCode::NoError != GET_DB_INSTANCE()->getSensorInfoFromDB(newSensor, row))
        {
            LOG(error) << "Failed to get sensor info from DB for sensor_id: " << sensor_id << endl;
            return nullptr;
        }

        // Add to cache
        {
            std::lock_guard<std::mutex> sensorsLock(m_sensorsMutex);
            sensors[newSensor->id] = newSensor;
            LOG(info) << "Added sensor to cache from DB: " << newSensor->id << endl;
            return newSensor;
        }
    }

    #if 0
    // TODO: WE need to implement properly when we support scaling streamprocessing service
    // Sensor not in DB -- remove from cache if stale
    {
        std::lock_guard<std::mutex> sensorsLock(m_sensorsMutex);
        auto it = sensors.find(sensor_id);
        if (it != sensors.end())
        {
            LOG(info) << "Removing stale sensor from cache (not in DB): " << sensor_id << endl;
            sensors.erase(it);
        }
    }
    #endif

    LOG(warning) << "Sensor not found in cache or DB: " << sensor_id << endl;
    return nullptr;
}

shared_ptr<SensorInfo> DeviceManager::searchSensor(const string& id)
{
    if (id.empty())
    {
        LOG(error) << "Invalid search: empty id" << endl;
        return nullptr;
    }

    // First check in memory cache
    {
        std::lock_guard<std::mutex> sensorsLock(m_sensorsMutex);
        for (const auto& sensorPair : sensors)
        {
            const auto& sensor = sensorPair.second;
            if (!sensor)
            {
                continue;
            }

            // Check sensor ID
            if (sensor->id == id)
            {
                return sensor;
            }

            // Check stream IDs
            const auto& streams = sensor->getStreams();
            for (const auto& stream : streams)
            {
                if (stream && stream->id == id)
                {
                    return sensor;
                }
            }
        }
    }

    // If not found in cache, check DB
    try
    {
        auto rowArray = GET_DB_INSTANCE()->readSensorDetails(getDeviceId());
        for (const auto& row : rowArray)
        {
            // Check if this row matches our search ID
            if (row.sensor_id_value == id)
            {
                // Also need to check streams in DB for this sensor
                vector<SensorStreamsDBColumns> streamArray = GET_DB_INSTANCE()->readAllStreams();
                bool foundInStreams = false;
                for (const auto& stream : streamArray)
                {
                    if (stream.stream_id_value == id && stream.sensor_id_value == row.sensor_id_value)
                    {
                        foundInStreams = true;
                        break;
                    }
                }
                if (!foundInStreams)
                {
                    continue;
                }

                // Double-check cache in case another thread added it
                {
                    std::lock_guard<std::mutex> sensorsLock(m_sensorsMutex);
                    auto it = sensors.find(row.sensor_id_value);
                    if (it != sensors.end())
                    {
                        return it->second;
                    }
                }

                // Create new sensor from DB data
                auto newSensor = std::make_shared<SensorInfo>();
                if (VmsErrorCode::NoError != GET_DB_INSTANCE()->getSensorInfoFromDB(newSensor, row))
                {
                    LOG(error) << "Failed to get sensor info from DB for id: " << id << endl;
                    continue;
                }

                // Add to cache
                {
                    std::lock_guard<std::mutex> sensorsLock(m_sensorsMutex);
                    sensors[newSensor->id] = newSensor;
                    LOG(info) << "Added sensor to cache from DB: " << newSensor->id << endl;
                    return newSensor;
                }
            }
        }
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Exception while searching sensor: " << e.what() << ", id: " << id << endl;
        return nullptr;
    }

    LOG(warning) << "Sensor not found for id: " << id << endl;
    return nullptr;
}

void DeviceManager::removeStreamsFromSensor(const string &sensor_id)
{
    shared_ptr<SensorInfo> sensor = getSensorInfo(sensor_id);
    if (sensor)
    {
        vector<shared_ptr<StreamInfo>> streams = sensor->getStreams();
        for (shared_ptr<StreamInfo> stream : streams)
        {
            GET_DB_INSTANCE()->deleteRowStream(stream->id);
            StreamRecorder* recorder = GET_RECORDER();
            if (recorder)
            {
                LOG(info) << "Removing stream from recorder" << endl;
                vst_recorder::removeStream(stream->id);
            }
        }
        sensor->clearStreams();
    }
}

void DeviceManager::updateSensorStatus(const SensorStatus& status)
{
    LOG(info) << "Updating sensor status and getting stream info if password present" << endl;
    shared_ptr<SensorInfo> sensor = getSensorInfo(status.sensorId);
    if (sensor)
    {
        sensor->updateSensorStatus(status.event);
        sensor->updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(NoError));
        if (sensor->getSensorStatus() != SensorStatusOnline)
        {
            sensor->updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(CameraNotFoundError));

            vector<shared_ptr<StreamInfo>> streams = sensor->getStreams();
            for (uint32_t j = 0; j < streams.size(); j++)
            {
                shared_ptr<StreamInfo> stream = streams[j];
                if (stream->stream_type == StreamType::Native)
                {
#ifdef ENABLE_NATIVE_STREAM_MONITOR
                    NativeStreamMonitor::getInstance()->removeNativeStream(stream);
                    stream->live_proxy_url.clear();
#endif
                }
                else
                {
                    StreamMonitor* streamMonitor = StreamMonitor::getInstance();
                    streamMonitor->removeStream(stream);
                }
            }
        }
        else
        {
            Json::Value details = readDeviceDetails();
            Json::Value details_array = details["devices"];
            if (details_array.isArray())
            {
                Json::Value creds = getSensorDetails(details_array, sensor->sensorId);
                if (creds != Json::nullValue)
                {
                    string user = creds.get("username", "").asString();
                    string password = creds.get("password", "").asString();
                    if (!user.empty() && !password.empty())
                    {
                        sensor->updateCredentials(user, password);
                    }
                }
            }
        }
    }
    else
    {
        LOG(error) << "Sensor not found: id: " << status.sensorId << endl;
    }
}

Json::Value DeviceManager::getSensorDetails(Json::Value& array, const string& in_id)
{
    Json::Value out = Json::nullValue;
    for (Json::Value::ArrayIndex i = 0; i != array.size(); i++)
    {
        out = array[i];
        string id = out.get("id", "").asString();
        if (id == in_id)
        {
            return out;
        }
    }
    return Json::nullValue;
}

bool DeviceManager::requiredRtspServer()
{
    return needRtspServer;
}

bool DeviceManager::requiredRecording()
{
    return needRecording;
}

bool DeviceManager::requiredStorageMngt()
{
    return needStorageMngt;
}

std::shared_ptr<SensorInfo> DeviceManager::addOrUpdateSensor(const SensorInfo& in_sensor)
{
    std::shared_ptr<SensorInfo> sensor = nullptr;
    sensor = findSensor(in_sensor.sensorId);
    if(sensor)
    {
        if(sensor->ip != in_sensor.ip)
        {
            LOG(info) << "Updating sensor with old ip " << sensor->ip << " --> " << in_sensor.ip << endl;
            sensor->ip = in_sensor.ip;
            sensor->url = in_sensor.url;
            sensor->clearStreams();
            sensor->clearServiceUrls();
        }
        else
        {
            if(sensor->ip.empty()) // WAR for Metadata sensors
            {
                sensor->metadata = in_sensor.metadata;
            }
            LOG(info) << "Same sensor is found with same IP " << in_sensor.ip << endl;
        }
    }
    else
    {
        LOG(info) << "Creating new sensor: ip: " << in_sensor.ip << endl;
        sensor = std::make_shared<SensorInfo>();
        *sensor = in_sensor;
    }
    addSensor(sensor);

    return sensor;
}

//StreamInfo functions

StreamInfo::StreamInfo (): live_url("")
                   ,replay_url("")
                   ,live_proxy_url("")
                   ,name("")
                   ,id("")
                   ,sensorId("")
                   ,isMainStream(false)
                   ,storageLocation(StreamStorageTypeLocal)
                   ,stream_type(StreamType::Rtsp)
                   ,direction(StreamDirectionOut)
                   ,duration(-1)
                   ,eStatusCode(StreamStatus::STREAM_STATUS_UNKNOWN, UNKNOWN_STRING)
    {
    }
    void StreamInfo::printInfo()
    {
        LOG2(info) << "\t id: " << id << endl;
        LOG2(info) << "\t Sensor id: " << sensorId << endl;
        LOG2(info) << "\t Is Main Stream: " << isMainStream << endl;
        LOG2(info) << "\t Stream Type: " << stream_type << endl;
        LOG2(info) << "\t Profile Name: " << name << endl;
#ifndef RELEASE
        LOG2(info) << "\t Live url: " << secureUrlForLogging(live_url) << endl;
#endif
        LOG2(info) << "\t Live proxy url: " << secureUrlForLogging(live_proxy_url) << endl;
        LOG2(info) << "\t Replay url: " << secureUrlForLogging(replay_url) << endl;
        LOG2(info) << "\t Stream resolution: " << settings.encoderValues.resolution.width << "x"<< settings.encoderValues.resolution.height << endl;
        LOG2(info) << "\t Stream frame rate: " << settings.encoderValues.frameRate << endl;
        LOG2(info) << "\t Stream encoding: " << settings.encoderValues.encoding << endl;
        LOG2(info) << "\t Stream encoding profile: " << settings.encoderValues.encodingProfile << endl;
        LOG2(info) << "\t Stream encoding bitrate: " << settings.encoderValues.bitrate << endl;
        LOG2(info) << "\t Stream encoding encodingInterval: " << settings.encoderValues.encodingInterval << endl;
        LOG2(info) << "\t Stream number of frames: " << settings.encoderValues.numFrames << endl;
        LOG2(info) << "\t Stream encoding govLength: " << settings.encoderValues.govLength << endl;
        LOG2(info) << "\t Stream encoding quality: " << settings.encoderValues.quality << endl;
        LOG2(info) << "\t Stream Duration: " << duration << endl;
        LOG2(info) << "\t Stream Storage Location: " << StreamStorageTypeToString(storageLocation) << endl;
        LOG2(info) << "\t Stream error " << eStatusCode.first << ":" << eStatusCode.second << endl;
        LOG2(info) << "" << endl;
    }

    void StreamInfo::updateStreamtype(const StreamType type)
    {
        std::lock_guard<std::mutex> guard(m_streamLock);
        stream_type = type;

        SensorStreamsDBColumns stream_row =  GET_DB_INSTANCE()->readSensorStreams(id);
        if (!stream_row.stream_id_value.empty())
        {
            stream_row.streamType_value = stream_type;
            GET_DB_INSTANCE()->insertRowStream(stream_row);
        }
    }

    void StreamInfo::updateErrorStatus(const std::pair<StreamStatus, string> error, bool updateDB)
    {
        std::lock_guard<std::mutex> guard(m_streamLock);
        if (error.first == STREAM_STATUS_ONLINE && eStatusCode.first == STREAM_STATUS_STREAMING)
        {
            // No need to update the status since already in streaming play mode.
            return;
        }
        eStatusCode = error;

        // Only update database if requested - skip DB operations during bulk reads
        if (updateDB)
        {
            SensorStreamsDBColumns stream_row =  GET_DB_INSTANCE()->readSensorStreams(id);
            if (!stream_row.stream_id_value.empty())
            {
                stream_row.streamStatus_value = eStatusCode.first;
                GET_DB_INSTANCE()->insertRowStream(stream_row);
            }
        }
    }

    std::pair<StreamStatus, string> StreamInfo::getErrorStatus()
    {
        std::lock_guard<std::mutex> guard(m_streamLock);
        return eStatusCode;
    }

    void StreamInfo::updateVideoEncoderValues(const SensorVideoEncoderSettingsValues& values, bool updateDB)
    {
        std::lock_guard<std::mutex> guard(m_streamLock);
        settings.encoderValues = values;

        if (updateDB)
        {
            SensorStreamsDBColumns stream_row =  GET_DB_INSTANCE()->readSensorStreams(id);
            if (!stream_row.stream_id_value.empty())
            {
                stream_row.encoding_value = settings.encoderValues.encoding;
                stream_row.frameRate_value = settings.encoderValues.frameRate;
                stream_row.resolution_value = settings.encoderValues.resolution.getString();
                stream_row.encodingProfile_value = settings.encoderValues.encodingProfile;
                stream_row.numFrames_value = settings.encoderValues.numFrames;
                stream_row.bitrate_value = settings.encoderValues.bitrate;
                stream_row.isBframesPresent_value = settings.encoderValues.isBframesPresent ? 1 : 0;
                GET_DB_INSTANCE()->insertRowStream(stream_row);
            }
        }
    }

    SensorVideoEncoderSettingsValues& StreamInfo::getvideoEncoderValues()
    {
        std::lock_guard<std::mutex> guard(m_streamLock);
        return settings.encoderValues;
    }

    void StreamInfo::updateVideoEncoderOptions(const SensorEncoderSettingsOptions& options)
    {
        std::lock_guard<std::mutex> guard(m_streamLock);
        settings.encoderOptions = options;
    }

    SensorEncoderSettingsOptions& StreamInfo::getvideoEncoderOptions()
    {
        std::lock_guard<std::mutex> guard(m_streamLock);
        return settings.encoderOptions;
    }

    void StreamInfo::updateAudioEncoderValues(const SensorAudioEncoderSettingsValues& values, bool updateDB)
    {
        std::lock_guard<std::mutex> guard(m_streamLock);
        settings.audioEncoderValues = values;

        if (updateDB)
        {
            SensorStreamsDBColumns stream_row =  GET_DB_INSTANCE()->readSensorStreams(id);
            if (!stream_row.stream_id_value.empty())
            {
                stream_row.audio_container_value   = values.container;
                stream_row.audio_encoding_value    = values.encoding;
                stream_row.audio_sample_rate_value = values.sample_rate;
                stream_row.audio_bps_value         = values.bits_per_sample;
                stream_row.audio_channels_value    = values.channels;
                GET_DB_INSTANCE()->insertRowStream(stream_row);
            }
        }
    }

    SensorAudioEncoderSettingsValues& StreamInfo::getAudioEncoderValues()
    {
        std::lock_guard<std::mutex> guard(m_streamLock);
        return settings.audioEncoderValues;
    }

    void StreamInfo::updateImageValues(const SensorImageSettingsValues& values)
    {
        std::lock_guard<std::mutex> guard(m_streamLock);
        settings.imageValues = values;
    }

    Json::Value StreamInfo::toJson(bool isStreamerDevice)
    {
        Json::Value stream_info;
        Json::Value metadata;

        // Basic stream info
        stream_info["name"] = name;
        stream_info["streamId"] = id;
        stream_info["isMain"] = isMainStream;
        stream_info["storageLocation"] = StreamStorageTypeToString(storageLocation);
        stream_info["url"] = live_proxy_url;
        stream_info["type"] = StreamTypeToString(stream_type);

        // Add VOD URL for non-streamer devices
        if (!isStreamerDevice) {
            stream_info["vodUrl"] = replay_url;
        }

        // Add IPC URL if enabled
#ifdef JETSON_PLATFORM
        if (GET_CONFIG().enable_ipc_path)
        {
            stream_info["ipc_url"] = "ipc://" + GET_CONFIG().ipc_socket_path + id;
        }
#endif

        // Stream metadata
        metadata["resolution"] = settings.encoderValues.resolution.getString();
        metadata["codec"] = settings.encoderValues.encoding;
        metadata["bitrate"] = settings.encoderValues.bitrate;
        metadata["framerate"] = settings.encoderValues.frameRate;
        metadata["govlength"] = settings.encoderValues.govLength;
        stream_info["metadata"] = metadata;

        return stream_info;
    }

void SensorMetadata::printInfo()
{
    map<string, string, std::less<>>::iterator it;
    for (it = data.begin(); it != data.end(); it++)
    {
        LOG2(info) << "\t\t"<< it->first << ":" << it->second << endl;
    }
}

UserInfo::UserInfo (): username("") {}

//Resolution functions

void Resolution::operator=(const string& value)
{
    const string token("x");
    if (value.find(token) != string::npos)
    {
        vector<string> arr = splitString(value, token);
        if (arr.size() >= 2)
        {
            if (isNumber(arr[0]) && isNumber(arr[1]))
            {
                width = arr[0];
                height= arr[1];
            }
        }
    }
}

bool Resolution::operator==(const Resolution& res)
{
    if (res.width.empty() || res.height.empty())
    {
        return false;
    }
    try
    {
        uint32_t w = std::stoi(this->width);
        uint32_t h = std::stoi(this->height);
        uint32_t w_ = std::stoi(res.width);
        uint32_t h_ = std::stoi(res.height);
        if (w == w_ && h == h_)
        {
            return true;
        }
    }
    catch(const std::exception& e)
    {
        std::cerr << e.what() << '\n';
    }
    return false;
}
bool Resolution::empty() const
{
    if (width.empty() || height.empty())
    {
        return true;
    }
    return false;
}
string Resolution::getString() const
{
    if(!width.empty() && !height.empty())
    {
        return (width + "x" + height);
    }
    else
    {
        return EMPTY_STRING;
    }
}

int Resolution::getPixels() const
{
    int nWidth = stringToInt(width, 0);
    int nHeight = stringToInt(height, 0);
    return nWidth * nHeight;
}

//SensorStatus functions
 SensorStatus::SensorStatus() : event(SensorStatusUnknown)
                    , sensorId("")
                    , timeStamp("")
                    , type("")
                    , sensorName("")
                    {}

string  SensorStatus::getEventString(const SensorStatusEvent event)
{
    switch(event)
    {
        case SensorStatusOffline:
            return "SensorStatusOffline";
        case SensorStatusOnline:
            return "SensorStatusOnline";
        case SensorStatusStreaming:
            return "SensorStatusStreaming";
        default:
            return "SensorStatusUnknown";
    }
}

//SensorPosition functions
SensorPosition::SensorPosition():origin(std::make_pair("",""))
                     , geoLocation(std::make_pair("",""))
                     , coordinates(std::make_pair("",""))
                     , direction("")
                     , fieldOfView("")
                     , depth("")
{
}

void SensorPosition::operator=(const SensorPosition& pos)
{
    origin = pos.origin;
    geoLocation = pos.geoLocation;
    coordinates = pos.coordinates;
    direction = pos.direction;
    fieldOfView = pos.fieldOfView;
    depth = pos.depth;
}

void SensorPosition::printInfo()
{
    LOG2(info) << "\t Origin: " << origin.first <<"," << origin.second << endl;
    LOG2(info) << "\t Geo Location: " << geoLocation.first <<"," << geoLocation.second << endl;
    LOG2(info) << "\t Coordinates: " << coordinates.first <<"," << coordinates.second << endl;
    LOG2(info) << "\t Direction: " << direction << endl;
    LOG2(info) << "\t Field of view: " << fieldOfView << endl;
    LOG2(info) << "\t Depth: " << depth << endl;
}
