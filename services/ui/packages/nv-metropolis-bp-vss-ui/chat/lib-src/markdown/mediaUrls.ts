// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

interface MarkdownNode {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: MarkdownNode[];
}

/**
 * Rebase NemoClaw's sandbox-only VST URLs after Markdown and raw HTML parsing.
 * This preserves autolinks and leaves literal code (including copied snippets)
 * intact while links, embedded media, and visible URL text use the UI route.
 */
export function rehypeOpenShellMediaUrls({ mediaProxyUrl }: { mediaProxyUrl?: string } = {}) {
  let proxyPath = '';
  if (mediaProxyUrl) {
    try {
      proxyPath = new URL(mediaProxyUrl, 'https://vss-ui.invalid').pathname;
      let end = proxyPath.length;
      while (end > 0 && proxyPath[end - 1] === '/') end -= 1;
      proxyPath = proxyPath.slice(0, end);
    } catch {
      // The ingress still serves /vst directly when no proxy path is usable.
    }
  }
  const normalize = (value: string) => value.replace(
    /\bhttps?:\/\/host\.openshell\.internal(?::\d+)?(?=\/vst\/)/gi,
    proxyPath,
  );

  const visit = (node: MarkdownNode): void => {
    if (node.tagName === 'code' || node.tagName === 'pre') return;
    if (node.type === 'text' && typeof node.value === 'string') {
      node.value = normalize(node.value);
    }
    if (node.properties) {
      // URL-bearing attributes include srcSet and visible labels such as alt
      // and title, as well as the usual href/src media targets.
      for (const [key, value] of Object.entries(node.properties)) {
        if (typeof value === 'string') node.properties[key] = normalize(value);
      }
    }
    node.children?.forEach(visit);
  };
  return visit;
}
