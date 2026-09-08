#!/usr/bin/env node
// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Select which shipped skills are active, from what the vss CLI knows about the
// deployment. OpenClaw reads a plugin's skills from the static manifest path
// (skills-active/), so "active" means "copied into that directory". Everything
// shipped stays in skills/; this only decides what the agent sees.
//
// Selection, per skills.txt (<skill> <needs>):
//   needs = a vss command group  -> active when `vss configure check` lists the
//                                   group as available (the CLI joins what the
//                                   deployment exposes with what each group needs)
//   needs = alerts               -> active when Alert Bridge answers at
//                                   <base_url>/alert-bridge or <host>:9080
// No recorded deployment (vss configure never ran) -> everything active, so the
// agent can still configure. --all forces that.
//
// Used three ways: `node dist/sync.js --all` at image build; from the plugin's
// register() on every OpenClaw start; and as `vss-openclaw-sync` for an operator
// or harness that configured the deployment after the gateway came up.

import { execFileSync, spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export type SkillSpec = { name: string; needs: string };
export type Selection = { active: string[]; inactive: Record<string, string>; reason: string };
type Logger = { info: (m: string) => void; warn: (m: string) => void };

const PROBE_TIMEOUT_MS = 5_000;
const CLI_TIMEOUT_MS = 60_000;

export function readSkillSpecs(pluginDir: string): SkillSpec[] {
  const text = readFileSync(join(pluginDir, "skills.txt"), "utf8");
  const specs: SkillSpec[] = [];
  for (const raw of text.split("\n")) {
    const line = raw.replace(/#.*/, "").trim();
    if (!line) continue;
    const [name, needs = ""] = line.split(/\s+/);
    specs.push({ name, needs });
  }
  return specs;
}

type Availability = { configured: boolean; available: Set<string>; baseUrl: string; error?: string };

/** `vss configure check`: which command groups the recorded deployment can serve. */
function commandAvailability(vssBin: string): Availability {
  const r = spawnSync(vssBin, ["configure", "check"], { encoding: "utf8", timeout: CLI_TIMEOUT_MS });
  const out = `${r.stdout ?? ""}\n${r.stderr ?? ""}`;
  if (r.error) {
    return { configured: false, available: new Set(), baseUrl: "", error: `${vssBin}: ${r.error.message}` };
  }
  // The CLI's exact wording for "vss configure never ran".
  if (/no deployment configured/i.test(out)) {
    return { configured: false, available: new Set(), baseUrl: "" };
  }
  if (!/^commands:/m.test(out)) {
    const first = out.split("\n").map((l) => l.trim()).find((l) => l.length > 0) ?? `exit ${r.status}`;
    return { configured: false, available: new Set(), baseUrl: "", error: first };
  }
  const available = new Set<string>();
  let inCommands = false;
  for (const line of out.split("\n")) {
    if (/^commands:/.test(line)) { inCommands = true; continue; }
    if (!inCommands) continue;
    const m = /^\s+(\S+)\s+(available|unavailable)\b/.exec(line);
    if (m && m[2] === "available") available.add(m[1]);
  }
  const base = /against (\S+)/.exec(out);
  return { configured: true, available, baseUrl: base ? base[1] : "" };
}

function httpAnswers(url: string): boolean {
  // curl is in the runtime; treat any HTTP status but 404 as "something is routed
  // there", which is the vss CLI's own presence rule for ingress probes.
  const r = spawnSync("curl", ["-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", String(PROBE_TIMEOUT_MS / 1000), url], { encoding: "utf8" });
  const code = Number.parseInt((r.stdout ?? "").trim(), 10);
  return Number.isFinite(code) && code > 0 && code !== 404;
}

function alertsAvailable(baseUrl: string): boolean {
  if (!baseUrl) return false;
  if (httpAnswers(`${baseUrl.replace(/\/$/, "")}/alert-bridge/`)) return true;
  try {
    const u = new URL(baseUrl);
    return httpAnswers(`${u.protocol}//${u.hostname}:9080/`);
  } catch {
    return false;
  }
}

export function select(specs: SkillSpec[], opts: { all?: boolean; vssBin?: string }): Selection {
  if (opts.all) {
    return { active: specs.map((s) => s.name), inactive: {}, reason: "all shipped skills (forced)" };
  }
  const { configured, available, baseUrl, error } = commandAvailability(opts.vssBin ?? "vss");
  if (!configured) {
    // Not knowing the deployment is not a reason to hide skills: every one of
    // them starts with `vss configure check` and can recover from there.
    const why = error ? `vss configure check failed (${error})` : "no deployment recorded by `vss configure`";
    return { active: specs.map((s) => s.name), inactive: {}, reason: `${why}; all shipped skills active` };
  }
  const alerts = specs.some((s) => s.needs === "alerts") ? alertsAvailable(baseUrl) : false;
  const active: string[] = [];
  const inactive: Record<string, string> = {};
  for (const s of specs) {
    const ok = s.needs === "" || s.needs === "alerts" ? (s.needs === "" || alerts) : available.has(s.needs);
    if (ok) active.push(s.name);
    else inactive[s.name] = s.needs === "alerts" ? "alert-bridge not reachable" : `vss command group '${s.needs}' unavailable`;
  }
  return { active, inactive, reason: `deployment ${baseUrl}: commands available = ${[...available].sort().join(", ") || "none"}` };
}

/** Make skills-active/ hold exactly `active` (real copies: OpenClaw skips symlinked skill dirs). */
export function apply(pluginDir: string, active: string[]): void {
  const src = join(pluginDir, "skills");
  const dst = join(pluginDir, "skills-active");
  mkdirSync(dst, { recursive: true });
  const want = new Set(active);
  for (const present of readdirSync(dst)) {
    if (!want.has(present)) rmSync(join(dst, present), { recursive: true, force: true });
  }
  for (const name of active) {
    const from = join(src, name);
    if (!existsSync(from)) throw new Error(`skills.txt lists '${name}' but ${from} is missing`);
    rmSync(join(dst, name), { recursive: true, force: true });
    cpSync(from, join(dst, name), { recursive: true });
  }
}

export function sync(pluginDir: string, opts: { all?: boolean; vssBin?: string; logger?: Logger }): Selection {
  const specs = readSkillSpecs(pluginDir);
  const sel = select(specs, opts);
  apply(pluginDir, sel.active);
  const log = opts.logger ?? { info: console.log, warn: console.warn };
  const off = Object.entries(sel.inactive).map(([k, v]) => `${k} (${v})`).join(", ");
  log.info(`[vss] skills active: ${sel.active.join(", ") || "none"}${off ? `; inactive: ${off}` : ""} — ${sel.reason}`);
  return sel;
}

// CLI entry: node dist/sync.js [--all] [--plugin-dir <dir>] [--vss <bin>]
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const args = process.argv.slice(2);
  const flag = (k: string) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : undefined; };
  const pluginDir = flag("--plugin-dir") ?? join(dirname(fileURLToPath(import.meta.url)), "..");
  try {
    const sel = sync(pluginDir, { all: args.includes("--all"), vssBin: flag("--vss") });
    process.exit(sel.active.length > 0 ? 0 : 3);
  } catch (e) {
    console.error(`[vss] sync failed: ${e instanceof Error ? e.message : String(e)}`);
    process.exit(1);
  }
}
// keep execFileSync import referenced for environments that tree-shake types
void execFileSync;
