// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * Fetch-based HTTP client with the Axios call shape used by warehouse analytics.
 */

export type HttpRequestConfig = {
  params?: Record<string, unknown> | URLSearchParams;
  headers?: Record<string, string>;
  cancelToken?: AbortSignal;
  responseType?: 'json' | 'text' | 'blob' | 'arraybuffer';
  timeout?: number;
};

export type HttpResponse<T = unknown> = {
  data: T;
  status: number;
  statusText: string;
  headers: Record<string, string>;
  config: { method: string; url: string };
};

export type HttpError = Error & {
  config?: { method?: string; url?: string };
  request?: unknown;
  response?: HttpResponse;
  __CANCEL__?: boolean;
  code?: string;
};

const isFormData = (value: unknown): value is FormData =>
  typeof FormData !== 'undefined' && value instanceof FormData;

export const isCancel = (error: unknown): boolean => {
  if (typeof error === 'string') {
    return error.startsWith('canceled') || error === 'aborted';
  }
  if (!error || typeof error !== 'object') return false;
  const err = error as HttpError & { name?: string };
  return Boolean(err.__CANCEL__ || err.name === 'AbortError' || err.code === 'ERR_CANCELED');
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

const canceledError = (message = 'canceled'): HttpError => {
  const error = new Error(message) as HttpError;
  error.__CANCEL__ = true;
  error.code = 'ERR_CANCELED';
  return error;
};

async function request<T = unknown>(
  method: string,
  url: string,
  data?: unknown,
  config: HttpRequestConfig = {},
): Promise<HttpResponse<T>> {
  const fullUrl = joinUrl(url, config.params);
  const headers: Record<string, string> = { ...(config.headers || {}) };
  const init: RequestInit = {
    method,
    headers,
    signal: config.cancelToken,
  };

  if (data !== undefined && method !== 'GET' && method !== 'HEAD') {
    if (isFormData(data)) {
      init.body = data;
    } else if (typeof data === 'string' || data instanceof Blob) {
      init.body = data as BodyInit;
    } else {
      if (!headers['Content-Type'] && !headers['content-type']) {
        headers['Content-Type'] = 'application/json';
      }
      init.body = JSON.stringify(data);
    }
  }

  const axiosConfig = { method, url: fullUrl };

  try {
    const response = await fetch(fullUrl, init);
    const headerMap: Record<string, string> = {};
    response.headers.forEach((value, key) => {
      headerMap[key] = value;
    });

    let body: unknown;
    const responseType = config.responseType || 'json';
    if (responseType === 'blob') {
      body = await response.blob();
    } else if (responseType === 'arraybuffer') {
      body = await response.arrayBuffer();
    } else if (responseType === 'text') {
      body = await response.text();
    } else {
      const text = await response.text();
      if (!text) {
        body = null;
      } else {
        try {
          body = JSON.parse(text);
        } catch {
          body = text;
        }
      }
    }

    const axiosResponse: HttpResponse<T> = {
      data: body as T,
      status: response.status,
      statusText: response.statusText,
      headers: headerMap,
      config: axiosConfig,
    };

    if (!response.ok) {
      const error = new Error(
        `Request failed with status code ${response.status}`,
      ) as HttpError;
      error.config = axiosConfig;
      error.response = axiosResponse;
      error.request = {};
      throw error;
    }

    return axiosResponse;
  } catch (caught) {
    const abortMessage =
      typeof caught === 'string'
        ? caught
        : caught instanceof Error
          ? caught.message
          : undefined;
    if (
      isCancel(caught) ||
      (caught instanceof Error && caught.name === 'AbortError') ||
      typeof caught === 'string'
    ) {
      throw canceledError(abortMessage || 'canceled');
    }
    if (caught && typeof caught === 'object') {
      const error = caught as HttpError;
      if (!error.config) error.config = axiosConfig;
      if (!error.request) error.request = {};
      throw error;
    }
    throw canceledError(String(caught));
  }
}

export const Axios = {
  get<T = unknown>(url: string, config?: HttpRequestConfig) {
    return request<T>('GET', url, undefined, config);
  },
  post<T = unknown>(url: string, data?: unknown, config?: HttpRequestConfig) {
    return request<T>('POST', url, data, config);
  },
  put<T = unknown>(url: string, data?: unknown, config?: HttpRequestConfig) {
    return request<T>('PUT', url, data, config);
  },
  delete<T = unknown>(url: string, config?: HttpRequestConfig) {
    return request<T>('DELETE', url, undefined, config);
  },
  isCancel,
  CancelToken: {
    source() {
      const controller = new AbortController();
      return {
        token: controller.signal,
        cancel(message?: string) {
          controller.abort(message);
        },
      };
    },
  },
};

export default Axios;
