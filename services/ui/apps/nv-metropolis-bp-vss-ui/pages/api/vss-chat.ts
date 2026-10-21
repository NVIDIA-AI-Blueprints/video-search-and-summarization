// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * Server-side proxy from the VSS chat surfaces to the agent backend.
 *
 * Needed because the replacement ChatPanel fetches from the browser, while the
 * configured backends (the agent adapter) sit on host-private ports that a
 * browser cannot reach. Proxying server-side also keeps the adapter's address
 * out of the client bundle.
 *
 * The SSE stream is piped through untouched; all parsing happens in
 * @nv-metropolis-bp-vss-ui/chat.
 *
 * Targets resolve from server-only vars first, then fall back to the existing
 * NEXT_PUBLIC_* chat completion URLs so an existing deployment needs no new
 * configuration.
 */
import type { NextApiRequest, NextApiResponse } from "next";

const backendFor = (surface: string): string | undefined => {
  if (surface === "sidebar") {
    return (
      process.env.VSS_CHAT_BACKEND_SIDEBAR ||
      process.env.NEXT_PUBLIC_SIDEBAR_CHAT_HTTP_CHAT_COMPLETION_URL ||
      process.env.NEXT_PUBLIC_HTTP_CHAT_COMPLETION_URL
    );
  }
  if (surface === "main") {
    return (
      process.env.VSS_CHAT_BACKEND_MAIN ||
      process.env.NEXT_PUBLIC_HTTP_CHAT_COMPLETION_URL
    );
  }
  return undefined;
};

export const config = {
  api: {
    bodyParser: { sizeLimit: "5mb" },
    // Intermediate-step payloads on a report turn easily exceed the 4MB default,
    // which aborts the SSE response and looks like a clean stream EOF.
    responseLimit: false,
  },
};

const abortIfClientDropped = (
  res: NextApiResponse,
  controller: AbortController,
) => {
  if (!res.writableEnded) controller.abort();
};

/** Node fetch reports a cut socket as the one-word `terminated`. */
const AGENT_CONNECTION_CLOSED =
  "The agent connection closed before the response completed.";

const streamDropMessage = (err: unknown): string => {
  const raw = err instanceof Error ? err.message : String(err);
  return /^terminated$/i.test(raw.trim()) ? AGENT_CONNECTION_CLOSED : raw;
};

export default async function handler(
  req: NextApiRequest,
  res: NextApiResponse,
) {
  if (req.method !== "POST") {
    res.status(405).json({ error: "method not allowed" });
    return;
  }

  const surface = String(req.query.surface ?? "main");
  const target = backendFor(surface);
  if (!target) {
    res
      .status(503)
      .json({ error: `no backend configured for surface: ${surface}` });
    return;
  }

  req.socket?.setTimeout(0);
  res.socket?.setTimeout(0);

  const controller = new AbortController();
  // Drop the upstream turn only when the browser actually hung up. `req.close`
  // also fires after the POST body is consumed, which would abort a healthy
  // stream the moment we start reading it.
  req.on("aborted", () => abortIfClientDropped(res, controller));
  res.on("close", () => abortIfClientDropped(res, controller));

  const interactionPath = String(req.query.interaction ?? "");
  if (interactionPath) {
    if (
      !/^\/executions\/[^/]+\/interactions\/[^/]+\/response$/.test(
        interactionPath,
      )
    ) {
      res.status(400).json({ error: "invalid interaction response path" });
      return;
    }
    try {
      const interactionTarget = new URL(interactionPath, target).toString();
      const upstream = await fetch(interactionTarget, {
        method: "POST",
        signal: controller.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(req.body ?? {}),
      });
      if (!upstream.ok) {
        res
          .status(502)
          .json({ error: `agent backend returned HTTP ${upstream.status}` });
        return;
      }
      res.status(204).end();
    } catch (err) {
      res.status(502).json({
        error: "agent interaction response failed",
        detail: err instanceof Error ? err.message : String(err),
      });
    }
    return;
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: "POST",
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        "Conversation-Id": String(req.headers["conversation-id"] ?? ""),
      },
      body: JSON.stringify(req.body ?? {}),
    });
  } catch (err) {
    res.status(502).json({
      error: `agent backend unreachable at ${target}`,
      detail: err instanceof Error ? err.message : String(err),
    });
    return;
  }

  if (!upstream.ok || !upstream.body) {
    res
      .status(502)
      .json({ error: `agent backend returned HTTP ${upstream.status}` });
    return;
  }

  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache, no-transform",
    Connection: "close",
    "X-Accel-Buffering": "no",
  });

  const reader = upstream.body.getReader();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      res.write(Buffer.from(value));
      // Next buffers by default; without flushing, SSE arrives all at once.
      (res as unknown as { flush?: () => void }).flush?.();
    }
  } catch (err) {
    // Swallowing this used to look like a successful EOF, so the chat UI
    // reported "backend event stream ended before the response completed"
    // with no cause. Forward the failure on the same SSE channel.
    if (!controller.signal.aborted && !res.writableEnded) {
      // Leading newline so a drop mid-line does not glue `error_data:` onto
      // the unfinished frame; SseParser would then miss the error.
      res.write(
        `\nerror_data: ${JSON.stringify({ message: streamDropMessage(err) })}\n`,
      );
    }
  } finally {
    reader.releaseLock();
    if (!res.writableEnded) res.end();
  }
}
