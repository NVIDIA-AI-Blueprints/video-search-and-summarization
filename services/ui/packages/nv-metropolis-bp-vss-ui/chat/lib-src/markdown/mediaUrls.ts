// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * NemoClaw can cite VST media through its sandbox-only OpenShell hostname.
 * Rebase those links when displaying an answer so Markdown link targets and
 * visible URL text use the UI's same-origin media route.
 */
export function normalizeOpenShellMediaUrls(content: string, mediaProxyUrl?: string): string {
  let proxyPath = '';
  if (mediaProxyUrl) {
    try {
      proxyPath = new URL(mediaProxyUrl, 'https://vss-ui.invalid').pathname.replace(/\/+$/, '');
    } catch {
      // The ingress still serves /vst directly when no proxy path is usable.
    }
  }
  return content.replace(
    /\bhttps?:\/\/host\.openshell\.internal(?::\d+)?(?=\/vst\/)/gi,
    proxyPath,
  );
}
