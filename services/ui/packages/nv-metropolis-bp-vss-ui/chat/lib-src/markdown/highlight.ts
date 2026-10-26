// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * Whether a fenced block is small enough to syntax-highlight.
 *
 * Prism emits a DOM span per token, and CSS max-height only clips paint — the
 * full token DOM is still built. Skip colour above this cap and render plain
 * text instead (do not load or run the highlighter).
 *
 * 10k characters is per block. A single agent turn can expand several
 * Input/Output JSON blocks at once, so the budget is shared across them.
 */
export const CODE_HIGHLIGHT_MAX_CHARS = 10_000;

export function shouldHighlightCode(value: string): boolean {
  return value.length > 0 && value.length <= CODE_HIGHLIGHT_MAX_CHARS;
}
