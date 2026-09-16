// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import {
  type ConnectorEvent,
  type JsonObject,
  stableStringify,
} from "./contract";
import { isJsonObject, strictJsonParse } from "./json";
import { createHash } from "node:crypto";

export const ARTIFACT_OPEN = "<vss-ui-artifact>";
export const ARTIFACT_CLOSE = "</vss-ui-artifact>";
export const ARTIFACT_PROTOCOL_VERSION = "1.0";
const MAX_ARTIFACT_LENGTH = 1_000_000;
const MAX_TRACKED_ARTIFACTS = 10_000;
const MAX_JSON_DOCUMENTS = 100;
const MAX_MEDIA_METADATA_LENGTH = 256;
const KIND_PATTERN = /^vss\.[a-z0-9]+(?:[._-][a-z0-9]+)*$/u;

export interface VssUiArtifact {
  artifactId: string;
  kind: string;
  payload: JsonObject;
}

const artifactEvent = (artifact: VssUiArtifact): ConnectorEvent => ({
  type: "artifact.created",
  data: {
    artifact_id: artifact.artifactId,
    version: ARTIFACT_PROTOCOL_VERSION,
    kind: artifact.kind,
    payload: artifact.payload,
  },
});

export const parseArtifact = (value: string): VssUiArtifact | null => {
  if (!value || value.length > MAX_ARTIFACT_LENGTH) return null;
  let decoded: unknown;
  try {
    decoded = strictJsonParse(value);
  } catch {
    return null;
  }
  if (
    !isJsonObject(decoded) ||
    decoded.version !== ARTIFACT_PROTOCOL_VERSION ||
    typeof decoded.kind !== "string" ||
    !KIND_PATTERN.test(decoded.kind) ||
    !isJsonObject(decoded.payload)
  ) {
    return null;
  }
  try {
    const canonical = stableStringify({
      version: ARTIFACT_PROTOCOL_VERSION,
      kind: decoded.kind,
      payload: decoded.payload,
    });
    return {
      artifactId: `artifact_${createHash("sha256")
        .update(canonical)
        .digest("hex")
        .slice(0, 24)}`,
      kind: decoded.kind,
      payload: decoded.payload,
    };
  } catch {
    return null;
  }
};

const retainedMarkerPrefix = (value: string): number => {
  const maximum = Math.min(value.length, ARTIFACT_OPEN.length - 1);
  for (let length = maximum; length > 0; length -= 1) {
    if (ARTIFACT_OPEN.startsWith(value.slice(-length))) return length;
  }
  return 0;
};

/** Find complete top-level JSON objects embedded in command output. */
const jsonDocuments = (value: string): unknown[] => {
  const documents: unknown[] = [];
  let cursor = 0;
  while (cursor < value.length && documents.length < MAX_JSON_DOCUMENTS) {
    const start = value.indexOf("{", cursor);
    if (start < 0) break;
    let depth = 0;
    let quoted = false;
    let escaped = false;
    let end = -1;
    for (let index = start; index < value.length; index += 1) {
      const character = value[index];
      if (quoted) {
        if (!escaped && character === '"') quoted = false;
        if (!escaped && character === "\\") escaped = true;
        else escaped = false;
        continue;
      }
      if (character === '"') {
        quoted = true;
      } else if (character === "{") {
        depth += 1;
      } else if (character === "}") {
        depth -= 1;
        if (depth === 0) {
          end = index + 1;
          break;
        }
      }
    }
    if (end < 0) break;
    try {
      documents.push(strictJsonParse(value.slice(start, end)));
      cursor = end;
    } catch {
      cursor = start + 1;
    }
  }
  return documents;
};

const safeMediaMetadata = (value: unknown): string | undefined => {
  if (typeof value !== "string") return undefined;
  const normalized = value.trim();
  if (
    !normalized ||
    normalized.length > MAX_MEDIA_METADATA_LENGTH ||
    /\p{Cc}/u.test(normalized)
  ) {
    return undefined;
  }
  return normalized;
};

/** Convert a VIOS URL into the VSS UI's browser-reachable media path. */
const normalizedVssMediaPath = (value: unknown): string | undefined => {
  if (typeof value !== "string") return undefined;
  let remainder = value.trim();
  if (!remainder || remainder.length > MAX_ARTIFACT_LENGTH) return undefined;

  // VIOS 3.2 can return a doubled scheme. Strip schemes until the remainder
  // is a host/path pair, then discard the container-only host.
  while (/^https?:\/\//iu.test(remainder)) {
    remainder = remainder.replace(/^https?:\/\//iu, "");
    if (!/^https?:\/\//iu.test(remainder)) {
      const slash = remainder.indexOf("/");
      remainder = slash >= 0 ? remainder.slice(slash) : "/";
      break;
    }
  }

  let parsed: URL;
  try {
    parsed = new URL(remainder, "https://vss-ui.invalid");
  } catch {
    return undefined;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return undefined;
  }

  let path = parsed.pathname;
  if (path === "/storage" || path.startsWith("/storage/")) {
    path = `/vst${path}`;
  }
  if (!path.startsWith("/vst/")) return undefined;
  return `${path}${parsed.search}`;
};

export class ArtifactStreamParser {
  private buffer = "";
  private readonly seen = new Map<string, true>();

  constructor(private readonly suppressInvalidAfterArtifact = false) {}

  private deduplicatedEvent(
    artifact: VssUiArtifact
  ): ConnectorEvent | undefined {
    if (this.seen.has(artifact.artifactId)) {
      this.seen.delete(artifact.artifactId);
      this.seen.set(artifact.artifactId, true);
      return undefined;
    }
    this.seen.set(artifact.artifactId, true);
    if (this.seen.size > MAX_TRACKED_ARTIFACTS) {
      const oldest = this.seen.keys().next().value;
      if (typeof oldest === "string") this.seen.delete(oldest);
    }
    return artifactEvent(artifact);
  }

  private inspectVssCliSearch(value: string): ConnectorEvent[] {
    const documents = jsonDocuments(value);
    const completedJobs = new Set(
      documents.flatMap((document) => {
        if (
          isJsonObject(document) &&
          document.event === "vss_job_completed" &&
          document.group === "search" &&
          document.status === "completed" &&
          document.exit_hint === 0 &&
          typeof document.job_id === "string" &&
          document.job_id.length > 0 &&
          document.job_id.length <= 256
        ) {
          return [document.job_id];
        }
        return [];
      })
    );
    const events: ConnectorEvent[] = [];
    for (const document of documents) {
      if (
        !isJsonObject(document) ||
        typeof document.job_id !== "string" ||
        !completedJobs.has(document.job_id) ||
        !Array.isArray(document.data) ||
        !Array.isArray(document.search_messages)
      ) {
        continue;
      }
      const artifact = parseArtifact(
        JSON.stringify({
          version: ARTIFACT_PROTOCOL_VERSION,
          kind: "vss.search.results",
          payload: document,
        })
      );
      if (!artifact) continue;
      const event = this.deduplicatedEvent(artifact);
      if (event) events.push(event);
    }
    return events;
  }

  private inspectVssSnapshot(candidate: JsonObject): ConnectorEvent[] {
    const urls: unknown[] = [];
    if (candidate.kind === "snapshot") urls.push(candidate.media_url);
    if (candidate.image_url !== undefined) urls.push(candidate.image_url);
    if (Array.isArray(candidate.snapshot_urls)) {
      urls.push(...candidate.snapshot_urls);
    }

    const name = safeMediaMetadata(candidate.name ?? candidate.sensor);
    const at = safeMediaMetadata(candidate.at ?? candidate.timestamp);
    const source = safeMediaMetadata(candidate.source);
    const streamId = safeMediaMetadata(candidate.stream_id);
    const sensorId = safeMediaMetadata(candidate.sensor_id);
    const events: ConnectorEvent[] = [];
    const emittedUrls = new Set<string>();

    for (const rawUrl of urls) {
      const mediaUrl = normalizedVssMediaPath(rawUrl);
      if (!mediaUrl || emittedUrls.has(mediaUrl)) continue;
      emittedUrls.add(mediaUrl);

      const payload: JsonObject = {
        media_url: mediaUrl,
        mime_type: "image/jpeg",
        alt: name
          ? `Snapshot of ${name}${at ? ` at ${at}` : ""}`
          : "VSS snapshot",
      };
      if (name) payload.sensor = name;
      if (at) payload.at = at;
      if (source) payload.source = source;
      if (streamId) payload.stream_id = streamId;
      if (sensorId) payload.sensor_id = sensorId;

      const artifact = parseArtifact(
        JSON.stringify({
          version: ARTIFACT_PROTOCOL_VERSION,
          kind: "vss.media.image",
          payload,
        })
      );
      if (!artifact) continue;
      const event = this.deduplicatedEvent(artifact);
      if (event) events.push(event);
    }
    return events;
  }

  inspectComplete(value: unknown): ConnectorEvent[] {
    const events: ConnectorEvent[] = [];
    const stack: Array<[unknown, number]> = [[value, 0]];
    let visited = 0;
    while (stack.length && visited < 1_000) {
      const [candidate, depth] = stack.pop()!;
      visited += 1;
      if (typeof candidate === "string") {
        if (candidate.length > MAX_ARTIFACT_LENGTH * 2) continue;
        events.push(...this.inspectVssCliSearch(candidate));
        if (depth < 4) {
          for (const document of jsonDocuments(candidate)) {
            stack.push([document, depth + 1]);
          }
        }
        let cursor = 0;
        while (cursor < candidate.length) {
          const opening = candidate.indexOf(ARTIFACT_OPEN, cursor);
          if (opening < 0) break;
          const payloadStart = opening + ARTIFACT_OPEN.length;
          const closing = candidate.indexOf(ARTIFACT_CLOSE, payloadStart);
          if (closing < 0) break;
          const artifact = parseArtifact(
            candidate.slice(payloadStart, closing).trim()
          );
          if (artifact) {
            const event = this.deduplicatedEvent(artifact);
            if (event) events.push(event);
          }
          cursor = closing + ARTIFACT_CLOSE.length;
        }
      } else if (depth < 4 && isJsonObject(candidate)) {
        events.push(...this.inspectVssSnapshot(candidate));
        for (const nested of Object.values(candidate)) {
          stack.push([nested, depth + 1]);
        }
      } else if (depth < 4 && Array.isArray(candidate)) {
        for (const nested of candidate) stack.push([nested, depth + 1]);
      }
    }
    return events;
  }

  feed(delta: string): ConnectorEvent[] {
    if (!delta) return [];
    this.buffer += delta;
    const events: ConnectorEvent[] = [];
    while (this.buffer) {
      const opening = this.buffer.indexOf(ARTIFACT_OPEN);
      if (opening < 0) {
        const retained = retainedMarkerPrefix(this.buffer);
        const visible = retained
          ? this.buffer.slice(0, -retained)
          : this.buffer;
        if (visible)
          events.push({ type: "message.delta", data: { delta: visible } });
        this.buffer = retained ? this.buffer.slice(-retained) : "";
        break;
      }
      if (opening) {
        events.push({
          type: "message.delta",
          data: { delta: this.buffer.slice(0, opening) },
        });
        this.buffer = this.buffer.slice(opening);
      }
      const closing = this.buffer.indexOf(ARTIFACT_CLOSE, ARTIFACT_OPEN.length);
      if (closing < 0) {
        if (this.buffer.length > MAX_ARTIFACT_LENGTH + ARTIFACT_OPEN.length) {
          events.push({
            type: "message.delta",
            data: { delta: ARTIFACT_OPEN },
          });
          this.buffer = this.buffer.slice(ARTIFACT_OPEN.length);
          continue;
        }
        break;
      }
      const end = closing + ARTIFACT_CLOSE.length;
      const rawEnvelope = this.buffer.slice(0, end);
      const rawPayload = this.buffer
        .slice(ARTIFACT_OPEN.length, closing)
        .trim();
      this.buffer = this.buffer.slice(end);
      const artifact = parseArtifact(rawPayload);
      if (!artifact) {
        if (!this.seen.size || !this.suppressInvalidAfterArtifact) {
          events.push({ type: "message.delta", data: { delta: rawEnvelope } });
        }
        continue;
      }
      const event = this.deduplicatedEvent(artifact);
      if (event) events.push(event);
    }
    return events;
  }

  finish(): ConnectorEvent[] {
    if (!this.buffer) return [];
    const buffered = this.buffer;
    this.buffer = "";
    return [{ type: "message.delta", data: { delta: buffered } }];
  }
}

export const stripArtifactEnvelopes = (value: string): string => {
  const parser = new ArtifactStreamParser();
  return [...parser.feed(value), ...parser.finish()]
    .filter((event) => event.type === "message.delta")
    .map((event) =>
      typeof event.data.delta === "string" ? event.data.delta : ""
    )
    .join("");
};

interface CleanedValue {
  value: unknown;
  changed: boolean;
}

const cleanArtifacts = (value: unknown, depth: number): CleanedValue => {
  if (typeof value === "string") {
    const stripped = stripArtifactEnvelopes(value);
    if (stripped !== value) return { value: stripped, changed: true };
    if (depth >= 4 || !value.includes(ARTIFACT_OPEN)) {
      return { value, changed: false };
    }
    let decoded: unknown;
    try {
      decoded = strictJsonParse(value);
    } catch {
      return { value, changed: false };
    }
    if (!isJsonObject(decoded) && !Array.isArray(decoded)) {
      return { value, changed: false };
    }
    const cleaned = cleanArtifacts(decoded, depth + 1);
    return cleaned.changed
      ? { value: JSON.stringify(cleaned.value), changed: true }
      : { value, changed: false };
  }
  if (depth >= 4) return { value, changed: false };
  if (Array.isArray(value)) {
    let changed = false;
    const cleaned = value.map((item) => {
      const result = cleanArtifacts(item, depth + 1);
      changed ||= result.changed;
      return result.value;
    });
    return { value: changed ? cleaned : value, changed };
  }
  if (isJsonObject(value)) {
    let changed = false;
    const cleaned: JsonObject = {};
    for (const [key, item] of Object.entries(value)) {
      const result = cleanArtifacts(item, depth + 1);
      changed ||= result.changed;
      cleaned[key] = result.value;
    }
    return { value: changed ? cleaned : value, changed };
  }
  return { value, changed: false };
};

export const stripArtifactsFromValue = (value: unknown): unknown =>
  cleanArtifacts(value, 0).value;
