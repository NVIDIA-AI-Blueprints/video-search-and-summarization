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
// Two things happen at register time. Skills are selected: `skills.txt` maps each
// shipped skill to the vss command group (or the alerts path) it needs, and
// src/sync.ts asks `vss configure check` which groups the recorded deployment can
// serve, then copies exactly those skills into skills-active/, the directory the
// manifest points OpenClaw at. Unconfigured deployment: all shipped skills.
// And the OpenClaw workspace instructions
// (`workspace/` — AGENTS.md, SOUL.md, IDENTITY.md, TOOLS.md, BOOTSTRAP.md, copied
// from agent-harness/openclaw/workspace at build) are seeded into the agent's
// configured workspace when they are not there yet, with the `_<variant>`
// overlay applied on top (VSS_WORKSPACE_VARIANT, or `nemoclaw` when running in
// a NemoClaw sandbox). Existing files are never overwritten: the workspace is
// the agent's memory.

import { execFile } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { sync as syncSkills } from "./sync.js";
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

const vssPlugin = defineToolPlugin({
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


type WorkspaceApi = {
  config?: { agents?: { defaults?: { workspace?: string } } };
  logger: { info: (msg: string) => void; warn: (msg: string) => void };
};

function resolveWorkspaceVariant(): string | undefined {
  const fromEnv = process.env.VSS_WORKSPACE_VARIANT?.trim();
  if (fromEnv) {
    return fromEnv;
  }
  // NemoClaw's managed runtime always ships this entrypoint.
  return existsSync("/usr/local/bin/nemoclaw-start") ? "nemoclaw" : undefined;
}

function expandWorkspacePath(raw: string): string {
  const expanded = raw === "~" || raw.startsWith("~/") ? join(homedir(), raw.slice(1)) : raw;
  return isAbsolute(expanded) ? expanded : resolve(process.cwd(), expanded);
}

function copyMissingMarkdown(fromDir: string, toDir: string): number {
  if (!existsSync(fromDir)) {
    return 0;
  }
  let copied = 0;
  for (const file of readdirSync(fromDir).filter((f) => f.endsWith(".md"))) {
    const target = join(toDir, file);
    if (existsSync(target)) {
      continue;
    }
    copyFileSync(join(fromDir, file), target);
    copied += 1;
  }
  return copied;
}

/** Seed the agent workspace with the VSS instruction files, never overwriting. */
export function seedWorkspace(api: WorkspaceApi): void {
  const configured = api.config?.agents?.defaults?.workspace;
  if (!configured) {
    return;
  }
  const workspaceDir = expandWorkspacePath(configured);
  const templatesDir = join(dirname(fileURLToPath(import.meta.url)), "..", "workspace");
  if (!existsSync(templatesDir)) {
    api.logger.warn(`[vss] workspace templates missing at ${templatesDir}; nothing seeded`);
    return;
  }
  const variant = resolveWorkspaceVariant();
  try {
    mkdirSync(workspaceDir, { recursive: true });
    // Overlay first so a variant file wins over the base of the same name.
    let copied = 0;
    if (variant) {
      const overlay = join(templatesDir, `_${variant}`);
      if (existsSync(overlay)) {
        copied += copyMissingMarkdown(overlay, workspaceDir);
      } else {
        api.logger.warn(`[vss] workspace variant '${variant}' has no ${overlay}; base files only`);
      }
    }
    copied += copyMissingMarkdown(templatesDir, workspaceDir);
    if (copied > 0) {
      api.logger.info(
        `[vss] seeded ${copied} workspace file(s) into ${workspaceDir}${variant ? ` (variant ${variant})` : ""}`,
      );
    }
  } catch (err) {
    api.logger.warn(`[vss] workspace seeding failed: ${err instanceof Error ? err.message : String(err)}`);
  }
}

// Keep the entry defineToolPlugin produced (its non-enumerable metadata included)
// and wrap only `register`, so tool registration is untouched.
const registerTools = vssPlugin.register;
vssPlugin.register = (api) => {
  const a = api as unknown as WorkspaceApi & { pluginConfig?: { skillSelection?: string; vssBin?: string } };
  try {
    const all = (process.env.VSS_SKILL_SELECTION ?? a.pluginConfig?.skillSelection) === "all";
    syncSkills(join(dirname(fileURLToPath(import.meta.url)), ".."), { all, vssBin: a.pluginConfig?.vssBin, logger: a.logger });
  } catch (err) {
    a.logger.warn(`[vss] skill selection failed, keeping the current skills-active/: ${err instanceof Error ? err.message : String(err)}`);
  }
  seedWorkspace(a);
  return registerTools(api);
};

export default vssPlugin;
