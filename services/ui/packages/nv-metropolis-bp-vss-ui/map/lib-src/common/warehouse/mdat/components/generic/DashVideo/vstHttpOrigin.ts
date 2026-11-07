// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * VST DASH start URLs are origin-relative (`/vst/api/v1/live/dash/start`).
 * Warehouse media config is usually `http://host:port/vst/api/v1`.
 */
export function vstHttpOrigin(mediaServiceBaseUrl: string): string {
  const href = String(mediaServiceBaseUrl || '').trim();
  if (!href) return '';
  try {
    const fallbackOrigin =
      typeof window !== 'undefined' ? window.location.origin : 'http://localhost';
    const parsed =
      href.startsWith('http://') || href.startsWith('https://')
        ? new URL(href)
        : new URL(href, fallbackOrigin);
    return parsed.origin;
  } catch {
    return href.replace(/\/vst\/api\/v1\/?.*$/i, '').replace(/\/+$/, '');
  }
}
