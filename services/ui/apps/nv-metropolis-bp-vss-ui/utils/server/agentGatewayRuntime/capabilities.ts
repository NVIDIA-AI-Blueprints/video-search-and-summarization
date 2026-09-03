// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { isJsonObject, strictJsonParse } from "./json";
import { createHash, timingSafeEqual } from "node:crypto";

const MAX_RECEIPT_BYTES = 256_000;
const RECEIPT_SCHEMA_VERSION = 1;
export const ARTIFACT_PROTOCOL_VERSION = "1.0";
const ARTIFACT_ENVELOPE = "vss-ui-artifact";
const REQUIRED_ARTIFACT_KINDS = new Set([
  "vss.search.results",
  "vss.alert.incidents",
]);
const REQUIRED_VSS_SKILLS = new Set([
  "benchmark-video-summarization",
  "vss-ask-video",
  "vss-build-vision-ai",
  "vss-deploy-dense-captioning",
  "vss-deploy-detection-tracking-2d",
  "vss-deploy-detection-tracking-3d",
  "vss-deploy-profile",
  "vss-deploy-video-embedding",
  "vss-deploy-warehouse-helm",
  "vss-generate-video-calibration",
  "vss-generate-video-report",
  "vss-generate-video-report-rag",
  "vss-manage-alerts",
  "vss-manage-video-io-storage",
  "vss-query-analytics",
  "vss-search-archive",
  "vss-setup-behavior-analytics",
  "vss-setup-video-analytics-api",
  "vss-summarize-video",
]);
const COMMIT_PATTERN = /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/u;
const SAFE_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/u;

export class CapabilityError extends Error {}

export interface CapabilityReceipt {
  schemaVersion: number;
  harness: string;
  identityMode: "dedicated" | "preserve";
  vssOrigin: string;
  runtimeRoot: string;
  runtimeCommit: string;
  skills: string[];
  artifactVersion: string;
  artifactKinds: string[];
}

const requiredObject = (
  payload: Record<string, unknown>,
  key: string
): Record<string, unknown> => {
  const value = payload[key];
  if (!isJsonObject(value)) {
    throw new CapabilityError(`capability receipt ${key} must be an object`);
  }
  return value;
};

const requiredString = (
  payload: Record<string, unknown>,
  key: string
): string => {
  const value = payload[key];
  if (typeof value !== "string" || !value.trim()) {
    throw new CapabilityError(
      `capability receipt ${key} must be a non-empty string`
    );
  }
  return value.trim();
};

const validateOrigin = (value: string): string => {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new CapabilityError(
      "capability receipt vss_origin must be a bare absolute http(s) origin"
    );
  }
  if (
    !["http:", "https:"].includes(parsed.protocol) ||
    !parsed.hostname ||
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash ||
    (parsed.pathname !== "" && parsed.pathname !== "/")
  ) {
    throw new CapabilityError(
      "capability receipt vss_origin must be a bare absolute http(s) origin"
    );
  }
  return value.replace(/\/$/u, "");
};

const uniqueStringList = (
  value: unknown,
  message: string,
  validate: (item: string) => boolean = () => true
): string[] => {
  if (
    !Array.isArray(value) ||
    value.some((item) => typeof item !== "string" || !validate(item)) ||
    new Set(value).size !== value.length
  ) {
    throw new CapabilityError(message);
  }
  return value as string[];
};

const parseReceipt = (payload: unknown): CapabilityReceipt => {
  if (!isJsonObject(payload)) {
    throw new CapabilityError("capability receipt must be a JSON object");
  }
  if (payload.schema_version !== RECEIPT_SCHEMA_VERSION) {
    throw new CapabilityError(
      `capability receipt schema_version must be ${RECEIPT_SCHEMA_VERSION}`
    );
  }
  const harness = requiredString(payload, "harness");
  if (!SAFE_NAME_PATTERN.test(harness)) {
    throw new CapabilityError("capability receipt harness is invalid");
  }
  const identityMode = requiredString(payload, "identity_mode");
  if (identityMode !== "dedicated" && identityMode !== "preserve") {
    throw new CapabilityError(
      "capability receipt identity_mode must be dedicated or preserve"
    );
  }
  if (typeof payload.vss_origin !== "string") {
    throw new CapabilityError("capability receipt vss_origin must be a string");
  }
  const rawOrigin = payload.vss_origin.trim();
  if (!rawOrigin && identityMode !== "dedicated") {
    throw new CapabilityError(
      "a preserved-agent capability receipt requires vss_origin"
    );
  }
  const vssOrigin = rawOrigin ? validateOrigin(rawOrigin) : "";

  const runtime = requiredObject(payload, "runtime");
  const runtimeRoot = requiredString(runtime, "repo_root");
  if (
    !runtimeRoot.startsWith("/sandbox/") ||
    runtimeRoot.split("/").includes("..")
  ) {
    throw new CapabilityError(
      "capability receipt runtime.repo_root must be below /sandbox"
    );
  }
  const runtimeCommit = requiredString(runtime, "commit").toLowerCase();
  if (!COMMIT_PATTERN.test(runtimeCommit)) {
    throw new CapabilityError(
      "capability receipt runtime.commit must be a full Git commit ID"
    );
  }

  const skills = uniqueStringList(
    payload.skills,
    "capability receipt skills must be a non-empty unique name list",
    (skill) => SAFE_NAME_PATTERN.test(skill)
  );
  if (skills.length === 0) {
    throw new CapabilityError(
      "capability receipt skills must be a non-empty unique name list"
    );
  }
  const missingSkills = [...REQUIRED_VSS_SKILLS]
    .filter((skill) => !skills.includes(skill))
    .sort();
  if (missingSkills.length) {
    throw new CapabilityError(
      `capability receipt is missing required VSS skills: ${missingSkills.join(
        ", "
      )}`
    );
  }

  const artifacts = requiredObject(payload, "ui_artifacts");
  const artifactVersion = requiredString(artifacts, "version");
  if (artifactVersion !== ARTIFACT_PROTOCOL_VERSION) {
    throw new CapabilityError(
      "capability receipt has an unsupported UI artifact version"
    );
  }
  if (requiredString(artifacts, "envelope") !== ARTIFACT_ENVELOPE) {
    throw new CapabilityError(
      "capability receipt has an unsupported UI artifact envelope"
    );
  }
  const artifactKinds = uniqueStringList(
    artifacts.kinds,
    "capability receipt ui_artifacts.kinds must be a unique string list"
  );
  const missingKinds = [...REQUIRED_ARTIFACT_KINDS]
    .filter((kind) => !artifactKinds.includes(kind))
    .sort();
  if (missingKinds.length) {
    throw new CapabilityError(
      `capability receipt is missing required UI artifacts: ${missingKinds.join(
        ", "
      )}`
    );
  }
  return {
    schemaVersion: RECEIPT_SCHEMA_VERSION,
    harness,
    identityMode,
    vssOrigin,
    runtimeRoot,
    runtimeCommit,
    skills,
    artifactVersion,
    artifactKinds,
  };
};

export const decodeCapabilityReceipt = (
  encoded: string,
  expectedSha256: string,
  expectedRuntimeCommit?: string
): CapabilityReceipt => {
  const digest = expectedSha256.trim().toLowerCase();
  if (!/^[0-9a-f]{64}$/u.test(digest)) {
    throw new CapabilityError(
      "AGENT_VSS_CAPABILITIES_SHA256 must be a lowercase SHA-256 digest"
    );
  }
  if (
    !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/u.test(
      encoded
    )
  ) {
    throw new CapabilityError(
      "AGENT_VSS_CAPABILITIES_B64 must be strict base64"
    );
  }
  const raw = Buffer.from(encoded, "base64");
  if (!raw.length || raw.length > MAX_RECEIPT_BYTES) {
    throw new CapabilityError(
      `decoded VSS capability receipt must be 1..${MAX_RECEIPT_BYTES} bytes`
    );
  }
  const actualDigest = createHash("sha256").update(raw).digest();
  const expectedDigest = Buffer.from(digest, "hex");
  if (!timingSafeEqual(actualDigest, expectedDigest)) {
    throw new CapabilityError("VSS capability receipt digest does not match");
  }
  let payload: unknown;
  try {
    payload = strictJsonParse(
      new TextDecoder("utf-8", { fatal: true }).decode(raw)
    );
  } catch {
    throw new CapabilityError("VSS capability receipt must be strict JSON");
  }
  const receipt = parseReceipt(payload);
  if (expectedRuntimeCommit !== undefined) {
    const expected = expectedRuntimeCommit.trim().toLowerCase();
    if (!COMMIT_PATTERN.test(expected)) {
      throw new CapabilityError(
        "AGENT_EXPECTED_VSS_RUNTIME_REF must be a full Git commit ID"
      );
    }
    const actual = Buffer.from(receipt.runtimeCommit);
    const target = Buffer.from(expected);
    if (actual.length !== target.length || !timingSafeEqual(actual, target)) {
      throw new CapabilityError(
        "VSS capability receipt runtime commit does not match the deployment"
      );
    }
  }
  return receipt;
};

export const capabilitySummary = (
  receipt: CapabilityReceipt
): Record<string, unknown> => ({
  attached: true,
  ready: true,
  schema_version: receipt.schemaVersion,
  harness: receipt.harness,
  identity_mode: receipt.identityMode,
  runtime_commit: receipt.runtimeCommit,
  skill_count: receipt.skills.length,
  artifact_version: receipt.artifactVersion,
  artifact_kinds: receipt.artifactKinds,
});
