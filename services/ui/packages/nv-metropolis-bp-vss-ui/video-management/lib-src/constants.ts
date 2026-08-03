// SPDX-License-Identifier: MIT
// Number of parallel file uploads
export const NUM_PARALLEL_FILE_UPLOADS = 3;

// Maximum parallel picture requests (live + replay combined)
export const NUM_PARALLEL_GET_PICTURES = 3;

// Number of streams per page in the grid
export const NUM_STREAMS_PER_PAGE = 24;

// Chunked upload settings (bypasses Cloudflare 100s timeout for large videos).
// 10MB keeps each chunk well under the 100s budget even on slow connections:
// ~4.5 Mbps uplink → ~18s per chunk (~18% of budget). Raise cautiously —
// 50MB chunks were observed at ~90s on slower links, too close to the cutoff.
export const CHUNK_SIZE_BYTES = 10 * 1024 * 1024; // 10MB
export const MAX_CHUNK_RETRIES = 3;

// After an agent delete of rtsp/video succeeds, VST's replay listing can still
// return the sensor for a short time (longer for RTSP).
// Poll with this backoff until the streams response no longer includes them,
// or until the budget is exhausted.
export const DELETED_STREAM_POLL_DELAYS_MS = [500, 1000, 2000, 4000, 8000];
