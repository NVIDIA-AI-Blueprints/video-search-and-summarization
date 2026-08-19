// SPDX-License-Identifier: MIT
/**
 * Serialising attached context for the agent.
 *
 * Tabs attach references — a camera clip, an incident — that the agent needs as
 * data. Only the `data` fields are sent: `id`, `label` and `contextType` exist
 * to render and identify the chip, and passing them would have the agent reason
 * about our UI's bookkeeping.
 */
import type { QueryDataContext } from '../types/chat';

/** Marker the agent looks for when a turn carries attached context. */
const CONTEXT_PREFIX = '[Context:';

/** The `data` payloads, with the UI-only `contextType` stripped if it leaked in. */
export function toContextPayload(
  items: QueryDataContext[],
): Record<string, unknown>[] {
  return items.map(({ data }) => {
    const copy: Record<string, unknown> = { ...(data as Record<string, unknown>) };
    delete copy.contextType;
    return copy;
  });
}

/** Prefixes `message` with its attached context, or returns it unchanged. */
export function withQueryContext(message: string, items: QueryDataContext[]): string {
  if (items.length === 0) return message;

  return `${CONTEXT_PREFIX} ${JSON.stringify(toContextPayload(items))}] ${message}`;
}

/** Adds an item unless one with the same id is already attached. */
export function addQueryContextItem(
  items: QueryDataContext[],
  item: QueryDataContext,
): QueryDataContext[] {
  return items.some((existing) => existing.id === item.id) ? items : [...items, item];
}
