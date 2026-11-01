// SPDX-License-Identifier: Apache-2.0

import type { InferenceModel, InferenceSettings } from '@/modules/contract/domain/types';

/**
 * Outbound port for accessing the shared incident contract artifacts
 * (prompt, JSON schema, version, inference settings, and model allowlist).
 */
export interface ContractCatalog {
  contractVersion(): string;
  schema(): Record<string, unknown>;
  extractionPrompt(): string;
  inferenceSettings(): InferenceSettings;
  allowedModels(): InferenceModel[];
  findAllowedModel(modelId: string): InferenceModel | undefined;
  responseFormat(): Record<string, unknown>;
  promptSha256(): string;
  schemaSha256(): string;
  /** A registered prompt from contracts/prompts/ (for example `p1/reviewer-preamble`), hash-checked. */
  prompt(id: string): string;
}

let activeCatalog: ContractCatalog | null = null;

export function setContractCatalog(catalog: ContractCatalog): void {
  activeCatalog = catalog;
}

export function getContractCatalog(): ContractCatalog {
  if (!activeCatalog) {
    throw new Error('ContractCatalog port has not been configured');
  }
  return activeCatalog;
}

export function hasContractCatalog(): boolean {
  return activeCatalog !== null;
}
