// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// VSS DeepSeek Harness (dsh) plugin. One tool, vss_cli, mirroring the
// OpenClaw plugin (../../.openclaw/plugin/src/index.ts) and the Hermes
// plugin (../../.hermes/plugin/__init__.py): same name, same
// {args, cwd, timeoutSec} parameter schema, the pinned `vss` CLI run as a
// no-shell subprocess (execFile, never a shell string), the same
// 200,000-char per-stream output cap, and the same
// {command, exitCode, signal, timedOut, stdout, stderr, truncated} result
// shape. A skill that says "call the vss_cli tool" works unchanged on any
// of the three harnesses.
//
// Registered via Cordis's own apply(ctx) convention (see dsh's
// packages/shell/tool-bash for the reference shape), not a framework-specific
// adapter -- dsh has no separate plugin SDK beyond Cordis itself.

import { execFile } from 'node:child_process'
import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'

export const name = 'vss-cli'
export const inject = ['tools']

const MAX_CAPTURE = 200_000

function clip(text: string): { text: string; truncated: boolean } {
  if (text.length <= MAX_CAPTURE) return { text, truncated: false }
  return { text: `${text.slice(0, MAX_CAPTURE)}\n…[truncated]`, truncated: true }
}

interface VssCliArgs {
  args: string[]
  cwd?: string
  timeoutSec?: number
}

interface VssCliResult {
  command: string
  exitCode: number | null
  signal: string | null
  timedOut: boolean
  stdout: string
  stderr: string
  truncated: boolean
}

export function apply(ctx: Context): void {
  const bin = process.env.VSS_BIN ?? '/usr/local/bin/vss'
  const defaultTimeoutSec = Number(process.env.VSS_CLI_DEFAULT_TIMEOUT_SEC ?? 600)

  ctx.tools.register(defineTool({
    name: 'vss_cli',
    description:
      'Run the NVIDIA VSS command-line client (`vss`) against the configured VSS deployment. '
      + 'Pass the subcommand and flags as an argument array; the VSS skills describe which '
      + 'subcommands to use. Returns exit code, stdout and stderr.',
    parameters: {
      args: {
        type: 'array',
        required: true,
        items: { type: 'string' },
        description:
          'Arguments after `vss`, one per element, for example ["summarize", "--help"] or '
          + '["ask", "--file", "clip.mp4", "what happens?"].',
      },
      cwd: { type: 'string', description: 'Working directory for the call. Defaults to the process cwd.' },
      timeoutSec: { type: 'integer', description: 'Seconds before the call is killed.' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          command: { type: 'string', required: true },
          exitCode: { required: true, oneOf: [{ type: 'integer' }, { type: 'null' }] },
          signal: { required: true, oneOf: [{ type: 'string' }, { type: 'null' }] },
          timedOut: { type: 'boolean', required: true },
          stdout: { type: 'string', required: true },
          stderr: { type: 'string', required: true },
          truncated: { type: 'boolean', required: true },
        },
      },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
    },
    async execute(args: VssCliArgs, exec): Promise<VssCliResult> {
      const command = [bin, ...args.args].join(' ')
      const timeoutMs = 1000 * (args.timeoutSec ?? defaultTimeoutSec)
      return new Promise((resolve) => {
        const child = execFile(
          bin,
          args.args,
          { cwd: args.cwd, timeout: timeoutMs, maxBuffer: 64 * 1024 * 1024, signal: exec.signal },
          (error, stdout, stderr) => {
            const out = clip(String(stdout ?? ''))
            const err = clip(String(stderr ?? ''))
            const e = error as (NodeJS.ErrnoException & { killed?: boolean; signal?: string; code?: number | string }) | null
            const spawnFailure = e && typeof e.code === 'string' ? `${e.code}: ${e.message}` : ''
            resolve({
              command,
              exitCode: e ? (typeof e.code === 'number' ? e.code : null) : (child.exitCode ?? 0),
              signal: e?.signal ?? null,
              timedOut: Boolean(e?.killed && e?.signal === 'SIGTERM'),
              stdout: out.text,
              stderr: spawnFailure ? `${err.text}${err.text ? '\n' : ''}${spawnFailure}` : err.text,
              truncated: out.truncated || err.truncated,
            })
          },
        )
      })
    },
  }))
}
