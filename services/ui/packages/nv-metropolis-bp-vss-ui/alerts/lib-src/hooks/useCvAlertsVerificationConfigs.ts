// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  VerificationAlertConfig,
  VerificationAlertConfigUpdate,
} from '../types';

interface UseCvAlertsVerificationConfigsOptions {
  alertsApiUrl?: string;
  /** Refetch persisted rules without resetting editor state. */
  refreshVersion?: number;
}

export interface CreateCvAlertsVerificationConfigInput {
  alert_type: string;
  prompt: string;
  enrichment_prompt?: string | null;
  output_category?: string | null;
}

const CONFIG_PATH = '/verification/config';

const buildBase = (url?: string) => {
  let base = url ?? '';
  while (base.endsWith('/')) base = base.slice(0, -1);
  return base;
};

const parseError = async (response: Response): Promise<string> => {
  try {
    const body = await response.json();
    if (body && typeof body === 'object') {
      if (typeof body.message === 'string') return body.message;
      if (typeof body.error === 'string') return body.error;
      if (Array.isArray(body.detail)) {
        const details = body.detail
          .map((item: unknown) => {
            if (!item || typeof item !== 'object') return '';
            const detail = item as { loc?: unknown[]; msg?: string };
            const field = detail.loc?.filter((part) => part !== 'body').join('.');
            return [field, detail.msg].filter(Boolean).join(': ');
          })
          .filter(Boolean);
        if (details.length > 0) return details.join('; ');
      }
    }
  } catch {
    // Fall through to the HTTP status.
  }
  return `${response.status} ${response.statusText}`;
};

export const useCvAlertsVerificationConfigs = ({
  alertsApiUrl,
  refreshVersion = 0,
}: UseCvAlertsVerificationConfigsOptions) => {
  const [configs, setConfigs] = useState<VerificationAlertConfig[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastRefreshedAt, setLastRefreshedAt] = useState<Date | null>(null);
  // Only the newest GET may update state. Successful mutations invalidate
  // earlier snapshots so a background refresh cannot undo an editor change.
  const requestVersionRef = useRef(0);

  const fetchConfigs = useCallback(
    async (
      signal?: AbortSignal,
      options?: { minLoadingMs?: number },
    ): Promise<VerificationAlertConfig[]> => {
      if (!alertsApiUrl) {
        setError('Alerts API URL is not configured');
        return [];
      }
      const requestVersion = ++requestVersionRef.current;
      setLoading(true);
      setError(null);
      const startedAt = Date.now();
      try {
        const response = await fetch(`${buildBase(alertsApiUrl)}${CONFIG_PATH}`, { signal });
        if (!response.ok) throw new Error(await parseError(response));
        const body = await response.json();
        const list: VerificationAlertConfig[] = Array.isArray(body?.configs)
          ? body.configs
          : [];
        if (signal?.aborted || requestVersion !== requestVersionRef.current) return [];
        setConfigs(list);
        setLastRefreshedAt(new Date());
        return list;
      } catch (err) {
        if (
          signal?.aborted ||
          requestVersion !== requestVersionRef.current ||
          (err instanceof DOMException && err.name === 'AbortError')
        ) {
          return [];
        }
        setError(err instanceof Error ? err.message : 'Failed to load verification configs');
        return [];
      } finally {
        const remaining = (options?.minLoadingMs ?? 0) - (Date.now() - startedAt);
        if (remaining > 0 && !signal?.aborted) {
          await new Promise<void>((resolve) => setTimeout(resolve, remaining));
        }
        if (!signal?.aborted && requestVersion === requestVersionRef.current) setLoading(false);
      }
    },
    [alertsApiUrl],
  );

  const refetch = useCallback(
    (options?: { minLoadingMs?: number }) => fetchConfigs(undefined, options),
    [fetchConfigs],
  );

  const createConfig = useCallback(
    async (input: CreateCvAlertsVerificationConfigInput): Promise<VerificationAlertConfig> => {
      if (!alertsApiUrl) throw new Error('Alerts API URL is not configured');
      const response = await fetch(`${buildBase(alertsApiUrl)}${CONFIG_PATH}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
      });
      if (!response.ok) throw new Error(await parseError(response));
      const created = (await response.json()) as VerificationAlertConfig;
      requestVersionRef.current += 1;
      setLoading(false);
      setConfigs((current) => [
        ...current.filter((config) => config.alert_type !== created.alert_type),
        created,
      ]);
      void fetchConfigs();
      return created;
    },
    [alertsApiUrl, fetchConfigs],
  );

  const updateConfig = useCallback(
    async (
      alertType: string,
      update: VerificationAlertConfigUpdate,
    ): Promise<VerificationAlertConfig> => {
      if (!alertsApiUrl) throw new Error('Alerts API URL is not configured');
      const response = await fetch(
        `${buildBase(alertsApiUrl)}${CONFIG_PATH}/${encodeURIComponent(alertType)}`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(update),
        },
      );
      if (!response.ok) throw new Error(await parseError(response));
      const updated = (await response.json()) as VerificationAlertConfig;
      requestVersionRef.current += 1;
      setLoading(false);
      setConfigs((current) =>
        current.map((config) => (config.alert_type === alertType ? updated : config)),
      );
      void fetchConfigs();
      return updated;
    },
    [alertsApiUrl, fetchConfigs],
  );

  const deleteConfig = useCallback(
    async (alertType: string): Promise<void> => {
      if (!alertsApiUrl) throw new Error('Alerts API URL is not configured');
      const response = await fetch(
        `${buildBase(alertsApiUrl)}${CONFIG_PATH}/${encodeURIComponent(alertType)}`,
        { method: 'DELETE' },
      );
      if (!response.ok) throw new Error(await parseError(response));
      requestVersionRef.current += 1;
      setLoading(false);
      setConfigs((current) => current.filter((config) => config.alert_type !== alertType));
      void fetchConfigs();
    },
    [alertsApiUrl, fetchConfigs],
  );

  useEffect(() => {
    const controller = new AbortController();
    fetchConfigs(controller.signal);
    return () => controller.abort();
  }, [fetchConfigs, refreshVersion]);

  return {
    configs,
    loading,
    error,
    lastRefreshedAt,
    refetch,
    createConfig,
    updateConfig,
    deleteConfig,
  };
};
