// SPDX-License-Identifier: Apache-2.0

// Server-only access to the shared incident contract in
// dev-profile-incident/contracts/ (the same files eval/contract.py loads).
// Nothing here is copied into the console: the files are read at runtime so a
// contract change reaches eval and the console together.

import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import path from 'node:path';

import type { InferenceModel, InferenceSettings } from '@/modules/contract/domain/types';
import type { ContractCatalog } from '@/modules/contract/domain/ContractCatalog';
import { setContractCatalog } from '@/modules/contract/domain/ContractCatalog';

// Keys describing the schema document itself; stripped from what is sent to the
// model because some OpenAI-compatible servers reject them inside response_format
// (mirrors eval/contract.py _DOCUMENT_ONLY_KEYS).
const DOCUMENT_ONLY_KEYS = new Set(['$schema', '$id']);

const cache = new Map<string, string>();

export function contractsDir(): string {
  const configured = process.env.CONTRACTS_DIR?.trim();
  return configured ? path.resolve(configured) : path.resolve(process.cwd(), '..', 'contracts');
}

function readContractFile(name: string): string {
  const filePath = path.join(contractsDir(), name);
  const cached = cache.get(filePath);
  if (cached !== undefined) return cached;
  let text: string;
  try {
    text = readFileSync(filePath, 'utf8');
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    throw new Error(`Could not read the shared incident contract file ${name} from ${contractsDir()}: ${detail}`);
  }
  cache.set(filePath, text);
  return text;
}

function sha256(text: string): string {
  return createHash('sha256').update(text, 'utf8').digest('hex');
}

export function contractVersion(): string {
  return readContractFile('VERSION').trim();
}

export function extractionPrompt(): string {
  return readContractFile('incident_extraction_prompt.txt');
}

export function schemaText(): string {
  return readContractFile('incident_report.schema.json');
}

export function schema(): Record<string, unknown> {
  return JSON.parse(schemaText()) as Record<string, unknown>;
}

export function inferenceSettings(): InferenceSettings {
  return JSON.parse(readContractFile('inference.json')) as InferenceSettings;
}

export function allowedModels(): InferenceModel[] {
  return inferenceSettings().models;
}

export function findAllowedModel(modelId: string): InferenceModel | undefined {
  return allowedModels().find((model) => model.id === modelId);
}

/** OpenAI-compatible response_format enforcing the contract (strict), identical to eval's contract.response_format(). */
export function responseFormat(): Record<string, unknown> {
  const strippedSchema = Object.fromEntries(Object.entries(schema()).filter(([key]) => !DOCUMENT_ONLY_KEYS.has(key)));
  return { type: 'json_schema', json_schema: { name: 'incident_report', schema: strippedSchema, strict: true } };
}

export function promptSha256(): string {
  return sha256(extractionPrompt());
}

export function schemaSha256(): string {
  return sha256(schemaText());
}

interface PromptEntry {
  id: string;
  file: string;
  sha256: string;
  used_by: string[];
}

/** A prompt the console uses from contracts/prompts/registry.json, after checking its file's SHA-256. */
export function prompt(id: string): string {
  const registry = JSON.parse(readContractFile('prompts/registry.json')) as { prompts: PromptEntry[] };
  const entry = registry.prompts.find((item) => item.id === id && item.used_by.includes('console'));
  if (!entry) throw new Error(`contracts/prompts/registry.json has no console prompt ${id}`);
  const text = readContractFile(`prompts/${entry.file}`);
  if (sha256(text) !== entry.sha256) {
    throw new Error(`contracts/prompts/${entry.file} does not match its registered sha256`);
  }
  return text;
}

export class FileContractCatalog implements ContractCatalog {
  contractVersion(): string {
    return contractVersion();
  }
  schema(): Record<string, unknown> {
    return schema();
  }
  extractionPrompt(): string {
    return extractionPrompt();
  }
  inferenceSettings(): InferenceSettings {
    return inferenceSettings();
  }
  allowedModels(): InferenceModel[] {
    return allowedModels();
  }
  findAllowedModel(modelId: string): InferenceModel | undefined {
    return findAllowedModel(modelId);
  }
  responseFormat(): Record<string, unknown> {
    return responseFormat();
  }
  promptSha256(): string {
    return promptSha256();
  }
  schemaSha256(): string {
    return schemaSha256();
  }
  prompt(id: string): string {
    return prompt(id);
  }
}

export const defaultContractCatalog: ContractCatalog = new FileContractCatalog();
setContractCatalog(defaultContractCatalog);
