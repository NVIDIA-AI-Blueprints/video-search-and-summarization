// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import {
  ARTIFACT_CLOSE,
  ARTIFACT_OPEN,
  ArtifactStreamParser,
  stripArtifactsFromValue,
} from "../../../utils/server/agentAdapter/artifacts";
import { loadAgentAdapterConfig } from "../../../utils/server/agentAdapter/config";
import {
  fullTranscript,
  parseCreateRunRequest,
} from "../../../utils/server/agentAdapter/contract";
import { strictJsonParse } from "../../../utils/server/agentAdapter/json";
import {
  EventsExpiredError,
  IdempotencyConflictError,
  RunNotFoundError,
  RunStore,
  StoreCapacityError,
  ThreadBusyError,
} from "../../../utils/server/agentAdapter/store";

const request = (threadId = "thread-1") =>
  parseCreateRunRequest({
    thread_id: threadId,
    input: [{ role: "user", content: "hello" }],
    history: [{ role: "assistant", content: "previous" }],
  });

describe("embedded agent adapter", () => {
  it("strictly parses JSON and rejects duplicate keys", () => {
    expect(strictJsonParse('{"a":1,"nested":{"b":2}}')).toEqual({
      a: 1,
      nested: { b: 2 },
    });
    expect(() => strictJsonParse('{"a":1,"a":2}')).toThrow(
      "invalid strict JSON"
    );
    expect(() => strictJsonParse('{"a":NaN}')).toThrow("invalid strict JSON");
    const special = strictJsonParse(
      '{"__proto__":{"polluted":true}}'
    ) as Record<string, unknown>;
    expect(Object.hasOwn(special, "__proto__")).toBe(true);
    expect(({} as { polluted?: boolean }).polluted).toBeUndefined();
  });

  it("validates run input and avoids duplicating the current turn", () => {
    const parsed = parseCreateRunRequest({
      thread_id: "thread-1",
      input: [{ role: "user", content: "new" }],
      history: [
        { role: "assistant", content: "old" },
        { role: "user", content: "new" },
      ],
    });
    expect(fullTranscript(parsed)).toEqual([
      { role: "assistant", content: "old" },
      { role: "user", content: "new" },
    ]);
    expect(() =>
      parseCreateRunRequest({
        thread_id: "thread-1",
        input: [{ role: "tool", content: "unsafe" }],
      })
    ).toThrow("input[0].role");
  });

  it("extracts fragmented artifacts and never hides malformed envelopes", () => {
    const parser = new ArtifactStreamParser();
    const encoded = JSON.stringify({
      version: "1.0",
      kind: "vss.search.results",
      payload: { data: [] },
    });
    expect(
      parser.feed(`answer${ARTIFACT_OPEN}${encoded.slice(0, 10)}`)
    ).toEqual([{ type: "message.delta", data: { delta: "answer" } }]);
    expect(parser.feed(`${encoded.slice(10)}${ARTIFACT_CLOSE}`)).toEqual([
      expect.objectContaining({
        type: "artifact.created",
        data: expect.objectContaining({ kind: "vss.search.results" }),
      }),
    ]);

    const malformed = `${ARTIFACT_OPEN}{bad}${ARTIFACT_CLOSE}`;
    expect(new ArtifactStreamParser().feed(malformed)).toEqual([
      { type: "message.delta", data: { delta: malformed } },
    ]);
  });

  it("derives a search artifact only from a matching successful CLI completion", () => {
    const parser = new ArtifactStreamParser();
    const result = JSON.stringify({
      job_id: "job-1",
      data: [{ video_name: "clip.mp4" }],
      search_messages: [],
    });
    const completion = JSON.stringify({
      event: "vss_job_completed",
      group: "search",
      status: "completed",
      exit_hint: 0,
      job_id: "job-1",
    });
    expect(parser.inspectComplete(`${result}\n${completion}`)).toEqual([
      expect.objectContaining({
        type: "artifact.created",
        data: expect.objectContaining({ kind: "vss.search.results" }),
      }),
    ]);
  });

  it("removes artifact envelopes from nested private tool output", () => {
    const artifact = `${ARTIFACT_OPEN}${JSON.stringify({
      version: "1.0",
      kind: "vss.alert.incidents",
      payload: { incidents: [] },
    })}${ARTIFACT_CLOSE}`;
    expect(stripArtifactsFromValue({ output: `done${artifact}` })).toEqual({
      output: "done",
    });
  });

  it("enforces idempotency, one active run per thread, and bounded replay", () => {
    const store = new RunStore(60_000, 2, 2, 1_000_000, 4_000_000);
    const first = store.create(request(), "same-key");
    expect(store.create(request(), "same-key")).toEqual({
      record: first.record,
      replayed: true,
    });
    expect(() => store.create(request())).toThrow(ThreadBusyError);
    expect(() =>
      store.create(
        parseCreateRunRequest({
          thread_id: "thread-2",
          input: [{ role: "user", content: "different" }],
        }),
        "same-key"
      )
    ).toThrow(IdempotencyConflictError);

    first.record.append("message.delta", { delta: "one" });
    first.record.append("message.delta", { delta: "two" });
    first.record.append("message.delta", { delta: "three" });
    expect(() => first.record.eventsAfter(0)).toThrow(EventsExpiredError);
  });

  it("evicts the oldest terminal run to enforce the retained character budget", () => {
    const store = new RunStore(60_000, 10, 100, 500, 900);
    const first = store.create(request("thread-1")).record;
    store.finish(first, "run.completed");

    const second = store.create(request("thread-2")).record;

    expect(() => store.get(first.runId)).toThrow(RunNotFoundError);
    expect(store.get(second.runId)).toBe(second);
  });

  it("rejects a new run when active reservations fill the character budget", () => {
    const store = new RunStore(60_000, 10, 100, 500, 900);
    const active = store.create(request("thread-1")).record;

    expect(() => store.create(request("thread-2"))).toThrow(StoreCapacityError);
    expect(store.get(active.runId)).toBe(active);
  });

  it("rejects reserved and unsafe upstream headers", () => {
    expect(() =>
      loadAgentAdapterConfig({
        AGENT_BACKEND_URL: "http://backend",
        AGENT_BACKEND_HEADERS_JSON: '{"Authorization":"secret"}',
      })
    ).toThrow("cannot override Authorization");
    expect(() =>
      loadAgentAdapterConfig({
        AGENT_BACKEND_URL: "http://backend",
        AGENT_BACKEND_HEADERS_JSON: '{"X-Test":"bad\\nvalue"}',
      })
    ).toThrow("invalid HTTP header value");
    expect(() =>
      loadAgentAdapterConfig({
        AGENT_BACKEND_PROTOCOL: "openclaw-ws",
        AGENT_BACKEND_URL: "ws://backend",
        AGENT_BACKEND_HEADERS_JSON: '{"X-Route":"one"}',
      })
    ).toThrow("unsupported with openclaw-ws");
  });

  it("uses the explicit adapter switch for profile deployments", () => {
    expect(
      loadAgentAdapterConfig({
        AGENT_ADAPTER_ENABLED: "false",
        AGENT_BACKEND_URL: "http://backend",
      })
    ).toBeNull();
    expect(() =>
      loadAgentAdapterConfig({ AGENT_ADAPTER_ENABLED: "true" })
    ).toThrow("AGENT_BACKEND_URL is required when AGENT_ADAPTER_ENABLED=true");
  });

  it("requires the global retention budget to cover event and thread limits", () => {
    const config = loadAgentAdapterConfig({
      AGENT_BACKEND_URL: "http://backend",
    });
    expect(config?.maxRetainedChars).toBe(64_000_000);
    expect(() =>
      loadAgentAdapterConfig({
        AGENT_BACKEND_URL: "http://backend",
        AGENT_MAX_RETAINED_CHARS: "40000000",
      })
    ).toThrow(
      "AGENT_MAX_RETAINED_CHARS must exceed AGENT_MAX_THREAD_STATE_CHARS plus AGENT_MAX_EVENT_CHARS_PER_RUN"
    );
  });
});
