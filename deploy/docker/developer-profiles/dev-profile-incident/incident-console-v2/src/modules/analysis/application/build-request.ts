// SPDX-License-Identifier: Apache-2.0

// Builds the P1 (incident extraction) chat-completions request exactly as
// eval/scripts/eval_vlm_client.py:analyze_video_with_p1 does: the video first,
// then the canonical contract prompt, with the contract schema enforced through
// response_format and the shared P1 inference settings. An optional reviewer
// instruction is appended as a separate, fenced, lower-priority text part; it
// never edits the canonical prompt and cannot change the enforced schema.
// Without an instruction the request body equals eval's P1 body key for key.

import type { ContractCatalog } from '@/modules/contract/domain/ContractCatalog';
import { getContractCatalog } from '@/modules/contract/domain/ContractCatalog';
import type { GatewayCompletionRequest } from '@/modules/analysis/domain/ModelGateway';

export const MAX_ADDITIONAL_INSTRUCTION_LENGTH = 1000;

/** The fenced instruction's preamble, shared with the agent through contracts/prompts/ (p1/reviewer-preamble). */
export function additionalInstructionPreamble(catalog?: ContractCatalog): string {
  return (catalog ?? getContractCatalog()).prompt('p1/reviewer-preamble');
}

/** Trim and strip control characters (keeping newlines and tabs); empty means no instruction. */
export function normalizeAdditionalInstruction(value: unknown): string | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== 'string') throw new RangeError('The additional instruction must be text');
  const cleaned = value
    // biome-ignore lint/suspicious/noControlCharactersInRegex: intentional sanitizer matching ASCII control characters
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, '')
    .replace(/\r\n?/g, '\n')
    .trim();
  if (!cleaned) return null;
  if (cleaned.length > MAX_ADDITIONAL_INSTRUCTION_LENGTH) {
    throw new RangeError(`The additional instruction must be at most ${MAX_ADDITIONAL_INSTRUCTION_LENGTH} characters`);
  }
  return cleaned;
}

/** The P1 settings recorded with every run (eval's FIXED_INFERENCE_CONFIG merged with P1_INFERENCE_CONFIG). */
export function p1InferenceConfig(catalog?: ContractCatalog): Record<string, unknown> {
  const cat = catalog ?? getContractCatalog();
  const { p1 } = cat.inferenceSettings();
  return { temperature: p1.temperature, max_tokens: p1.max_tokens, media_io_kwargs: p1.media_io_kwargs };
}

export function buildP1Request(
  model: string,
  videoUrl: string,
  additionalInstruction: string | null = null,
  catalog?: ContractCatalog,
): GatewayCompletionRequest {
  const cat = catalog ?? getContractCatalog();
  const content: Array<Record<string, unknown>> = [
    { type: 'video_url', video_url: { url: videoUrl } },
    { type: 'text', text: cat.extractionPrompt() },
  ];
  if (additionalInstruction) {
    content.push({ type: 'text', text: `${additionalInstructionPreamble(cat)}\n<<<\n${additionalInstruction}\n>>>` });
  }
  return {
    model,
    messages: [{ role: 'user', content }],
    ...p1InferenceConfig(cat),
    response_format: cat.responseFormat(),
  };
}
