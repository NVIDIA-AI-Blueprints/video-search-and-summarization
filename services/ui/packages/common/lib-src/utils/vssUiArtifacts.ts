// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

export const VSS_UI_ARTIFACT_OPEN = "<vss-ui-artifact>";
export const VSS_UI_ARTIFACT_CLOSE = "</vss-ui-artifact>";
export const VSS_UI_ARTIFACT_VERSION = "1.0";
export const VSS_UI_ARTIFACT_MAX_LENGTH = 1_000_000;

export interface VssUiArtifact {
  version: typeof VSS_UI_ARTIFACT_VERSION;
  kind: string;
  payload: Record<string, unknown>;
}

const KIND_PATTERN = /^vss\.[a-z0-9]+(?:[._-][a-z0-9]+)*$/;

const isVssUiArtifact = (value: unknown): value is VssUiArtifact => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const artifact = value as Partial<VssUiArtifact>;
  return (
    artifact.version === VSS_UI_ARTIFACT_VERSION &&
    typeof artifact.kind === "string" &&
    KIND_PATTERN.test(artifact.kind) &&
    !!artifact.payload &&
    typeof artifact.payload === "object" &&
    !Array.isArray(artifact.payload)
  );
};

/** Extract valid, versioned VSS artifacts while ignoring malformed agent text. */
export const extractVssUiArtifacts = (text: string): VssUiArtifact[] => {
  if (!text || typeof text !== "string") return [];
  const artifacts: VssUiArtifact[] = [];
  let cursor = 0;
  while (cursor < text.length) {
    const opening = text.indexOf(VSS_UI_ARTIFACT_OPEN, cursor);
    if (opening < 0) break;
    const payloadStart = opening + VSS_UI_ARTIFACT_OPEN.length;
    const closing = text.indexOf(VSS_UI_ARTIFACT_CLOSE, payloadStart);
    if (closing < 0) break;
    if (closing - payloadStart > VSS_UI_ARTIFACT_MAX_LENGTH) {
      cursor = closing + VSS_UI_ARTIFACT_CLOSE.length;
      continue;
    }
    try {
      const candidate: unknown = JSON.parse(
        text.slice(payloadStart, closing).trim()
      );
      if (isVssUiArtifact(candidate)) artifacts.push(candidate);
    } catch {
      // Agent prose is untrusted and may contain illustrative or partial tags.
    }
    cursor = closing + VSS_UI_ARTIFACT_CLOSE.length;
  }
  return artifacts;
};
