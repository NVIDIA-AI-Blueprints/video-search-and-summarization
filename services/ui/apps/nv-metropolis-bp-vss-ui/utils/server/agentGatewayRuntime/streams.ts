// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const MAX_STREAM_BUFFER = 5_000_000;

export interface SseFrame {
  event?: string;
  data: string;
  id?: string;
}

const parseSseFrame = (frame: string): SseFrame | null => {
  const data: string[] = [];
  let event: string | undefined;
  let id: string | undefined;
  for (const line of frame.split("\n")) {
    if (!line || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator < 0 ? line : line.slice(0, separator);
    const raw = separator < 0 ? "" : line.slice(separator + 1);
    const value = raw.startsWith(" ") ? raw.slice(1) : raw;
    if (field === "data") data.push(value);
    else if (field === "event") event = value;
    else if (field === "id" && !value.includes("\0")) id = value;
  }
  return data.length ? { event, data: data.join("\n"), id } : null;
};

export async function* readSse(response: Response): AsyncGenerator<SseFrame> {
  if (!response.body) throw new Error("backend response has no body");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer = (buffer + decoder.decode(value, { stream: !done })).replaceAll(
        "\r\n",
        "\n"
      );
      if (buffer.length > MAX_STREAM_BUFFER) {
        throw new Error("backend emitted an oversized stream frame");
      }
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";
      for (const rawFrame of frames) {
        const frame = parseSseFrame(rawFrame);
        if (frame) yield frame;
      }
      if (done) break;
    }
    if (buffer) {
      const frame = parseSseFrame(buffer);
      if (frame) yield frame;
    }
  } finally {
    reader.releaseLock();
  }
}

export async function* readLines(response: Response): AsyncGenerator<string> {
  if (!response.body) throw new Error("backend response has no body");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      if (buffer.length > MAX_STREAM_BUFFER) {
        throw new Error("backend emitted an oversized stream line");
      }
      const lines = buffer.split(/\r?\n/u);
      buffer = lines.pop() ?? "";
      for (const line of lines) yield line;
      if (done) break;
    }
    if (buffer) yield buffer;
  } finally {
    reader.releaseLock();
  }
}

export const boundedResponseText = async (
  response: Response,
  maximum: number
): Promise<string> => {
  if (!response.body) return "";
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let result = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      result += decoder.decode(value, { stream: !done });
      if (result.length > maximum) {
        throw new Error("backend response is oversized");
      }
      if (done) return result;
    }
  } finally {
    reader.releaseLock();
  }
};

export const linkedTimeoutSignal = (
  source: AbortSignal,
  timeoutMs: number
): { signal: AbortSignal; cleanup: () => void } => {
  const controller = new AbortController();
  const abort = (): void => controller.abort(source.reason);
  source.addEventListener("abort", abort, { once: true });
  if (source.aborted) abort();
  const timeout = setTimeout(
    () => controller.abort(new Error("backend request timed out")),
    timeoutMs
  );
  timeout.unref?.();
  return {
    signal: controller.signal,
    cleanup: () => {
      clearTimeout(timeout);
      source.removeEventListener("abort", abort);
    },
  };
};
