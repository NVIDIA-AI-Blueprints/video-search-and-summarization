ucf.svc.vms
==============================

## Description
UCF vms Microservice.
NVIDIA’s Video Storage Toolkit (VST) delivers a complete solution for Video Management. A VST is responsible for a few critical tasks in end to end solutions that leverage devices as sensors. The VST allows end users to add or remove devices, configure, and control them and create a policy driven storage and archival of the video feeds at appropriate media granularity. The VST enables retrieval of previously stored video content. Finally, it enables local and remote dashboards to play one or more such streams synchronously. AI IVA solutions need to access such device streams in real time streaming analytics as well as retrieval and visualization.

### Input:

Camera RTSP stream
Raw udp stream
Webrtc input
Video File input

### Output:

Proxy RTSP video/audio stream
Webrtc output stream
Recorded stream


### Features:

1. Supports ONVIF S-profile devices, ONVIF discovery with control and data flow.
2. Add devices manually by IP address and/or RTSP URLs
3. Video storage and aging policy
4. RTSP proxy url in pass-through and multi-cast mode
5. Media streaming using webRTC protocol for live and recorded videos.
6. Provide RESTful APIs to write client application to control and configure VST.
7. Support Redis message bus to publish device add/remove events.
8. Prometheus/Grafana integration to publish some VST stats.
9. Provide sample web-based client.
10. Configure devices remotely
11. Policy driven video recording
12. Derive insights about the current status of VST.
13. Webrtc input stream support


## Usage

###Params:

vms:
  configs:
    vst_config.json:
      network:
        rtsp_streaming_over_tcp: true
        udp_latency_ms: 200
        udp_drop_on_latency: false
        stunurl_list:
          - stun.l.google.com:19302
          - stun1.l.google.com:19302
        # List of turnUrls with static credentials. Example - admin:admin@10.0.0.1:3478
        static_turnurl_list:
          - admin:admin@13.56.254.166:3478
        # List of coturn turnUrls with secret. Example - 10.0.0.1:3478:secret_key
        use_coturn_auth_secret: false
        coturn_turnurl_list_with_secret:
        # Twilio account details userId & auth_token.
        use_twilio_stun_turn: false
        twilio_account_sid: "ACafbcfa1a2b27f48b5df04bdd69f48809"
        twilio_auth_token: "d60a5a972a3e8c7ca7260fe198c1fdd4"
        ntp_servers:
        max_webrtc_out_connections: 8
        max_webrtc_in_connections: 1
        enable_grpc: false
        grpc_server_port: 50051
        webrtc_in_audio_sender_max_bitrate: 128000
        webrtc_in_video_degradation_preference: "resolution"
        webrtc_in_video_sender_max_framerate: 30
        webrtc_in_video_bitrate_thresold_percentage: 50
      data:
        always_recording: true
        webrtc_in_fixed_resolution: "1280x720"
        webrtc_in_max_framerate: 30
      notifications:
        enable_notification: true
        use_message_broker: redis
        # Redis stream name
        message_broker_topic: vst_events
        redis_server_env_var: REDIS_SVC_SERVICE_HOST:6379
    vst_storage.json
      total_video_storage_size_MB: 100000
    rtsp_streams.json:
      streams:
      - enabled: true
        stream_in: udp
        name: Tokkio_Avatar
        video:
          codec: h264
          framerate: 30
          port: 30031
        audio:
          bits_per_sample: 32
          codec: pcm
          enabled: true
          port: 30032
          sample_rate_Hz: 44100

With vst_config.json, Various vms configuration params can be controlled such as:
- udp/tcp streaming over rtp
- stun/turn server configs
- webrtc streaming configs
With rtsp_streams.json,
- udp ports & its config can be modified, which is used for Avatar stream from A2F MS
- rtsp streams also can be added in the vms in this file. Streaming will be ready for these streams as soon as vms moves to running state.
 
### Connections:

connections:
  vms/redis: redis-timeseries/redis
  maxine-anim-a2f/udp-video-out: vms/udp-video-in
  maxine-anim-a2f/udp-audio-out: vms/udp-audio-in

webrtc input stream (webcam) --> vms (RTSP) --> ds-lipactivity/maxin-audio OR
RTSP(camera stream) --> vms (Proxy RTSP) --> ds-lipactivity/maxin-audio
maxine-anim-a2f (UDP stream) --> vms (webrtc stream) --> tokkio-ui
vms --> redis --> <DOWNSTREAM>

## Performance
8 rtsp streams @30 FPS
1 webrtc input/out, 720p @30 FPS


## Supported Platforms
x86 dGPU
aarch64

## Deployment requirements

1. Make sure k8s foundational services are running


## License
Nvidia properiatery

## Known Issues / Limitations
None

