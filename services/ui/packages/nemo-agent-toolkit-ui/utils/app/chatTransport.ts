// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

export interface ResolveWebSocketModeOptions {
  forceHttpTransport: boolean;
  storedWebSocketMode: string | null;
  configuredWebSocketMode?: boolean;
}

/** Resolve the transport without allowing a saved preference to bypass an HTTP lock. */
export const resolveWebSocketMode = ({
  forceHttpTransport,
  storedWebSocketMode,
  configuredWebSocketMode,
}: ResolveWebSocketModeOptions): boolean => {
  if (forceHttpTransport) return false;
  if (storedWebSocketMode !== null) return storedWebSocketMode === 'true';
  return configuredWebSocketMode ?? false;
};
