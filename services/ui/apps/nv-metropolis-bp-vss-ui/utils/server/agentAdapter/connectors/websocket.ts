// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { JsonObject } from "../contract";
import { isJsonObject, strictJsonParse } from "../json";

export const MAX_WEBSOCKET_MESSAGE_BYTES = 26_214_400;

export class WebSocketTransportError extends Error {}
export class WebSocketTransportTimeoutError extends WebSocketTransportError {}

export interface WebSocketLike {
  readonly readyState: number;
  binaryType: BinaryType;
  addEventListener(type: string, listener: (event: Event) => void): void;
  removeEventListener(type: string, listener: (event: Event) => void): void;
  send(data: string): void;
  close(code?: number, reason?: string): void;
}

export type WebSocketFactory = (url: string) => WebSocketLike;

interface Waiter {
  resolve: (value: JsonObject) => void;
  reject: (error: Error) => void;
  cleanup: () => void;
}

const defaultFactory: WebSocketFactory = (url) => new WebSocket(url);

const errorFromReason = (reason: unknown): Error =>
  reason instanceof Error ? reason : new WebSocketTransportError("aborted");

export class JsonWebSocket {
  private readonly queue: JsonObject[] = [];
  private readonly waiters: Waiter[] = [];
  private failure?: Error;
  private closed = false;
  private messageChain = Promise.resolve();

  private constructor(private readonly socket: WebSocketLike) {
    socket.binaryType = "arraybuffer";
    socket.addEventListener("message", this.onMessage);
    socket.addEventListener("error", this.onError);
    socket.addEventListener("close", this.onClose);
  }

  static connect(
    url: string,
    timeoutMs: number,
    factory: WebSocketFactory = defaultFactory
  ): Promise<JsonWebSocket> {
    return new Promise((resolve, reject) => {
      let socket: WebSocketLike;
      try {
        socket = factory(url);
      } catch (error) {
        reject(
          new WebSocketTransportError("WebSocket connection failed", {
            cause: error,
          })
        );
        return;
      }
      const timeout = setTimeout(() => {
        cleanup();
        socket.close();
        reject(
          new WebSocketTransportTimeoutError("WebSocket upgrade timed out")
        );
      }, timeoutMs);
      timeout.unref?.();
      const cleanup = (): void => {
        clearTimeout(timeout);
        socket.removeEventListener("open", onOpen);
        socket.removeEventListener("error", onFailure);
        socket.removeEventListener("close", onFailure);
      };
      const onOpen = (): void => {
        cleanup();
        resolve(new JsonWebSocket(socket));
      };
      const onFailure = (): void => {
        cleanup();
        reject(new WebSocketTransportError("WebSocket connection failed"));
      };
      socket.addEventListener("open", onOpen);
      socket.addEventListener("error", onFailure);
      socket.addEventListener("close", onFailure);
    });
  }

  private readonly onMessage = (rawEvent: Event): void => {
    const event = rawEvent as MessageEvent<unknown>;
    this.messageChain = this.messageChain
      .then(async () => {
        let text: string;
        if (typeof event.data === "string") {
          text = event.data;
        } else if (event.data instanceof ArrayBuffer) {
          text = new TextDecoder("utf-8", { fatal: true }).decode(event.data);
        } else if (ArrayBuffer.isView(event.data)) {
          text = new TextDecoder("utf-8", { fatal: true }).decode(event.data);
        } else if (event.data instanceof Blob) {
          text = await event.data.text();
        } else {
          throw new WebSocketTransportError(
            "OpenClaw Gateway emitted an unsupported frame"
          );
        }
        if (Buffer.byteLength(text, "utf8") > MAX_WEBSOCKET_MESSAGE_BYTES) {
          throw new WebSocketTransportError(
            "OpenClaw Gateway emitted an oversized frame"
          );
        }
        const parsed = strictJsonParse(text);
        if (!isJsonObject(parsed)) {
          throw new WebSocketTransportError(
            "OpenClaw Gateway emitted a non-object frame"
          );
        }
        this.deliver(parsed);
      })
      .catch((error: unknown) => {
        this.fail(
          error instanceof Error
            ? error
            : new WebSocketTransportError(
                "OpenClaw Gateway emitted invalid JSON"
              )
        );
      });
  };

  private readonly onError = (): void => {
    this.fail(new WebSocketTransportError("WebSocket stream failed"));
  };

  private readonly onClose = (): void => {
    if (!this.closed) {
      this.fail(new WebSocketTransportError("WebSocket peer closed"));
    }
  };

  private deliver(value: JsonObject): void {
    const waiter = this.waiters.shift();
    if (waiter) {
      waiter.cleanup();
      waiter.resolve(value);
    } else {
      this.queue.push(value);
    }
  }

  private fail(error: Error): void {
    if (this.failure) return;
    this.failure = error;
    for (const waiter of this.waiters.splice(0)) {
      waiter.cleanup();
      waiter.reject(error);
    }
  }

  receive(timeoutMs: number, signal: AbortSignal): Promise<JsonObject> {
    const queued = this.queue.shift();
    if (queued) return Promise.resolve(queued);
    if (this.failure) return Promise.reject(this.failure);
    if (signal.aborted) return Promise.reject(errorFromReason(signal.reason));
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        const index = this.waiters.indexOf(waiter);
        if (index >= 0) this.waiters.splice(index, 1);
        cleanup();
        reject(
          new WebSocketTransportTimeoutError("WebSocket receive timed out")
        );
      }, timeoutMs);
      timeout.unref?.();
      const onAbort = (): void => {
        const index = this.waiters.indexOf(waiter);
        if (index >= 0) this.waiters.splice(index, 1);
        cleanup();
        reject(errorFromReason(signal.reason));
      };
      const cleanup = (): void => {
        clearTimeout(timeout);
        signal.removeEventListener("abort", onAbort);
      };
      const waiter: Waiter = { resolve, reject, cleanup };
      signal.addEventListener("abort", onAbort, { once: true });
      this.waiters.push(waiter);
    });
  }

  send(frame: JsonObject): void {
    if (this.closed || this.failure) {
      throw this.failure ?? new WebSocketTransportError("WebSocket is closed");
    }
    const encoded = JSON.stringify(frame);
    if (Buffer.byteLength(encoded, "utf8") > MAX_WEBSOCKET_MESSAGE_BYTES) {
      throw new WebSocketTransportError("WebSocket message is oversized");
    }
    try {
      this.socket.send(encoded);
    } catch (error) {
      throw new WebSocketTransportError("WebSocket send failed", {
        cause: error,
      });
    }
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.socket.removeEventListener("message", this.onMessage);
    this.socket.removeEventListener("error", this.onError);
    this.socket.removeEventListener("close", this.onClose);
    this.socket.close(1000);
    this.fail(new WebSocketTransportError("WebSocket is closed"));
  }
}
