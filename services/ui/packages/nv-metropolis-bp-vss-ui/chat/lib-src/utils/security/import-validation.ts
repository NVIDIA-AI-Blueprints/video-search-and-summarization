// SPDX-License-Identifier: MIT
/**
 * Import file validation.
 *
 * An export file is user-supplied JSON that becomes conversation state, so it
 * is treated as untrusted: bounded before parsing, stripped of keys that reach
 * Object.prototype, and accepted only if it matches a known export shape.
 */
import toast from 'react-hot-toast';

import { MAX_FILE_SIZE_BYTES } from '../../constants';
import type { SupportedExportFormats } from '../../types/export';

/** Keys that let a crafted file write through to Object.prototype. */
const PROTOTYPE_POLLUTION_KEYS = ['__proto__', 'constructor', 'prototype'];

/** Rebuilds the value without any prototype-polluting key, at any depth. */
function sanitizeObject(value: any): any {
  if (value === null || typeof value !== 'object') return value;

  if (Array.isArray(value)) return value.map(sanitizeObject);

  const sanitized: Record<string, unknown> = {};
  for (const [key, nested] of Object.entries(value)) {
    if (PROTOTYPE_POLLUTION_KEYS.includes(key)) {
      console.warn(`Blocked dangerous key during import: ${key}`);
      continue;
    }
    sanitized[key] = sanitizeObject(nested);
  }

  return sanitized;
}

/** v1: a bare array of numerically-keyed conversations. */
function isV1Shape(value: any[]): boolean {
  return value.every(
    (item) =>
      typeof item === 'object' &&
      item !== null &&
      typeof item.id === 'number' &&
      typeof item.name === 'string' &&
      Array.isArray(item.messages),
  );
}

/**
 * Parses and validates an export file.
 *
 * Reports the reason to the user via toast and returns null, rather than
 * throwing: import is a user action and every failure here is theirs to fix.
 */
export function validateImportData(rawJson: string): SupportedExportFormats | null {
  if (!rawJson || typeof rawJson !== 'string') return null;

  // Checked on the raw string so an oversized file is rejected before parsing.
  if (rawJson.length > MAX_FILE_SIZE_BYTES) {
    const maxSizeMB = Math.round(MAX_FILE_SIZE_BYTES / (1024 * 1024));
    toast.error(`Import file too large (max ${maxSizeMB}MB)`);
    return null;
  }

  let parsed: any;
  try {
    parsed = JSON.parse(rawJson);
  } catch {
    toast.error('Invalid JSON format');
    return null;
  }

  if (parsed === null || typeof parsed !== 'object') {
    toast.error('Import data must be a valid object');
    return null;
  }

  const sanitized = sanitizeObject(parsed);

  if (Array.isArray(sanitized)) {
    if (isV1Shape(sanitized)) return sanitized as SupportedExportFormats;
  } else {
    if (
      sanitized.version === 4 &&
      Array.isArray(sanitized.history) &&
      Array.isArray(sanitized.folders) &&
      Array.isArray(sanitized.prompts)
    ) {
      return sanitized as SupportedExportFormats;
    }

    if (
      sanitized.version === 3 &&
      Array.isArray(sanitized.history) &&
      Array.isArray(sanitized.folders)
    ) {
      return sanitized as SupportedExportFormats;
    }

    // v2 is unversioned; both fields are nullable in that format.
    if (
      (sanitized.history === null || Array.isArray(sanitized.history)) &&
      (sanitized.folders === null || Array.isArray(sanitized.folders))
    ) {
      return sanitized as SupportedExportFormats;
    }
  }

  toast.error('Invalid import format. Please use a valid export file.');
  return null;
}
