// SPDX-License-Identifier: MIT
/**
 * React hook for the vss-alert-bridge realtime alert rule API
 * (see "Realtime alert API" PDF).
 *
 * Endpoints (relative to the configured alerts API base URL, which already
 * includes the API version prefix, e.g. `<host>/alert-bridge/api/v1`):
 *
 *   - GET    /realtime
 *   - POST   /realtime
 *   - DELETE /realtime/{alert_rule_id}
 */

import { useCallback, useEffect, useState } from 'react';
import { RealtimeAlertRule } from '../types';

interface UseRealtimeAlertRulesOptions {
  alertsApiUrl?: string;
}

export interface CreateRealtimeRuleInput {
  live_stream_url: string;
  alert_type: string;
  prompt: string;
  /** Optional friendly label for the sensor (per the updated API spec). */
  sensor_name?: string;
}

const REALTIME_PATH = '/realtime';

const buildBase = (alertsApiUrl?: string) => (alertsApiUrl ?? '').replace(/\/+$/, '');

const parseError = async (response: Response): Promise<string> => {
  try {
    const body = await response.json();
    if (body && typeof body === 'object') {
      if (typeof body.message === 'string') return body.message;
      if (typeof body.error === 'string') return body.error;
    }
  } catch {
    // fall through
  }
  return `${response.status} ${response.statusText}`;
};

export const useRealtimeAlertRules = ({
  alertsApiUrl,
}: UseRealtimeAlertRulesOptions) => {
  const [rules, setRules] = useState<RealtimeAlertRule[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);


  const fetchRules = useCallback(
    async (signal?: AbortSignal): Promise<RealtimeAlertRule[]> => {
      if (!alertsApiUrl) {
        setError('Alerts API URL is not configured');
        return [];
      }
      setLoading(true);
      setError(null);
      try {
        const response = await fetch(
          `${buildBase(alertsApiUrl)}${REALTIME_PATH}`,
          { signal },
        );
        if (!response.ok) {
          throw new Error(await parseError(response));
        }
        const body = await response.json();
        const list: RealtimeAlertRule[] = Array.isArray(body?.rules) ? body.rules : [];
        if (signal?.aborted) return [];
        setRules(list);
        return list;
      } catch (err) {
        // Swallow aborts silently — they fire intentionally on unmount.
        if (signal?.aborted || (err instanceof DOMException && err.name === 'AbortError')) {
          return [];
        }
        const message = err instanceof Error ? err.message : 'Failed to load alert rules';
        setError(message);
        return [];
      } finally {
        if (!signal?.aborted) {
          setLoading(false);
        }
      }
    },
    [alertsApiUrl],
  );

  const createRule = useCallback(
    async (input: CreateRealtimeRuleInput): Promise<RealtimeAlertRule> => {
      if (!alertsApiUrl) {
        throw new Error('Alerts API URL is not configured');
      }
      const response = await fetch(`${buildBase(alertsApiUrl)}${REALTIME_PATH}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
      });
      if (!response.ok) {
        throw new Error(await parseError(response));
      }
      const body = await response.json();
      const id: string | undefined = body?.id;
      if (!id) {
        throw new Error('rtvi_invalid_response: server response missing rule id');
      }
      // The POST response only returns id/created_at, so refresh the list to
      // get the full rule (and surface server-side defaults like model).
      const refreshed = await fetchRules();
      const rule =
        refreshed.find((r) => r.id === id) ?? {
          id,
          live_stream_url: input.live_stream_url,
          alert_type: input.alert_type,
          prompt: input.prompt,
          ...(input.sensor_name ? { sensor_name: input.sensor_name } : {}),
          status: 'active',
          created_at: body?.created_at,
        };
      return rule;
    },
    [alertsApiUrl, fetchRules],
  );

  const deleteRule = useCallback(
    async (id: string): Promise<void> => {
      if (!alertsApiUrl) {
        throw new Error('Alerts API URL is not configured');
      }
      const response = await fetch(
        `${buildBase(alertsApiUrl)}${REALTIME_PATH}/${encodeURIComponent(id)}`,
        { method: 'DELETE' },
      );
      if (!response.ok) {
        throw new Error(await parseError(response));
      }
      setRules((prev) => prev.filter((rule) => rule.id !== id));
    },
    [alertsApiUrl],
  );

  useEffect(() => {
    const controller = new AbortController();
    fetchRules(controller.signal);
    return () => {
      controller.abort();
    };
  }, [fetchRules]);

  return { rules, loading, error, refetch: fetchRules, createRule, deleteRule };
};
