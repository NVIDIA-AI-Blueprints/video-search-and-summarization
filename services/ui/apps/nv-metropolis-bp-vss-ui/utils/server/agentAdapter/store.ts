// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import {
  type CreateRunRequest,
  type JsonObject,
  type RunEvent,
  TERMINAL_EVENT_TYPES,
  createRunEvent,
  requestDigest,
} from "./contract";
import { randomBytes } from "node:crypto";

export class RunNotFoundError extends Error {}
export class EventsExpiredError extends Error {}
export class IdempotencyConflictError extends Error {}
export class StoreCapacityError extends Error {}
export class ThreadBusyError extends Error {
  constructor(readonly runId: string) {
    super(`thread already has active run ${runId}`);
  }
}

export const validateIdempotencyKey = (
  value: string | undefined
): string | undefined => {
  if (!value) return undefined;
  if (
    value.length > 255 ||
    [...value].some((character) => {
      const code = character.codePointAt(0) ?? 0;
      return code < 33 || code > 126;
    })
  ) {
    throw new Error(
      "Idempotency-Key must contain 1-255 visible ASCII characters"
    );
  }
  return value;
};

export class RunRecord {
  readonly abortController = new AbortController();
  status = "queued";
  private readonly events: RunEvent[] = [];
  private readonly eventCharSizes: number[] = [];
  private retainedEventChars = 0;
  private nextSequence = 1;
  private updatedAt = Date.now();
  private readonly listeners = new Set<() => void>();

  constructor(
    readonly runId: string,
    readonly request: CreateRunRequest,
    readonly requestHash: string,
    private readonly requestRetainedChars: number,
    private readonly maxEvents: number,
    private readonly maxEventChars: number,
    private readonly onRetainedCharsChanged: (
      record: RunRecord,
      delta: number
    ) => void
  ) {}

  get terminal(): boolean {
    return ["completed", "failed", "cancelled"].includes(this.status);
  }

  get lastUpdatedAt(): number {
    return this.updatedAt;
  }

  get lastEventSequence(): number {
    return this.nextSequence - 1;
  }

  get retainedChars(): number {
    // Reserve the complete event allowance while a run is active. Appends can
    // then stream safely without making process-wide retention unbounded.
    return (
      this.requestRetainedChars +
      (this.terminal ? this.retainedEventChars : this.maxEventChars)
    );
  }

  append(type: string, data: JsonObject = {}): RunEvent {
    if (this.terminal) throw new Error("cannot append to a terminal run");
    const previousRetainedChars = this.retainedChars;
    const encoded = JSON.stringify(data);
    if (encoded.length > this.maxEventChars) {
      throw new Error("one adapter event exceeds the per-run character limit");
    }
    const event = createRunEvent(
      this.nextSequence,
      type,
      this.runId,
      this.request.threadId,
      data
    );
    const eventChars = JSON.stringify(event).length;
    if (eventChars > this.maxEventChars) {
      throw new Error("one adapter event exceeds the per-run character limit");
    }
    this.nextSequence += 1;
    this.events.push(event);
    this.eventCharSizes.push(eventChars);
    this.retainedEventChars += eventChars;
    while (
      this.events.length > this.maxEvents ||
      this.retainedEventChars > this.maxEventChars
    ) {
      this.events.shift();
      this.retainedEventChars -= this.eventCharSizes.shift() ?? 0;
    }
    if (type === "run.started") this.status = "running";
    else if (type === "run.completed") this.status = "completed";
    else if (type === "run.failed") this.status = "failed";
    else if (type === "run.cancelled") this.status = "cancelled";
    this.updatedAt = Date.now();
    this.onRetainedCharsChanged(
      this,
      this.retainedChars - previousRetainedChars
    );
    for (const listener of this.listeners) listener();
    return event;
  }

  eventsAfter(sequence: number): RunEvent[] {
    if (this.events.length && sequence < this.events[0].sequence - 1) {
      throw new EventsExpiredError("requested events are no longer retained");
    }
    return this.events.filter((event) => event.sequence > sequence);
  }

  waitForChange(signal: AbortSignal, timeoutMs = 15_000): Promise<void> {
    if (this.terminal || signal.aborted) return Promise.resolve();
    return new Promise((resolve) => {
      const done = (): void => {
        cleanup();
        resolve();
      };
      const timeout = setTimeout(done, timeoutMs);
      timeout.unref?.();
      const cleanup = (): void => {
        clearTimeout(timeout);
        this.listeners.delete(done);
        signal.removeEventListener("abort", done);
      };
      this.listeners.add(done);
      signal.addEventListener("abort", done, { once: true });
    });
  }

  snapshot(): JsonObject {
    return {
      run_id: this.runId,
      thread_id: this.request.threadId,
      status: this.status,
      last_event_id: String(this.lastEventSequence),
    };
  }
}

interface IdempotencyRecord {
  digest: string;
  runId: string;
}

export class RunStore {
  private readonly runs = new Map<string, RunRecord>();
  private readonly activeThreads = new Map<string, string>();
  private readonly idempotency = new Map<string, IdempotencyRecord>();
  private retainedChars = 0;

  constructor(
    private readonly retentionMs: number,
    private readonly maxRuns: number,
    private readonly maxEventsPerRun: number,
    private readonly maxEventCharsPerRun: number,
    private readonly maxRetainedChars: number
  ) {}

  private remove(runId: string): void {
    const run = this.runs.get(runId);
    if (!run) return;
    this.runs.delete(runId);
    this.retainedChars -= run.retainedChars;
    if (this.activeThreads.get(run.request.threadId) === runId) {
      this.activeThreads.delete(run.request.threadId);
    }
    for (const [key, record] of this.idempotency) {
      if (record.runId === runId) this.idempotency.delete(key);
    }
  }

  private oldestTerminal(): RunRecord | undefined {
    return [...this.runs.values()]
      .filter((record) => record.terminal)
      .sort((left, right) => left.lastUpdatedAt - right.lastUpdatedAt)[0];
  }

  private updateRetainedChars(record: RunRecord, delta: number): void {
    if (this.runs.get(record.runId) !== record) return;
    this.retainedChars += delta;
    if (this.retainedChars > this.maxRetainedChars) {
      throw new StoreCapacityError(
        "adapter exceeded its retained character limit"
      );
    }
  }

  private cleanup(): void {
    const now = Date.now();
    for (const [runId, record] of this.runs) {
      if (record.terminal && now - record.lastUpdatedAt > this.retentionMs) {
        this.remove(runId);
      }
    }
  }

  create(
    request: CreateRunRequest,
    idempotencyKey?: string
  ): { record: RunRecord; replayed: boolean } {
    const key = validateIdempotencyKey(idempotencyKey);
    const digest = requestDigest(request);
    this.cleanup();
    if (key) {
      const existing = this.idempotency.get(key);
      if (existing) {
        if (existing.digest !== digest) {
          throw new IdempotencyConflictError(
            "Idempotency-Key was already used with a different request"
          );
        }
        const record = this.runs.get(existing.runId);
        if (record) return { record, replayed: true };
      }
    }
    const activeRunId = this.activeThreads.get(request.threadId);
    if (activeRunId) throw new ThreadBusyError(activeRunId);
    const runId = `run_${randomBytes(18).toString("base64url")}`;
    const requestRetainedChars = JSON.stringify({
      idempotency_key: key ?? null,
      run_id: runId,
      request,
      request_hash: digest,
    }).length;
    const record = new RunRecord(
      runId,
      request,
      digest,
      requestRetainedChars,
      this.maxEventsPerRun,
      this.maxEventCharsPerRun,
      (changedRecord, delta) => this.updateRetainedChars(changedRecord, delta)
    );
    while (
      this.runs.size >= this.maxRuns ||
      this.retainedChars + record.retainedChars > this.maxRetainedChars
    ) {
      const terminal = this.oldestTerminal();
      if (!terminal) {
        throw new StoreCapacityError(
          this.runs.size >= this.maxRuns
            ? "adapter has reached its active run limit"
            : "adapter has reached its retained character limit"
        );
      }
      this.remove(terminal.runId);
    }
    this.runs.set(runId, record);
    this.retainedChars += record.retainedChars;
    this.activeThreads.set(request.threadId, runId);
    if (key) this.idempotency.set(key, { digest, runId });
    return { record, replayed: false };
  }

  get(runId: string): RunRecord {
    this.cleanup();
    const record = this.runs.get(runId);
    if (!record) throw new RunNotFoundError(runId);
    return record;
  }

  finish(record: RunRecord, type: string, data: JsonObject = {}): RunEvent {
    if (!TERMINAL_EVENT_TYPES.has(type)) {
      throw new Error(`${type} is not terminal`);
    }
    if (record.terminal) {
      const terminal = record
        .eventsAfter(0)
        .findLast((event) => TERMINAL_EVENT_TYPES.has(event.type));
      if (terminal) return terminal;
    }
    const event = record.append(type, data);
    if (this.activeThreads.get(record.request.threadId) === record.runId) {
      this.activeThreads.delete(record.request.threadId);
    }
    return event;
  }
}
