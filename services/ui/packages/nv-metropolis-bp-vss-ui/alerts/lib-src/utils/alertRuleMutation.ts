// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Detect a successful realtime alert-rule create / delete / update in assistant
 * prose. Alert Bridge rule mutations are not published as `vss.alert.incidents`
 * artifacts (those are for incident listings), so the Alerts tab has to
 * recognise the completed chat turn from the text the agent returns.
 */

const AGENT_THINK_BLOCK = /<agent-think(?![-\w])[^>]*>[\s\S]*?<\/agent-think>/gi;
const ARTIFACT_BLOCK = /<vss-ui-artifact>[\s\S]*?<\/vss-ui-artifact>/gi;

const FAILURE_PATTERNS = [
  /\bfailed to\b/i,
  /\bcould not\b/i,
  /\bwas not found\b/i,
  /\bnot found in\b/i,
  /\bno matching rule\b/i,
  /\bnothing to stop\b/i,
  /\bstays active\b/i,
  /\bnot reachable\b/i,
  /\bexperiencing issues\b/i,
  /\brequest was rejected\b/i,
  /\bis required\b/i,
];

/** Stop/delete confirmation is a question, not a completed mutation. */
const CONFIRMATION_PATTERN = /\byes\s*\/\s*no\b/i;

const SUCCESS_PATTERNS = [
  // NAT tool + LLM restatements ("Real-time VLM alert successfully started…")
  /real[- ]?time(?:\s+vlm)?\s+alert(?:\s+rule)?[\s\S]{0,160}?(?:has been\s+)?(?:successfully\s+)?(?:started|stopped|created|deleted|removed|updated|modified)/i,
  // Verb-first paraphrases ("started a real-time alert…")
  /(?:successfully\s+)?(?:started|stopped|created|deleted|removed|updated|modified)[\s\S]{0,160}?real[- ]?time(?:\s+vlm)?\s+alert(?:\s+rule)?/i,
  // OpenClaw Workflow D create: "Realtime alert <id> is live on **sensor**"
  /realtime alert\b[\s\S]{0,120}\bis live\b/i,
  // OpenClaw Workflow D delete: "Alert `ppe` on **sensor** has been stopped"
  /\balert\b[\s\S]{0,120}has been stopped\b/i,
  /\balready stopped\b/i,
  /realtime alert rule (?:created|deleted)/i,
];

function visibleAnswerText(answer: string): string {
  return answer
    .replace(AGENT_THINK_BLOCK, ' ')
    .replace(ARTIFACT_BLOCK, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

/** True when the assistant reports a completed alert-rule add, delete, or update. */
export function isSuccessfulAlertRuleMutation(answer: string): boolean {
  if (typeof answer !== 'string' || !answer.trim()) return false;
  const text = visibleAnswerText(answer);
  if (!text) return false;
  if (CONFIRMATION_PATTERN.test(text)) return false;
  if (FAILURE_PATTERNS.some((pattern) => pattern.test(text))) return false;
  return SUCCESS_PATTERNS.some((pattern) => pattern.test(text));
}
