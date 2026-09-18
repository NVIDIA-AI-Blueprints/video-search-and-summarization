// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { randomUUID } from "node:crypto";

type Outcome = { exitCode: number | null; signal: string | null; failed: boolean };

/** Observe the existing vss_cli tool only. Never wait for telemetry in its execution path. */
export function beginToolEvent(): ((outcome: Outcome) => void) | undefined {
  try {
    const raw = process.env.VSS_RELAY_URL;
    const run = process.env.VSS_RELAY_RUN ?? "";
    const trial = process.env.VSS_RELAY_TRIAL ?? "";
    const validId = (value: string) => value.length > 0 && value.length <= 255
      && /^[A-Za-z0-9_-]/.test(value) && !/[^A-Za-z0-9_.-]/.test(value);
    if (!raw || !validId(run) || !validId(trial)) return;
    const url = new URL(raw);
    if (!/^https?:\/\//i.test(raw) || /[\u0000-\u0020\u007f\\]/.test(raw)
      || /^https?:\/\/[^/?#]*@/i.test(raw) || url.username || url.password
      || url.search || url.hash) return;

    const uuid = randomUUID();
    const started = performance.now();
    const event = (phase: "start" | "end", outcome?: Outcome) => ({
      kind: "scope", category: "tool", scope_category: phase, uuid,
      // Name the tool we actually observe; never parse or export its argument array.
      name: "vss_cli", timestamp: new Date().toISOString(),
      data_schema: "nvidia.vss.openclaw.tool-lifecycle/v1",
      metadata: {
        session_id: `vss-eval/${run}/${trial}`, source: "vss-openclaw",
        ...(outcome ? { duration_ms: Math.max(0, performance.now() - started) } : {}),
      },
      data: outcome ? {
        exit_code: outcome.exitCode, signal: outcome.signal,
        ...(outcome.failed ? { error: "Tool execution failed" } : {}),
      } : {},
    });
    const send = async (record: ReturnType<typeof event>) => {
      try {
        const response = await fetch(url, {
          method: "POST", redirect: "error", signal: AbortSignal.timeout(200),
          headers: { "Content-Type": "application/json", "X-Relay-Run": run, "X-Relay-Trial": trial },
          body: JSON.stringify(record),
        });
        await response.body?.cancel(); // No response bodies or transport errors enter tool output.
      } catch { /* Best effort, no retries or diagnostic output. */ }
    };
    const start = send(event("start"));
    return (outcome) => {
      try {
        // Snapshot completion before transport waits, preserving execution timing.
        const end = event("end", outcome);
        // ponytail: two bounded sends per call, no durable delivery; process exit can lose events.
        void start.then(() => send(end));
      } catch { /* Telemetry must not change the tool's result. */ }
    };
  } catch {
    return undefined; // Invalid or unavailable optional context never breaks the tool.
  }
}
