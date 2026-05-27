# Technical Documentation

This folder contains comprehensive technical documentation for the VMS Shim project, focusing on architecture, design patterns, and implementation details.

## Contents

### 1. Video Source Pipeline Architecture

#### XML Format (`video_source_pipeline_architecture.xml`)
A comprehensive XML block diagram documenting the refactored video source pipeline architecture, including:

- **High-Level Architecture**: CommonVideoSource, PipelineManager, and PipelineBuilder components
- **Pipeline Types**: Single stream and composite pipeline variants
- **Component Hierarchy**: Source, processing, and output components
- **Data Flow Patterns**: Standard, pass-through, composite, and image capture flows
- **Pipeline Selection Logic**: Decision trees for pipeline type selection
- **Lifecycle Management**: Creation, startup, operation, and shutdown phases
- **Error Handling**: Strategies for graceful degradation and recovery
- **Performance Considerations**: Hardware acceleration and optimization strategies

#### HTML Format (`video_source_pipeline_architecture.html`)
An interactive, web-friendly version of the documentation with:

- **Responsive Design**: Mobile-friendly layout with modern UI
- **Interactive Navigation**: Tab-based navigation between sections
- **Visual Hierarchy**: Color-coded components and sections
- **Flow Diagrams**: Visual representation of data flows
- **Decision Trees**: Interactive pipeline selection logic
- **Component Cards**: Detailed component information with capabilities
- **Professional Styling**: Modern gradient backgrounds and hover effects

## Architecture Overview

The refactored video source architecture implements the **Builder Pattern** with clean separation of concerns:

```
CommonVideoSource (Controller)
    ↓
PipelineManager (Orchestrator)
    ↓
PipelineBuilder (Abstract Builder)
    ↓
SingleStreamPipelineBuilder | CompositePipelineBuilder
```

## Video Source Directory Structure

The video source module has been refactored into a clean, modular directory structure:

```
src/framework/media/
├── overlays/               # Video overlay and OSD components (separate library)
│   ├── ll_overlay.h/cpp          # Low-level overlay processing
│   ├── overlay_internal.h/cpp    # Internal overlay implementation
│   └── Makefile                  # Independent build configuration
│
└── video_source/
    ├── core/                    # Main video source classes
    │   ├── CommonVideoSource.h/cpp      # Main controller with backward compatibility
    │   ├── PipelineManager.h/cpp        # Pipeline lifecycle management
    │   └── PipelineConfiguration.h/cpp  # Configuration parsing and validation
    │
    ├── builders/                # Pipeline construction using Builder pattern
    │   ├── PipelineBuilder.h/cpp              # Abstract base class
    │   ├── SingleStreamPipelineBuilder.h/cpp  # Single stream pipelines
    │   └── CompositePipelineBuilder.h/cpp     # Multi-stream pipelines
    │
    ├── processors/              # Video processing components
    │   ├── transforms/          # Video transformation and scaling
    │   │   └── ll_transform.h/cpp
    │   └── compositors/        # Multi-stream composition
    │       └── nvcompositor.h/cpp
    │
    ├── decoders/               # Video decoding components
    │   ├── gstnvvideodecoder.h/cpp  # GStreamer NVIDIA decoder
    │   └── decoderpool.h            # Decoder pool management
    │
    ├── encoders/               # Video encoding and image capture
    │   ├── nvvideoencoder.h/cpp     # NVIDIA hardware encoder
    │   ├── libnv_encoder.h/cpp      # Low-level encoder interface
    │   ├── image_encoder.h/cpp      # JPEG image encoding
    │   └── nvjpegenc_loader.h/cpp   # JPEG encoder loader
    │
    ├── senders/                # Video transmission and WebRTC
    │   ├── videowebRTCsender.h/cpp    # WebRTC sender (pass-through)
    │   ├── videosenderpool.h          # Video sender pool management
    │   └── webrtc_sink_consumer.h/cpp # WebRTC sink consumer (regular)
    │
    └── producers/              # Data source producers
        ├── nativestreamproducer.h/cpp   # Native camera streams
        ├── native_stream_monitor.h/cpp  # Native stream monitoring
        ├── webrtcstreamproducer.h       # WebRTC stream producer
        ├── gstnvipcproducer.h/cpp       # IPC producer (Jetson)
        └── ipcproducerpool.h            # IPC producer pool (Jetson)
```

### Build System Integration

- **Video Source Library**: `libnvvideo_source.so` - Standalone video source shared library
- **Overlay Library**: `libnvoverlays.so` - Separate overlay processing library
- **Dependency Chain**: `webrtc_streamer` → `video_source` → `overlays` → `utilities`
- **Modular Compilation**: Independent building and testing of video source and overlay components
- **Resource Management**: VideoSenderPool and DecoderPool for efficient resource sharing

### Resource Pool Management

#### VideoSenderPool (Pass-Through Mode)
- **Purpose**: Manages VideoWebRTCSender instances for pass-through streaming
- **Key Benefit**: Multiple WebRTC clients can share the same camera stream efficiently
- **Resource Sharing**: One VideoWebRTCSender per unique stream URL, shared across clients
- **Memory Optimization**: Prevents duplicate processing pipelines for the same source
- **Thread Safety**: Mutex-protected operations for concurrent access

#### DecoderPool (Regular Mode)
- **Purpose**: Manages GstNvVideoDecoder instances for standard streaming
- **Key Benefit**: Reuses decoder instances for the same stream URL
- **Resource Sharing**: Multiple consumers can share the same decoder
- **Lifecycle Management**: Automatic creation, reuse, and cleanup of decoders

### Key Benefits

1. **Modularity**: Each pipeline type has its own builder, overlays are a separate library
2. **Extensibility**: Easy to add new pipeline types and overlay functionality
3. **Maintainability**: Clear separation of pipeline construction logic and overlay processing
4. **Backward Compatibility**: Legacy interfaces preserved
5. **Performance**: Hardware acceleration and shared stream optimization
6. **Independent Development**: Overlay library can be developed and tested independently

### Pipeline Variants

#### Single Stream Pipelines
- **Standard**: Decoder → Transform → Encoder → WebRTC
- **Pass-Through**: Direct streaming without re-encoding
- **Native Stream**: Camera stream processing
- **Gods Eye View**: Recorded file playback
- **IPC**: Jetson platform with bounding box overlay
- **Image Capture**: JPEG capture pipeline

#### Composite Pipelines
- **Video Compositor**: Multi-stream video wall creation
- **Image Capture**: Composite image capture from multiple streams

## Usage

These documents are intended for:
- **Developers**: Understanding the codebase architecture
- **Architects**: Planning system modifications and extensions
- **DevOps**: Understanding system behavior and troubleshooting
- **QA**: Understanding test scenarios and edge cases

### Viewing Options

1. **XML Version**: For programmatic processing and integration with tools
2. **HTML Version**: For web viewing, presentations, and team collaboration
3. **README**: For quick overview and navigation guidance

## File Formats

- **XML**: Structured data for programmatic processing
- **HTML**: Interactive web documentation with modern UI
- **Markdown**: Human-readable documentation
- **SVG/PNG**: Visual diagrams and flowcharts

## Contributing

When updating technical documentation:
1. Ensure XML files are well-formed and validated
2. Update both XML and HTML files for consistency
3. Update README for navigation and overview
4. Include architectural decisions and rationale
5. Document any breaking changes or deprecations

## Related Documentation

- **API Documentation**: Located in `../api/`
- **VMS Documentation**: Located in `../vms/`
- **MMS Documentation**: Located in `../mms/`
- **NVStreamer Documentation**: Located in `../nvstreamer/`
