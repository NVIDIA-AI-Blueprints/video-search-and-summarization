// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import {
  agentAdapterConfigured,
  getAgentAdapterService,
  observeRunEvents,
} from "./agentAdapter";
import {
  ContractError,
  createRunEvent,
  parseCreateRunRequest,
  runEventPayload,
} from "./agentAdapter/contract";
import type { AgentAdapterService } from "./agentAdapter/service";
import {
  EventsExpiredError,
  IdempotencyConflictError,
  StoreCapacityError,
  ThreadBusyError,
} from "./agentAdapter/store";
import type { NextApiRequest, NextApiResponse } from "next";

const MAX_SSE_BUFFER_LENGTH = 5_000_000;
const MAX_ARTIFACT_LENGTH = 1_000_000;
const AGENT_PROTOCOL_MAJOR = "1";
const ARTIFACT_OPEN = "<vss-ui-artifact>";
const ARTIFACT_CLOSE = "</vss-ui-artifact>";
const INCIDENTS_OPEN = "<incidents>";
const INCIDENTS_CLOSE = "</incidents>";
const INTERMEDIATE_OPEN = "<intermediatestep>";
const INTERMEDIATE_CLOSE = "</intermediatestep>";

const escapeTagOpen = (value: string): string =>
  value.replaceAll("<", String.raw`\u003c`);

type JsonObject = Record<string, unknown>;

export interface AgentEvent {
  protocol_version: string;
  id: string;
  type: string;
  run_id: string;
  thread_id: string;
  data: JsonObject;
}

interface ChatMessage {
  role: "system" | "developer" | "user" | "assistant";
  content: string;
}

class AgentProtocolError extends Error {}

export interface LegacyEventState {
  toolArguments: Map<string, string>;
  reasoning: string;
}

export const createLegacyEventState = (): LegacyEventState => ({
  toolArguments: new Map<string, string>(),
  reasoning: "",
});

const asString = (value: unknown): string | undefined =>
  typeof value === "string" ? value : undefined;

const serializedPayload = (value: unknown): string => {
  if (typeof value === "string") return value;
  if (value === undefined || value === null) return "";
  try {
    return JSON.stringify(value) ?? "";
  } catch {
    return "[unserializable payload]";
  }
};

const intermediateChunk = (
  event: AgentEvent,
  options: { id: string; name: string; status: string; payload: unknown }
): string => {
  const intermediate = {
    id: options.id,
    status: options.status,
    type: "system_intermediate",
    parent_id: "default",
    content: {
      name: options.name,
      payload: serializedPayload(options.payload),
    },
    time_stamp: new Date().toISOString(),
    index: Number.parseInt(event.id, 10) || 0,
  };
  // A backend string must not be able to terminate the wrapper tag consumed by the legacy renderer.
  const json = escapeTagOpen(JSON.stringify(intermediate));
  return `<intermediatestep>${json}</intermediatestep>`;
};

const vssUiArtifactChunk = (data: JsonObject): string | null => {
  const version = asString(data.version);
  const kind = asString(data.kind);
  const payload = data.payload;
  if (
    version !== "1.0" ||
    !kind?.startsWith("vss.") ||
    !payload ||
    typeof payload !== "object" ||
    Array.isArray(payload)
  ) {
    return null;
  }
  try {
    const json = escapeTagOpen(JSON.stringify({ version, kind, payload }));
    return `<vss-ui-artifact>${json}</vss-ui-artifact>`;
  } catch {
    return null;
  }
};

/** Remove only adapter-generated presentation markup from assistant history. */
export const sanitizeAgentHistoryContent = (content: string): string => {
  let output = "";
  let cursor = 0;
  while (cursor < content.length) {
    const opening = content.indexOf(ARTIFACT_OPEN, cursor);
    if (opening < 0) {
      output += content.slice(cursor);
      break;
    }
    const payloadStart = opening + ARTIFACT_OPEN.length;
    const closing = content.indexOf(ARTIFACT_CLOSE, payloadStart);
    if (closing < 0) {
      output += content.slice(cursor);
      break;
    }

    let candidate: JsonObject | null = null;
    if (closing - payloadStart <= MAX_ARTIFACT_LENGTH) {
      try {
        const parsed: unknown = JSON.parse(
          content.slice(payloadStart, closing).trim()
        );
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
          candidate = parsed as JsonObject;
        }
      } catch {
        // Preserve malformed agent text exactly as it was received.
      }
    }
    if (!candidate || !vssUiArtifactChunk(candidate)) {
      const envelopeEnd = closing + ARTIFACT_CLOSE.length;
      output += content.slice(cursor, envelopeEnd);
      cursor = envelopeEnd;
      continue;
    }

    output += content.slice(cursor, opening);
    cursor = closing + ARTIFACT_CLOSE.length;
    // The legacy bridge appends this card payload immediately after an alert
    // artifact. It never came from the harness and must not enter its history.
    if (
      candidate.kind === "vss.alert.incidents" &&
      content.startsWith(INCIDENTS_OPEN, cursor)
    ) {
      const incidentsEnd = content.indexOf(
        INCIDENTS_CLOSE,
        cursor + INCIDENTS_OPEN.length
      );
      if (incidentsEnd >= 0) cursor = incidentsEnd + INCIDENTS_CLOSE.length;
    }
  }

  // Run status, reasoning, and tool rows are serialized into the legacy text
  // stream for presentation only. Strip only wrappers whose JSON has the exact
  // Adapter-generated discriminator; malformed or illustrative prose survives.
  let cleaned = "";
  cursor = 0;
  while (cursor < output.length) {
    const opening = output.indexOf(INTERMEDIATE_OPEN, cursor);
    if (opening < 0) return cleaned + output.slice(cursor);
    const payloadStart = opening + INTERMEDIATE_OPEN.length;
    const closing = output.indexOf(INTERMEDIATE_CLOSE, payloadStart);
    if (closing < 0) return cleaned + output.slice(cursor);

    let generated = false;
    if (closing - payloadStart <= MAX_SSE_BUFFER_LENGTH) {
      try {
        const parsed: unknown = JSON.parse(
          output.slice(payloadStart, closing).trim()
        );
        generated =
          !!parsed &&
          typeof parsed === "object" &&
          !Array.isArray(parsed) &&
          (parsed as JsonObject).type === "system_intermediate";
      } catch {
        // Preserve malformed agent text exactly as it was received.
      }
    }
    if (!generated) {
      const envelopeEnd = closing + INTERMEDIATE_CLOSE.length;
      cleaned += output.slice(cursor, envelopeEnd);
      cursor = envelopeEnd;
      continue;
    }
    cleaned += output.slice(cursor, opening);
    cursor = closing + INTERMEDIATE_CLOSE.length;
  }
  return cleaned;
};

const alertIncidentsChunk = (data: JsonObject): string | null => {
  if (data.kind !== "vss.alert.incidents") return null;
  const payload = data.payload;
  if (!payload || typeof payload !== "object" || Array.isArray(payload))
    return null;
  const incidents = (payload as JsonObject).incidents;
  if (!Array.isArray(incidents)) return null;
  const normalized = incidents
    .slice(0, 100)
    .flatMap<JsonObject>((value): JsonObject[] => {
      if (!value || typeof value !== "object" || Array.isArray(value))
        return [];
      const incident = value as JsonObject;
      const legacyDetails = incident["Alert Details"];
      const legacyClip = incident["Clip Information"];
      const hasLegacyShape =
        Object.hasOwn(incident, "Alert Details") ||
        Object.hasOwn(incident, "Clip Information");
      if (
        hasLegacyShape &&
        (!legacyDetails ||
          typeof legacyDetails !== "object" ||
          Array.isArray(legacyDetails) ||
          !legacyClip ||
          typeof legacyClip !== "object" ||
          Array.isArray(legacyClip))
      ) {
        return [];
      }
      if (
        legacyDetails &&
        typeof legacyDetails === "object" &&
        !Array.isArray(legacyDetails) &&
        legacyClip &&
        typeof legacyClip === "object" &&
        !Array.isArray(legacyClip)
      ) {
        const details = legacyDetails as JsonObject;
        const clip = legacyClip as JsonObject;
        const metadata = clip["CV Metadata"];
        return [
          {
            "Alert Title": asString(incident["Alert Title"]) || "VSS alert",
            "Alert Details": {
              "Alert Triggered":
                asString(details["Alert Triggered"]) || "VSS alert",
              Validation:
                typeof details.Validation === "boolean"
                  ? details.Validation
                  : false,
              "Alert Description": asString(details["Alert Description"]) || "",
            },
            "Clip Information": {
              Timestamp: asString(clip.Timestamp) || "",
              Stream: asString(clip.Stream) || "",
              Alerts: asString(clip.Alerts) || "VSS alert",
              "CV Metadata":
                metadata &&
                typeof metadata === "object" &&
                !Array.isArray(metadata)
                  ? metadata
                  : {},
              snapshot_url: asString(clip.snapshot_url) || "",
              video_url: asString(clip.video_url) || "",
            },
          },
        ];
      }
      const info =
        incident.info &&
        typeof incident.info === "object" &&
        !Array.isArray(incident.info)
          ? (incident.info as JsonObject)
          : {};
      const analytics =
        incident.analyticsModule &&
        typeof incident.analyticsModule === "object" &&
        !Array.isArray(incident.analyticsModule)
          ? (incident.analyticsModule as JsonObject)
          : {};
      const description =
        asString(info.reasoning) ||
        asString(info.vlm_response) ||
        asString(analytics.description) ||
        "";
      return [
        {
          "Alert Title": asString(incident.category) || "VSS alert",
          "Alert Details": {
            "Alert Triggered": asString(incident.category) || "VSS alert",
            Validation: asString(info.verdict) === "confirmed",
            "Alert Description": description,
          },
          "Clip Information": {
            Timestamp: asString(incident.timestamp) || "",
            Stream: asString(incident.sensorId) || "",
            Alerts: asString(incident.category) || "VSS alert",
            "CV Metadata": {},
            start_time: asString(incident.timestamp) || "",
            end_time: asString(incident.end) || "",
            video_url:
              asString(info.videoSource) ||
              asString(info.media_url) ||
              asString(info.video_url) ||
              "",
          },
        },
      ];
    });
  const json = escapeTagOpen(
    JSON.stringify({
      incidents: normalized,
      total_incidents:
        typeof (payload as JsonObject).total === "number"
          ? (payload as JsonObject).total
          : normalized.length,
    })
  );
  return `<incidents>${json}</incidents>`;
};

export const agentRunStatusChunk = (
  runId: string,
  status: "in_progress" | "complete",
  payload: string,
  index = 0
): string => {
  const intermediate = {
    id: `run-status-${runId}`,
    status,
    type: "system_intermediate",
    parent_id: "default",
    content: { name: "Agent run", payload },
    time_stamp: new Date().toISOString(),
    index,
  };
  const json = escapeTagOpen(JSON.stringify(intermediate));
  return `<intermediatestep>${json}</intermediatestep>`;
};

const legacyToolStatus = (eventType: string): string => {
  if (eventType === "tool.failed") return "failed";
  if (eventType === "tool.completed") return "complete";
  if (eventType === "tool.requested") return "waiting";
  return "in_progress";
};

/**
 * Temporary presentation adapter for the existing chat renderer. The browser-facing
 * `/api/agent/*` route remains fully structured; this is removed when the renderer
 * consumes AgentEvent directly.
 */
export const agentEventToLegacyChunks = (
  event: AgentEvent,
  state: LegacyEventState
): string[] => {
  const data = event.data || {};
  if (event.type === "run.started") {
    return [
      agentRunStatusChunk(
        event.run_id,
        "in_progress",
        "Waiting for the agent backend...",
        Number.parseInt(event.id, 10) || 0
      ),
    ];
  }
  if (event.type === "run.completed") {
    return [
      agentRunStatusChunk(
        event.run_id,
        "complete",
        "Agent run completed.",
        Number.parseInt(event.id, 10) || 0
      ),
    ];
  }
  if (event.type === "message.delta") {
    const delta = asString(data.delta);
    return delta ? [delta] : [];
  }

  if (event.type === "reasoning.delta") {
    const delta = asString(data.delta) || "";
    state.reasoning += delta;
    return [
      intermediateChunk(event, {
        id: `reasoning-${event.run_id}`,
        name: "Reasoning",
        status: "in_progress",
        payload: state.reasoning,
      }),
    ];
  }

  if (event.type.startsWith("tool.")) {
    const id = asString(data.tool_call_id) || `tool-${event.id}`;
    const name = asString(data.name) || "Agent tool";
    if (event.type === "tool.arguments.delta") {
      const argumentsDelta = asString(data.delta) || "";
      state.toolArguments.set(
        id,
        (state.toolArguments.get(id) || "") + argumentsDelta
      );
    } else if (typeof data.arguments === "string") {
      state.toolArguments.set(id, data.arguments);
    }
    const status = legacyToolStatus(event.type);
    const payload =
      data.error ??
      data.output ??
      data.payload ??
      state.toolArguments.get(id) ??
      "";
    return [intermediateChunk(event, { id, name, status, payload })];
  }

  if (event.type === "artifact.created") {
    const artifact = vssUiArtifactChunk(data);
    if (!artifact) return [];
    const incidents = alertIncidentsChunk(data);
    return incidents ? [artifact, incidents] : [artifact];
  }

  if (event.type === "interaction.required") {
    return [
      intermediateChunk(event, {
        id: asString(data.interaction_id) || `interaction-${event.id}`,
        name: "Agent input required",
        status: "waiting",
        payload: data,
      }),
    ];
  }

  if (event.type === "run.failed") {
    const error = data.error;
    const message =
      error && typeof error === "object"
        ? asString((error as JsonObject).message)
        : undefined;
    return [
      `\n\n**Agent run failed:** ${
        message || "The backend could not complete this request."
      }`,
    ];
  }
  return [];
};

export class AgentSseDecoder {
  private buffer = "";

  push(chunk: string): AgentEvent[] {
    this.buffer = (this.buffer + chunk).replaceAll("\r\n", "\n");
    if (this.buffer.length > MAX_SSE_BUFFER_LENGTH) {
      throw new AgentProtocolError(
        "Agent adapter emitted an oversized SSE frame"
      );
    }
    const frames = this.buffer.split("\n\n");
    this.buffer = frames.pop() || "";
    return frames.flatMap((frame) => this.parseFrame(frame));
  }

  finish(): AgentEvent[] {
    const finalFrame = this.buffer;
    this.buffer = "";
    return finalFrame ? this.parseFrame(finalFrame) : [];
  }

  private parseFrame(frame: string): AgentEvent[] {
    const data = frame
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).replace(/^ /, ""))
      .join("\n");
    if (!data || data === "[DONE]") return [];
    let parsed: unknown;
    try {
      parsed = JSON.parse(data);
    } catch {
      throw new AgentProtocolError("Agent adapter emitted invalid SSE JSON");
    }
    if (!isAgentEvent(parsed)) {
      throw new AgentProtocolError(
        "Agent adapter emitted an invalid protocol event"
      );
    }
    return [parsed];
  }
}

export const isAgentEvent = (value: unknown): value is AgentEvent => {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<AgentEvent>;
  return (
    typeof candidate.protocol_version === "string" &&
    candidate.protocol_version.split(".")[0] === AGENT_PROTOCOL_MAJOR &&
    typeof candidate.id === "string" &&
    typeof candidate.type === "string" &&
    typeof candidate.run_id === "string" &&
    typeof candidate.thread_id === "string" &&
    !!candidate.data &&
    typeof candidate.data === "object" &&
    !Array.isArray(candidate.data)
  );
};

export const isAgentAdapterConfigured = (): boolean => agentAdapterConfigured();

const cleanMessages = (value: unknown): ChatMessage[] => {
  if (!Array.isArray(value)) return [];
  const allowedRoles = new Set(["system", "developer", "user", "assistant"]);
  return value.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const role = (item as JsonObject).role;
    const content = (item as JsonObject).content;
    if (
      typeof role !== "string" ||
      !allowedRoles.has(role) ||
      typeof content !== "string"
    )
      return [];
    return [
      {
        role: role as ChatMessage["role"],
        content:
          role === "assistant" ? sanitizeAgentHistoryContent(content) : content,
      },
    ];
  });
};

const RESERVED_CHAT_BODY_FIELDS = new Set([
  "additionalProps",
  "chatCompletionURL",
  "messages",
]);
const CUSTOM_AGENT_PARAM_KEY = /^[A-Za-z_][A-Za-z0-9_.-]{0,127}$/u;
const MAX_CUSTOM_AGENT_PARAMS = 64;
const MAX_CUSTOM_AGENT_INSTRUCTIONS_LENGTH = 16_384;

const customAgentInstructions = (body: unknown): string | undefined => {
  if (!body || typeof body !== "object" || Array.isArray(body))
    return undefined;

  const params = Object.entries(body)
    .filter(
      ([key, value]) =>
        !RESERVED_CHAT_BODY_FIELDS.has(key) &&
        CUSTOM_AGENT_PARAM_KEY.test(key) &&
        (typeof value === "string" ||
          typeof value === "boolean" ||
          (typeof value === "number" && Number.isFinite(value)))
    )
    .sort(([left], [right]) => left.localeCompare(right));

  if (!params.length) return undefined;
  if (params.length > MAX_CUSTOM_AGENT_PARAMS) {
    throw new ContractError(
      `at most ${MAX_CUSTOM_AGENT_PARAMS} custom agent parameters are allowed`
    );
  }

  const instructions = `VSS UI request parameters for this turn (JSON):\n${JSON.stringify(
    Object.fromEntries(params)
  )}`;
  if (instructions.length > MAX_CUSTOM_AGENT_INSTRUCTIONS_LENGTH) {
    throw new ContractError(
      `custom agent parameters must serialize to at most ${MAX_CUSTOM_AGENT_INSTRUCTIONS_LENGTH} characters`
    );
  }
  return instructions;
};

export const agentChatBridgeHandler = async (
  req: NextApiRequest,
  res: NextApiResponse
): Promise<void> => {
  if (req.method !== "POST") {
    res.setHeader("Allow", "POST");
    res.status(405).json({
      error: { code: "method_not_allowed", message: "Method not allowed" },
    });
    return;
  }
  let service: AgentAdapterService | null;
  try {
    service = getAgentAdapterService();
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Agent adapter is misconfigured";
    res.status(503).json({
      error: { code: "adapter_not_configured", message },
    });
    return;
  }
  if (!service) {
    res.status(503).json({
      error: {
        code: "adapter_not_configured",
        message: "Embedded agent adapter is not configured",
      },
    });
    return;
  }

  const messages = cleanMessages(req.body?.messages);
  const lastUserMessage = [...messages]
    .reverse()
    .find((message) => message.role === "user");
  if (!lastUserMessage) {
    res.status(400).json({
      error: {
        code: "invalid_request",
        message: "A user message is required",
      },
    });
    return;
  }
  const threadId =
    typeof req.headers["conversation-id"] === "string"
      ? req.headers["conversation-id"]
      : "";
  if (!threadId) {
    res.status(400).json({
      error: {
        code: "invalid_request",
        message: "Conversation-Id is required",
      },
    });
    return;
  }

  const controller = new AbortController();
  let runId: string | undefined;
  let terminal = false;
  let lastEventSequence = 0;
  const legacyState = createLegacyEventState();
  const onDisconnect = (): void => {
    if (!terminal && !res.writableEnded) {
      controller.abort();
      if (runId) void service.cancelRun(runId);
    }
  };
  req.once("aborted", onDisconnect);
  res.once("close", onDisconnect);

  try {
    const messageId = req.headers["user-message-id"];
    const request = parseCreateRunRequest({
      thread_id: threadId,
      input: [lastUserMessage],
      // A one-message body means the existing UI disabled client-side history.
      // Omit recovery history so the adapter can continue its saved response chain.
      history: messages.length > 1 ? messages : [],
      surface: "vss-ui",
      // Both harness protocols have closed request schemas. Carry the existing
      // UI's per-turn controls through the adapter's protocol-neutral field:
      // Responses maps it to `instructions`, while OpenClaw prepends its
      // documented instruction block to `chat.send.message`.
      instructions: customAgentInstructions(req.body),
    });
    const created = service.createRun(
      request,
      typeof messageId === "string" && messageId ? messageId : undefined
    );
    runId = created.record.runId;

    res.status(200);
    res.setHeader("Content-Type", "text/plain; charset=utf-8");
    res.setHeader("Cache-Control", "no-cache, no-transform");
    res.setHeader("X-Accel-Buffering", "no");
    res.flushHeaders?.();

    for await (const runEvent of observeRunEvents(
      created.record,
      0,
      controller.signal
    )) {
      if (!runEvent) {
        res.write(
          agentRunStatusChunk(
            runId,
            "in_progress",
            "Waiting for the agent backend..."
          )
        );
        continue;
      }
      lastEventSequence = runEvent.sequence;
      const event = runEventPayload(runEvent) as unknown as AgentEvent;
      for (const chunk of agentEventToLegacyChunks(event, legacyState)) {
        res.write(chunk);
      }
      terminal = ["run.completed", "run.failed", "run.cancelled"].includes(
        event.type
      );
    }
    if (!terminal)
      throw new Error(
        "Agent adapter event stream ended before a terminal event"
      );
    res.end();
  } catch (error) {
    if (controller.signal.aborted) return;
    if (error instanceof EventsExpiredError) {
      // Retention expiry belongs to this observer. Report it through the same
      // terminal-event adapter as a backend failure, but leave the shared run
      // and connector available to consumers that have not fallen behind.
      terminal = true;
      const message = "requested events are no longer retained";
      if (res.headersSent && runId) {
        const failure = runEventPayload(
          createRunEvent(lastEventSequence + 1, "run.failed", runId, threadId, {
            error: {
              code: "events_expired",
              message,
              retryable: false,
            },
          })
        ) as unknown as AgentEvent;
        for (const chunk of agentEventToLegacyChunks(failure, legacyState)) {
          res.write(chunk);
        }
        res.end();
      } else {
        res.status(410).json({ error: { code: "events_expired", message } });
      }
      return;
    }
    const message =
      error instanceof Error ? error.message : "Agent adapter request failed";
    if (res.headersSent) {
      res.write(`\n\n**Agent adapter error:** ${message}`);
      res.end();
    } else if (error instanceof ContractError) {
      res.status(400).json({ error: { code: "invalid_request", message } });
    } else if (error instanceof IdempotencyConflictError) {
      res
        .status(409)
        .json({ error: { code: "idempotency_key_conflict", message } });
    } else if (error instanceof ThreadBusyError) {
      res.status(409).json({
        error: {
          code: "thread_busy",
          message: "thread already has an active run",
          active_run_id: error.runId,
        },
      });
    } else if (error instanceof StoreCapacityError) {
      res.status(503).json({
        error: { code: "run_capacity_reached", message },
      });
    } else {
      res.status(502).json({ error: { code: "adapter_unavailable", message } });
    }
    if (runId && !terminal) void service.cancelRun(runId);
  } finally {
    req.off("aborted", onDisconnect);
    res.off("close", onDisconnect);
  }
};
