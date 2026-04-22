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
- **getUploadUrl** – Get presigned upload URL from Agent API
- **uploadFile** – Upload file (two-step: get URL, then PUT)

## Requirements

- React 18+
- Tailwind CSS (components use Tailwind utility classes – the app must configure Tailwind)

## Build

```bash
npm run build
```
