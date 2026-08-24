'use client';

import React, { createContext, useContext } from 'react';

import { env } from 'next-runtime-env';
import { getWorkflowName } from '@/utils/app/helper';

/**
 * Optional runtime overrides for values that would otherwise be read from env at runtime.
 * When provided (e.g. by an embedding app), the chat uses these instead of env() so
 * multiple instances can have different workflow names, etc., without mutating global env.
 */
export interface RuntimeConfig {
  /** Override for workflow name (else read from NEXT_PUBLIC_WORKFLOW via env). */
  workflow?: string;
  /** Override for right menu open default (else read from NEXT_PUBLIC_RIGHT_MENU_OPEN). */
  rightMenuOpen?: boolean;
  /**
   * When set (e.g. "searchTab"), conversation/folder storage uses prefixed keys
   * so multiple chat instances (main vs sidebar) keep separate history.
   */
  storageKeyPrefix?: string;
}

/** Build sessionStorage key: prefix ? `${prefix}_${baseKey}` : baseKey */
export function getStorageKey(baseKey: string, prefix?: string | null): string {
  return prefix ? `${prefix}_${baseKey}` : baseKey;
}

const RuntimeConfigContext = createContext<RuntimeConfig | undefined>(undefined);

export interface RuntimeConfigProviderProps {
  value?: RuntimeConfig;
  children: React.ReactNode;
}

export function RuntimeConfigProvider({ value, children }: RuntimeConfigProviderProps) {
  return (
    <RuntimeConfigContext.Provider value={value}>
      {children}
    </RuntimeConfigContext.Provider>
  );
}

export function useRuntimeConfig(): RuntimeConfig | undefined {
  return useContext(RuntimeConfigContext);
}

/** Workflow name: from RuntimeConfig if provided, otherwise from env (getWorkflowName). */
export function useWorkflowName(): string {
  const config = useRuntimeConfig();
  const fromEnv = getWorkflowName();
  return (config?.workflow != null && config.workflow !== '') ? config.workflow : fromEnv;
}

/** Right menu open default: from RuntimeConfig if provided, otherwise from env. */
export function useRightMenuOpenDefault(): boolean {
  const config = useRuntimeConfig();
  if (config?.rightMenuOpen !== undefined) return config.rightMenuOpen;
  return (
    env('NEXT_PUBLIC_RIGHT_MENU_OPEN') === 'true' ||
    process?.env?.NEXT_PUBLIC_RIGHT_MENU_OPEN === 'true'
  );
}
