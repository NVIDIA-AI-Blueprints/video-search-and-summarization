// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * Native-fetch helpers for warehouse analytics (query strings, JSON/blob
 * bodies, abort detection). Call sites use `fetch` directly.
 */

export type HttpError = Error & {
  status?: number;
  statusText?: string;
  url?: string;
  method?: string;
  data?: unknown;
  code?: string;
  config?: { method?: string; url?: string };
  request?: unknown;
  response?: { status: number; statusText: string; data: unknown };
};

const toParamEntries = (
  params: Record<string, unknown> | URLSearchParams,
): Array<[string, unknown]> => {
  if (typeof URLSearchParams !== 'undefined' && params instanceof URLSearchParams) {
    return [...params.entries()];
  }
  return Object.entries(params);
};

export const joinUrl = (
  url: string,
  params?: Record<string, unknown> | URLSearchParams,
): string => {
  const href = String(url);
  if (!params) return href;
  const origin =
    typeof window !== 'undefined' ? window.location.origin : 'http://localhost';
  const parsed = href.startsWith('http://') || href.startsWith('https://')
    ? new URL(href)
    : new URL(href, origin);
  for (const [key, value] of toParamEntries(params)) {
    if (value === undefined || value === null) continue;
    parsed.searchParams.append(key, String(value));
  }
  return parsed.toString();
};

export const isAbortError = (error: unknown): boolean => {
  if (typeof error === 'string') {
    return error.startsWith('canceled') || error === 'aborted';
  }
  if (!error || typeof error !== 'object') return false;
  const err = error as { name?: string; code?: string };
  return Boolean(
    err.name === 'AbortError' ||
      err.name === 'TimeoutError' ||
      err.code === 'ABORT_ERR' ||
      err.code === 'ERR_CANCELED' ||
      err.code === 'TIMEOUT',
  );
};

const httpError = (
  response: Response,
  data: unknown,
  method = 'GET',
): HttpError => {
  const error = new Error(
    `Request failed with status code ${response.status}`,
  ) as HttpError;
  error.status = response.status;
  error.statusText = response.statusText;
  error.url = response.url;
  error.method = method;
  error.data = data;
  error.config = { method, url: response.url };
  error.request = {};
  error.response = {
    status: response.status,
    statusText: response.statusText,
    data,
  };
  return error;
};

export const readJson = async <T = unknown>(
  response: Response,
  method = 'GET',
): Promise<T> => {
  const text = await response.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!response.ok) throw httpError(response, data, method);
  return data as T;
};

export const readBlob = async (response: Response, method = 'GET'): Promise<Blob> => {
  const blob = await response.blob();
  if (!response.ok) throw httpError(response, blob, method);
  return blob;
};
