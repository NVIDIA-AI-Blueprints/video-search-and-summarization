// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {...init, headers: {'Content-Type': 'application/json', ...init?.headers}});
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try { const body = await response.json(); detail = typeof body.detail === 'string' ? body.detail : body.error || detail; } catch { /* response may not be JSON */ }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}
export const clock = (seconds: number, decimals = false) => {
  const safe = Math.max(0, seconds);
  const minutes = Math.floor(safe / 60).toString().padStart(2, '0');
  return `${minutes}:${(decimals ? (safe % 60).toFixed(1) : Math.floor(safe % 60).toString()).padStart(decimals ? 4 : 2, '0')}`;
};
export const percent = (value: number | null) => value === null ? '—' : value >= .975 ? '≥98%' : `${Math.round(value * 100)}%`;
export const palette = ['#b5eb47', '#74d3dd', '#c5a1ff'];
export const bottleColor = (color: string | undefined, index: number) => color && /^#[a-f0-9]{3,8}$/i.test(color) ? color : palette[index % palette.length];
