// SPDX-License-Identifier: MIT
/**
 * Conversation list logic, kept free of React so it can be unit tested.
 *
 * Export/import stay wire-compatible with the toolkit's v4 format
 * (`{version: 4, history, folders, prompts}`) so a file exported from the old
 * chat bar imports into this one. Folders and prompts are accepted and
 * round-tripped but not rendered — VSS never surfaced either.
 */
import type { ChatMessage, Conversation } from './types';

export const NEW_CONVERSATION_NAME = 'New Conversation';

/** Export envelope, matching the toolkit's `ExportFormatV4`. */
export interface ChatExportV4 {
  version: 4;
  history: Conversation[];
  folders: unknown[];
  prompts: unknown[];
}

let seq = 0;
export const newId = (): string =>
  `c${Date.now().toString(36)}-${(seq++).toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

export function createConversation(name = NEW_CONVERSATION_NAME): Conversation {
  return { id: newId(), name, messages: [] };
}

/**
 * Name a conversation after its first user message.
 *
 * The toolkit truncates at 30 characters; matched here so titles look the same
 * in a sidebar that may show both during the migration.
 */
export function titleFromMessage(content: string): string {
  const trimmed = content.trim();
  if (!trimmed) return NEW_CONVERSATION_NAME;
  return trimmed.length > 30 ? `${trimmed.substring(0, 30)}...` : trimmed;
}

/** Case-insensitive match over conversation names and message text. */
export function filterConversations(
  conversations: Conversation[],
  searchTerm: string,
): Conversation[] {
  const term = searchTerm.trim().toLowerCase();
  if (!term) return conversations;
  return conversations.filter((c) => {
    if (c.name.toLowerCase().includes(term)) return true;
    return c.messages.some((m) => !m.hidden && m.content.toLowerCase().includes(term));
  });
}

/**
 * Strip fields that should never outlive the turn that created them.
 *
 * `streaming` would restore a conversation stuck mid-answer with a blinking
 * cursor and no request behind it; `uploadConversationId` is only meaningful
 * while an upload is in flight.
 */
export function sanitizeForPersistence(conversations: Conversation[]): Conversation[] {
  return conversations.map((c) => ({
    ...c,
    messages: c.messages.map(({ streaming: _s, uploadConversationId: _u, ...rest }) => rest),
  }));
}

export function buildExport(conversations: Conversation[]): ChatExportV4 {
  return {
    version: 4,
    history: sanitizeForPersistence(conversations),
    folders: [],
    prompts: [],
  };
}

export function exportFilename(): string {
  const date = new Date();
  return `vss_chat_history_${date.getMonth() + 1}-${date.getDate()}.json`;
}

const DANGEROUS_KEYS = ['__proto__', 'constructor', 'prototype'];

/**
 * Recursively drop prototype-pollution keys.
 *
 * Imports are user-supplied JSON that we spread into React state; without this
 * a crafted file can reach `Object.prototype`. Ported from the toolkit's
 * `utils/security/import-validation.ts` rather than reinvented.
 */
function sanitizeObject(value: unknown): unknown {
  if (value === null || typeof value !== 'object') return value;
  if (Array.isArray(value)) return value.map(sanitizeObject);
  const out: Record<string, unknown> = {};
  for (const [key, val] of Object.entries(value as Record<string, unknown>)) {
    if (DANGEROUS_KEYS.includes(key)) continue;
    out[key] = sanitizeObject(val);
  }
  return out;
}

function isConversationLike(value: unknown): value is Conversation {
  if (!value || typeof value !== 'object') return false;
  const c = value as Record<string, unknown>;
  return (
    (typeof c.id === 'string' || typeof c.id === 'number') &&
    typeof c.name === 'string' &&
    Array.isArray(c.messages)
  );
}

function normalizeMessages(messages: unknown[]): ChatMessage[] {
  return messages
    .filter((m): m is Record<string, unknown> => !!m && typeof m === 'object')
    .map((m) => ({
      id: typeof m.id === 'string' ? m.id : newId(),
      role: m.role === 'assistant' ? ('assistant' as const) : ('user' as const),
      content: typeof m.content === 'string' ? m.content : '',
      steps: Array.isArray(m.steps) ? (m.steps as ChatMessage['steps']) : undefined,
      callerInfo: typeof m.callerInfo === 'string' ? m.callerInfo : undefined,
      hidden: m.hidden === true,
    }));
}

export const MAX_IMPORT_BYTES = 10 * 1024 * 1024;

export interface ImportResult {
  conversations: Conversation[] | null;
  error?: string;
}

/**
 * Parse an exported file back into conversations.
 *
 * Accepts every format the toolkit accepted (v1 bare array through v4) so old
 * exports still load, and returns a message rather than throwing so the caller
 * can toast it.
 */
export function parseImport(rawJson: string): ImportResult {
  if (!rawJson || typeof rawJson !== 'string') {
    return { conversations: null, error: 'Empty import file' };
  }
  if (rawJson.length > MAX_IMPORT_BYTES) {
    return {
      conversations: null,
      error: `Import file too large (max ${Math.round(MAX_IMPORT_BYTES / (1024 * 1024))}MB)`,
    };
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(rawJson);
  } catch {
    return { conversations: null, error: 'Invalid JSON format' };
  }
  if (parsed === null || typeof parsed !== 'object') {
    return { conversations: null, error: 'Import data must be an object or array' };
  }

  const clean = sanitizeObject(parsed);

  // v1: a bare array of conversations.
  const history: unknown = Array.isArray(clean)
    ? clean
    : (clean as Record<string, unknown>).history;

  if (!Array.isArray(history)) {
    return { conversations: null, error: 'Invalid import format' };
  }

  const conversations = history.filter(isConversationLike).map((c) => ({
    id: String(c.id),
    name: c.name,
    messages: normalizeMessages(c.messages as unknown[]),
  }));

  if (!conversations.length) {
    return { conversations: null, error: 'No conversations found in file' };
  }
  return { conversations };
}
