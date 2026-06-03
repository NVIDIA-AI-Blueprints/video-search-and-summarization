# VST Web Streamer

A TypeScript library that simplifies WebRTC streaming for VST (Video Storage Toolkit) applications. Provides a streamlined API for real-time video streaming with automatic retry mechanisms and cross-browser compatibility.

## Features

- WebRTC-based video streaming (inbound/outbound)
- WebSocket connection management with automatic retry
- Support for live, replay, and StreamBridge use cases
- Cross-browser compatibility via webrtc-adapter

## Installation

```bash
npm install vst-streaming-lib
```

## Quick Start

```typescript
import StreamManager, { StreamType } from 'vst-streaming-lib';

const streamManager = new StreamManager();

// Configure the stream
streamManager.updateConfig({
  inboundStreamVideoElementId: 'video-element-id',
  vstWebsocketEndpoint: 'ws://your-server.com/vms/ws',
  streamType: StreamType.Live,
  enableLogs: true,
  successCallback: () => console.log('Stream started'),
  errorCallback: () => console.log('Stream failed')
});

// Start streaming
streamManager.startStreaming({
  streamId: 'your-stream-id',
  options: {
    rtptransport: 'udp',
    timeout: 60,
    quality: 'auto'
  }
});

// Stop streaming
streamManager.stopStreaming();
```

## API Reference

### Configuration

```typescript
streamManager.updateConfig({
  // Required
  inboundStreamVideoElementId: string,
  vstWebsocketEndpoint: string,
  streamType: 'live' | 'replay' | 'streambridge',
  
  // Optional
  outboundStreamVideoElementId?: string,
  connectionId?: string,
  enableMicrophone?: boolean,
  enableCamera?: boolean,
  enableLogs?: boolean,
  enableWebsocketPing?: boolean,
  websocketTimeoutMS?: number,
  
  // Callbacks
  successCallback?: () => void,
  errorCallback?: () => void,
  closeCallback?: () => void,
  firstFrameReceivedCallback?: () => void
});
```

### Streaming

```typescript
// Start streaming
streamManager.startStreaming({
  streamId?: string,           // For live/replay streams
  startTime?: string,          // For replay streams (UTC)
  endTime?: string,            // For replay streams (UTC)
  options?: {
    rtptransport: 'udp',
    timeout: 60,
    quality: 'auto' | 'low' | 'medium' | 'high' | 'pass-through'
  }
});

// Stop streaming
streamManager.stopStreaming();
```

### Utility Methods

```typescript
// Get peer connection objects
const inbound = streamManager.getInboundPeerConnectionObject();
const outbound = streamManager.getOutboundPeerConnectionObject();

// Get peer IDs
const inboundId = streamManager.getInboundStreamPeerId();
const outboundId = streamManager.getOutboundStreamPeerId();

// Send custom WebSocket message
streamManager.sendCustomWebsocketMessage(message);

// Get configurations
const streamConfig = streamManager.getStreamConfig();
const libConfig = streamManager.getConfig();
```

## Use Cases

### Live Streaming
Stream live video from VST sensors.

### Replay Streaming
Playback recorded video with specified time range.

### StreamBridge
Real-time bidirectional streaming (e.g., digital human avatars).

## Development

```bash
# Clone and build
git clone <repository>
cd vst-web-streamer
npm install
npm run build

# Link for development
npm link
cd /path/to/your/project
npm link vst-streaming-lib
```
