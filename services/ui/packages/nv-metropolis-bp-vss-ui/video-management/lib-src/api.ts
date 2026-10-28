// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
export const createApiEndpoints = (viosApiUrl: string) => ({
  STREAMS: `${viosApiUrl}/v1/replay/streams`,
  ADD_SENSOR: `${viosApiUrl}/v1/sensor/add`,
  DELETE_SENSOR: (sensorId: string) => `${viosApiUrl}/v1/sensor/${encodeURIComponent(sensorId)}`,
  DELETE_STORAGE_FILES: (sensorId: string, startTime: string, endTime: string) =>
    `${viosApiUrl}/v1/storage/file/${encodeURIComponent(sensorId)}?startTime=${encodeURIComponent(startTime)}&endTime=${encodeURIComponent(endTime)}`,
  LIVE_PICTURE: (streamId: string) => `${viosApiUrl}/v1/live/stream/${streamId}/picture`,
  REPLAY_PICTURE: (streamId: string, startTime: string) =>
    `${viosApiUrl}/v1/replay/stream/${streamId}/picture?startTime=${encodeURIComponent(startTime)}`,
  STORAGE_SIZE: `${viosApiUrl}/v1/storage/size?timelines=true`,
  UPLOAD_FILE: `${viosApiUrl}/v1/storage/file`,
} as const);

