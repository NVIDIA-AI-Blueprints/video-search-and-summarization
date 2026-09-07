// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// VSS OpenClaw plugin. Two things, both declared in openclaw.plugin.json:
//   - the `vss_cli` tool below, which runs the pinned `vss` CLI baked into the
//     sandbox image so the agent drives the VSS backends through a typed tool
//     call rather than a free-form shell;
//   - the VSS skills (`skills/`, copied from the repo's skills/ tree at build),
//     which OpenClaw loads from the plugin root. The skills teach the agent
//     which vss subcommands to reach for; the tool is how it invokes them.

import { execFile } from "node:child_process";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";
import { Type } from "typebox";

// Output is capped so a chatty subcommand cannot blow the model's context.
const MAX_CAPTURE = 200_000;

const VssCliParameters = Type.Object(
  {
    args: Type.Array(Type.String(), {
      description:
        'Arguments after `vss`, one per element, for example ["summarize", "--help"] or ["ask", "--file", "clip.mp4", "what happens?"].',
    }),
    cwd: Type.Optional(
      Type.String({ description: "Working directory for the call. Defaults to the process cwd." }),
    ),
    timeoutSec: Type.Optional(
      Type.Integer({ minimum: 1, description: "Seconds before the call is killed." }),
    ),
  },
  { additionalProperties: false },
);

const VssConfig = Type.Object(
  {
    vssBin: Type.Optional(Type.String({ description: "Path to the vss CLI binary." })),
    defaultTimeoutSec: Type.Optional(
      Type.Integer({ minimum: 1, description: "Default timeout for a vss call." }),
    ),
  },
  { additionalProperties: false },
);

function clip(text: string): { text: string; truncated: boolean } {
  if (text.length <= MAX_CAPTURE) {
    return { text, truncated: false };
  }
  return { text: `${text.slice(0, MAX_CAPTURE)}\n…[truncated]`, truncated: true };
}

export default defineToolPlugin({
  id: "vss",
  name: "NVIDIA VSS",
  description:
    "Drive a live Video Search and Summarization deployment: the vss CLI as an agent tool, plus the VSS skills.",
  configSchema: VssConfig,
  tools: (tool) => [
    tool({
      name: "vss_cli",
      label: "vss CLI",
      description:
        "Run the NVIDIA VSS command-line client (`vss`) against the configured VSS deployment. " +
        "Pass the subcommand and flags as an argument array; the VSS skills describe which subcommands to use. " +
        "Returns exit code, stdout and stderr.",
      parameters: VssCliParameters,
      async execute({ args, cwd, timeoutSec }, config, context) {
        context.signal?.throwIfAborted();
        const bin = config.vssBin ?? "/usr/local/bin/vss";
        const timeoutMs = 1000 * (timeoutSec ?? config.defaultTimeoutSec ?? 600);
        const command = [bin, ...args].join(" ");

        return await new Promise((resolve) => {
          const child = execFile(
            bin,
            args,
            { cwd, timeout: timeoutMs, maxBuffer: 64 * 1024 * 1024, signal: context.signal },
            (error, stdout, stderr) => {
              const out = clip(String(stdout ?? ""));
              const err = clip(String(stderr ?? ""));
              const e = error as (NodeJS.ErrnoException & { killed?: boolean; signal?: string; code?: number | string }) | null;
              const spawnFailure = e && typeof e.code === "string" ? `${e.code}: ${e.message}` : "";
              resolve({
                command,
                exitCode: e ? (typeof e.code === "number" ? e.code : null) : (child.exitCode ?? 0),
                signal: e?.signal ?? null,
                timedOut: Boolean(e?.killed && e?.signal === "SIGTERM"),
                stdout: out.text,
                stderr: spawnFailure ? `${err.text}${err.text ? "\n" : ""}${spawnFailure}` : err.text,
                truncated: out.truncated || err.truncated,
              });
            },
          );
        });
      },
    }),
  ],
});
