// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { createHash } from "node:crypto";

export const PROTOCOL_VERSION = "1.0";
export const TERMINAL_EVENT_TYPES = new Set([
  "run.completed",
  "run.failed",
  "run.cancelled",
]);

const MAX_IDENTIFIER_LENGTH = 256;
const MAX_MESSAGE_CONTENT_LENGTH = 1_000_000;
const MAX_TRANSCRIPT_LENGTH = 5_000_000;
const ALLOWED_ROLES = new Set(["system", "developer", "user", "assistant"]);

export type JsonObject = Record<string, unknown>;

export interface Message {
  role: "system" | "developer" | "user" | "assistant";
  content: string;
}

export interface CreateRunRequest {
  threadId: string;
  input: Message[];
  history: Message[];
  surface: string;
  instructions?: string;
  metadata: JsonObject;
}

export interface ConnectorEvent {
  type: string;
  data: JsonObject;
}

export interface RunEvent {
  sequence: number;
  type: string;
  runId: string;
  threadId: string;
  data: JsonObject;
  createdAt: string;
}

export class ContractError extends Error {}

const isJsonObject = (value: unknown): value is JsonObject =>
  !!value && typeof value === "object" && !Array.isArray(value);

const requiredIdentifier = (value: unknown, name: string): string => {
  if (typeof value !== "string" || !value.trim()) {
    throw new ContractError(`${name} must be a non-empty string`);
  }
  const normalized = value.trim();
  if (normalized.length > MAX_IDENTIFIER_LENGTH) {
    throw new ContractError(
      `${name} must be at most ${MAX_IDENTIFIER_LENGTH} characters`
    );
  }
  if (/\p{Cc}/u.test(normalized)) {
    throw new ContractError(`${name} must not contain control characters`);
  }
  return normalized;
};

const parseMessage = (value: unknown, name: string): Message => {
  if (!isJsonObject(value)) {
    throw new ContractError(`${name} must be an object`);
  }
  const { role, content } = value;
  if (typeof role !== "string" || !ALLOWED_ROLES.has(role)) {
    throw new ContractError(
      `${name}.role must be one of: ${[...ALLOWED_ROLES].sort().join(", ")}`
    );
  }
  if (typeof content !== "string") {
    throw new ContractError(`${name}.content must be a string`);
  }
  if (content.length > MAX_MESSAGE_CONTENT_LENGTH) {
    throw new ContractError(
      `${name}.content must be at most ${MAX_MESSAGE_CONTENT_LENGTH} characters`
    );
  }
  return { role: role as Message["role"], content };
};

export const parseCreateRunRequest = (value: unknown): CreateRunRequest => {
  if (!isJsonObject(value)) {
    throw new ContractError("request body must be an object");
  }
  const threadId = requiredIdentifier(value.thread_id, "thread_id");
  if (!Array.isArray(value.input) || value.input.length === 0) {
    throw new ContractError("input must be a non-empty array");
  }
  const input = value.input.map((message, index) =>
    parseMessage(message, `input[${index}]`)
  );
  const rawHistory = value.history ?? [];
  if (!Array.isArray(rawHistory)) {
    throw new ContractError("history must be an array");
  }
  const history = rawHistory.map((message, index) =>
    parseMessage(message, `history[${index}]`)
  );
  const surface = requiredIdentifier(value.surface ?? "vss-ui", "surface");
  if (
    value.instructions !== undefined &&
    value.instructions !== null &&
    typeof value.instructions !== "string"
  ) {
    throw new ContractError("instructions must be a string");
  }
  const metadata = value.metadata ?? {};
  if (!isJsonObject(metadata)) {
    throw new ContractError("metadata must be an object");
  }
  const transcriptLength = [...input, ...history].reduce(
    (total, message) => total + message.content.length,
    0
  );
  if (transcriptLength > MAX_TRANSCRIPT_LENGTH) {
    throw new ContractError(
      `input and history must total at most ${MAX_TRANSCRIPT_LENGTH} characters`
    );
  }
  return {
    threadId,
    input,
    history,
    surface,
    instructions:
      typeof value.instructions === "string" ? value.instructions : undefined,
    metadata,
  };
};

const messagesEqual = (left: Message[], right: Message[]): boolean =>
  left.length === right.length &&
  left.every(
    (message, index) =>
      message.role === right[index].role &&
      message.content === right[index].content
  );

export const historyPrefix = (request: CreateRunRequest): Message[] => {
  if (request.input.length > request.history.length) return request.history;
  const tail = request.history.slice(-request.input.length);
  return messagesEqual(tail, request.input)
    ? request.history.slice(0, -request.input.length)
    : request.history;
};

export const fullTranscript = (request: CreateRunRequest): Message[] => [
  ...historyPrefix(request),
  ...request.input,
];

export const toChatMessage = (message: Message): Message => ({ ...message });

export const toResponsesItem = (message: Message): JsonObject => ({
  type: "message",
  role: message.role,
  content: [{ type: "input_text", text: message.content }],
});

const sortJson = (value: unknown): unknown => {
  if (Array.isArray(value)) return value.map(sortJson);
  if (!isJsonObject(value)) return value;
  return Object.fromEntries(
    Object.keys(value)
      .sort()
      .map((key) => [key, sortJson(value[key])])
  );
};

export const stableStringify = (value: unknown): string =>
  JSON.stringify(sortJson(value));

export const transcriptDigest = (messages: Message[]): string =>
  createHash("sha256")
    .update(stableStringify(messages.map(toChatMessage)))
    .digest("hex");

export const requestDigest = (request: CreateRunRequest): string =>
  createHash("sha256")
    .update(
      stableStringify({
        thread_id: request.threadId,
        input: request.input.map(toChatMessage),
        history: request.history.map(toChatMessage),
        surface: request.surface,
        instructions: request.instructions ?? null,
        metadata: request.metadata,
      })
    )
    .digest("hex");

export const createRunEvent = (
  sequence: number,
  type: string,
  runId: string,
  threadId: string,
  data: JsonObject = {}
): RunEvent => ({
  sequence,
  type,
  runId,
  threadId,
  data,
  createdAt: new Date().toISOString(),
});

export const runEventPayload = (event: RunEvent): JsonObject => ({
  protocol_version: PROTOCOL_VERSION,
  id: String(event.sequence),
  type: event.type,
  run_id: event.runId,
  thread_id: event.threadId,
  created_at: event.createdAt,
  data: event.data,
});

export const runEventSse = (event: RunEvent): string =>
  `id: ${event.sequence}\nevent: ${event.type}\ndata: ${JSON.stringify(
    runEventPayload(event)
  )}\n\n`;
