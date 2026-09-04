// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT AND Apache-2.0
/**
 * Repairs for markdown that is only half-written.
 *
 * A streamed answer is re-parsed on every token, so the parser routinely sees a
 * tag that has been opened but not closed and an `<img src="` with no closing
 * quote. Left alone, react-markdown renders the raw text of the broken tag and
 * the message visibly flickers between garbage and content.
 *
 * Each repair swaps the incomplete fragment for a placeholder that renders
 * cleanly, and marks the still-open element with `data-streaming="true"` so the
 * component can show a spinner. Ported from the toolkit's `fixMalformedHtml`.
 *
 * Pure string functions, kept out of the components so they can be tested.
 */

const LOADING_IMG = '<img src="loading" alt="loading" style="max-width: 100%; height: 100%;" />';
const LOADING_VIDEO =
  '<video controls width="400" height="200"><source src="loading" type="video/mp4"></video>';

/** `![alt](https://…` with no closing paren yet. */
export function replaceMalformedMarkdownImages(str = ''): string {
  return str.replace(/!\[.*?\]\(([^)]*)$/, LOADING_IMG);
}

/** `<img src="…` with no closing `>` yet. */
export function replaceMalformedHtmlImages(str = ''): string {
  return str.replace(/<img\s+[^>]*$/, LOADING_IMG);
}

/** `<video …` with no closing `>` yet. */
export function replaceMalformedHtmlVideos(str = ''): string {
  return str.replace(/<video\s+[^>]*$/, LOADING_VIDEO);
}

/**
 * Balance an unclosed custom tag and flag the newest one as still streaming.
 *
 * Only the *last* unclosed opener is marked: earlier ones are complete as far
 * as the reader is concerned, and marking them all would spin every step in the
 * trace at once.
 */
function balanceTag(str: string, tag: string, notFollowedBy?: string): string {
  const suffix = notFollowedBy ? `(?!${notFollowedBy})` : '';
  // Clear stale markers first, so a tag that closed since the last frame stops
  // claiming to be in flight.
  str = str.replace(
    new RegExp(`<${tag}([^>]*)\\s+data-streaming="true"([^>]*)>`, 'g'),
    `<${tag}$1$2>`,
  );

  const openers: { index: number; fullTag: string }[] = [];
  const openRegex = new RegExp(`<${tag}(\\s[^>]*)?>${suffix}`, 'g');
  let match: RegExpExecArray | null;
  while ((match = openRegex.exec(str)) !== null) {
    openers.push({ index: match.index, fullTag: match[0] });
  }

  const closeRegex = new RegExp(`</${tag}>${suffix}`, 'g');
  let closeCount = 0;
  while (closeRegex.exec(str) !== null) closeCount += 1;

  const incomplete = openers.length - closeCount;
  if (incomplete <= 0 || !openers.length) return str;

  const last = openers[openers.length - 1];
  const marked = last.fullTag.replace(/>$/, ' data-streaming="true">');
  return (
    str.substring(0, last.index) +
    marked +
    str.substring(last.index + last.fullTag.length) +
    Array(incomplete).fill(`</${tag}>`).join('')
  );
}

export function handleIncompleteAgentThinkTags(str = ''): string {
  // `-step` excluded so `<agent-think-step>` is not counted as `<agent-think>`.
  return balanceTag(str, 'agent-think', '-step');
}

export function handleIncompleteAgentThinkStepTags(str = ''): string {
  return balanceTag(str, 'agent-think-step');
}

export function fixMalformedHtml(content = ''): string {
  try {
    let fixed = replaceMalformedHtmlImages(content);
    fixed = replaceMalformedHtmlVideos(fixed);
    fixed = replaceMalformedMarkdownImages(fixed);
    fixed = handleIncompleteAgentThinkTags(fixed);
    fixed = handleIncompleteAgentThinkStepTags(fixed);
    return fixed;
  } catch {
    return content;
  }
}
