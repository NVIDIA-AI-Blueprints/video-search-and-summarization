# @aiqtoolkit-ui/common

Shared components and utilities for the AIQ Toolkit UI system.

## Installation

The package is included in the monorepo. To use it in an app or another package:

```json
{
  "dependencies": {
    "@aiqtoolkit-ui/common": "*"
  }
}
```

## Components

- **VideoModal** – Popup modal for video playback (use with useVideoModal)
- **UploadFilesDialog** – File upload dialog with config template, JSON metadata, etc.
- **useVideoModal** – Hook for video modal state management

## Utils

- **copyToClipboard** – Copy text to clipboard (browser API with fallback)
- **formatTimestamp** – Format timestamp string for display
- **getUploadUrl** – Get the VST upload URL from the agent (`POST /api/v1/videos`)
- **uploadFileChunkedToVst** – Three-step chunked upload (agent URL handshake → VST chunked upload → agent `/complete`)
- **notifyGenericUploadComplete** – Notify the agent that a chunked upload finished (`POST /api/v1/videos/{video_id}/complete`)

## Requirements

- React 18+
- Tailwind CSS (components use Tailwind utility classes – the app must configure Tailwind)

## Build

```bash
npm run build
```
