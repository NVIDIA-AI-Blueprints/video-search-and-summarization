# common

Shared components and utilities for the UI monorepo.

## Installation

The package is included in the monorepo. To use it in an app or another package:

```json
{
  "dependencies": {
    "common": "*"
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
- **uploadFileChunked** – Upload a video in chunks directly to a supplied VST storage URL

## Requirements

- React 18+
- Tailwind CSS (components use Tailwind utility classes – the app must configure Tailwind)

## Build

```bash
npm run build
```
