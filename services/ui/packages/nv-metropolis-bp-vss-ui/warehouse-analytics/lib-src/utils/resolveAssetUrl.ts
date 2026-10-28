// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Next webpack may emit a PNG import as a string URL or as
 * `{ src: string }` / `{ default: { src: string } }`. Google Maps
 * Marker.icon.url requires a string.
 */
export function resolveAssetUrl(imported: unknown): string {
  const seen = new Set<unknown>();
  let current = imported;
  while (current && !seen.has(current)) {
    if (typeof current === 'string') return current;
    seen.add(current);
    if (typeof current !== 'object') break;
    const record = current as { src?: unknown; default?: unknown };
    current = record.src ?? record.default;
  }
  return '';
}
