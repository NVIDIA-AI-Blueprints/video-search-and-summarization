ucf.svc.nvstreamer
==============================

## Description
UCF nvstreamer Microservice.
Nvstreamer is complete RTSP server application.
It can stream several kinds of media file. These media files can be either stored on filesystem or they can be uploaded runtime using web-ui.
These streams can be received/played by standards-compliant RTSP/RTP media clients.

### Input:

Media Files to be streamed

### Output:

RTSP stream
Webrtc stream

### Features:

1. Media file streaming.
2. Provide sample web-based client.
3. File upload feature
4. Tagging feature in the web-ui.

## Usage

###Params:

vms:
    configs:
      vst_config.json:
        data:
          nv_streamer_loop_playback: true
          nv_streamer_seekable: false
          nv_streamer_max_upload_file_size_MB: 10000
          nv_streamer_media_container_supported:
          - mp4
          - mkv
          supported_video_codecs:
          - h264
          supported_audio_codecs:
          - pcmu
        network:
          turnurl:
          - admin:nvidia123@10.24.143.97:3478

With vst_config.json, Various nvstreamer configuration params can be updated such as loop plaback, upload size, stun/turn urls etc.
 
### Connections:

connections:

FILE(video stream) --> Nvstreamer (RTSP server) --> rtsp client

## Performance
20 rtsp streams out @30 FPS

## Supported Platforms
x86 dGPU

## Deployment requirements

1. Make sure k8s foundational services are running


## License
Apache License, Version 2.0. Copyright (c) 2021-2022 NVIDIA CORPORATION

## Known Issues / Limitations

1. CPU Utilization => 
