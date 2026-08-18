'use client';
// SPDX-License-Identifier: MIT
/**
 * Per-instance runtime configuration.
 *
 * The app mounts chat more than once — a full tab and a docked sidebar — and
 * each needs its own workflow name and its own storage namespace. Reading
 * those from env would give every instance the same values, so an embedding
 * app supplies them through this context and env becomes the fallback.
 */
import React, { createContext, useContext } from 'react';
import { env } from 'next-runtime-env';

export interface RuntimeConfig {
  /** Overrides NEXT_PUBLIC_WORKFLOW for this instance. */
  workflow?: string;
  /** Overrides NEXT_PUBLIC_RIGHT_MENU_OPEN for this instance. */
  rightMenuOpen?: boolean;
  /**
   * Namespaces this instance's stored conversations and folders, so a sidebar
   * and a full-tab chat on the same page do not share history.
   */
  storageKeyPrefix?: string;
}

/** `prefix ? `${prefix}_${baseKey}` : baseKey` */
export function getStorageKey(baseKey: string, prefix?: string | null): string {
  return prefix ? `${prefix}_${baseKey}` : baseKey;
}

/** Query param wins over env, so a deep link can target a specific workflow. */
export function getWorkflowNameFromEnv(defaultName = 'Agent'): string {
  if (typeof window !== 'undefined') {
    const fromQuery = new URLSearchParams(window.location.search).get('workflow');
    if (fromQuery) return fromQuery;
  }

  return (
    env('NEXT_PUBLIC_WORKFLOW') ||
    process?.env?.NEXT_PUBLIC_WORKFLOW ||
    defaultName
  );
}

const RuntimeConfigContext = createContext<RuntimeConfig | undefined>(undefined);

export interface RuntimeConfigProviderProps {
  value?: RuntimeConfig;
  children: React.ReactNode;
}

export function RuntimeConfigProvider({ value, children }: RuntimeConfigProviderProps) {
  return (
    <RuntimeConfigContext.Provider value={value}>{children}</RuntimeConfigContext.Provider>
  );
}

export function useRuntimeConfig(): RuntimeConfig | undefined {
  return useContext(RuntimeConfigContext);
}

/** Instance override when set and non-empty, otherwise env. */
export function useWorkflowName(): string {
  const config = useRuntimeConfig();
  const fromEnv = getWorkflowNameFromEnv();

  return config?.workflow ? config.workflow : fromEnv;
}

export function useRightMenuOpenDefault(): boolean {
  const config = useRuntimeConfig();
  if (config?.rightMenuOpen !== undefined) return config.rightMenuOpen;

  return (
    env('NEXT_PUBLIC_RIGHT_MENU_OPEN') === 'true' ||
    process?.env?.NEXT_PUBLIC_RIGHT_MENU_OPEN === 'true'
  );
}
