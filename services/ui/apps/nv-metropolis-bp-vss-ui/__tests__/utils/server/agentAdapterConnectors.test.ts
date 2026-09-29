/** @jest-environment node */

// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { AgentAdapterConfig } from "../../../utils/server/agentAdapter/config";
import { OpenClawConnector } from "../../../utils/server/agentAdapter/connectors/openClaw";
import { ResponsesConnector } from "../../../utils/server/agentAdapter/connectors/responses";
import type { WebSocketLike } from "../../../utils/server/agentAdapter/connectors/websocket";
import { parseCreateRunRequest } from "../../../utils/server/agentAdapter/contract";
import { AgentAdapterService } from "../../../utils/server/agentAdapter/service";

const config = (
  overrides: Partial<AgentAdapterConfig> = {}
): AgentAdapterConfig => ({
  backendProtocol: "responses",
  backendUrl: "http://agent.local",
  backendPath: "/v1/responses",
  backendToken: "backend-secret",
  backendModel: "agent",
  backendSessionField: "user",
  backendSessionHeader: "X-Agent-Session",
  backendHeaders: {},
  requestTimeoutMs: 5_000,
  runRetentionMs: 60_000,
  maxRuns: 100,
  maxEventsPerRun: 1_000,
  maxEventCharsPerRun: 1_000_000,
  maxThreadStateChars: 1_000_000,
  maxRetainedChars: 4_000_000,
  ...overrides,
});

const requestWithInstructions = parseCreateRunRequest({
  thread_id: "thread-1",
  input: [{ role: "user", content: "Describe a clip" }],
  instructions:
    'VSS UI request parameters for this turn (JSON):\n{"llm_reasoning":true}',
});

const sseResponse = (
  ...events: Array<[string, Record<string, unknown>]>
): Response =>
  new Response(
    events
      .map(
        ([type, payload]) =>
          `event: ${type}\ndata: ${JSON.stringify({ type, ...payload })}\n\n`
      )
      .join(""),
    { headers: { "Content-Type": "text/event-stream" } }
  );

class FakeOpenClawSocket extends EventTarget implements WebSocketLike {
  readyState = 0;
  binaryType: BinaryType = "arraybuffer";
  readonly sent: Record<string, unknown>[] = [];
  private sessionKey = "";
  private runId = "";

  constructor(private readonly imageMode?: "final" | "read", private readonly operatorResult?: Record<string, unknown>, private readonly abortAccepted = true, private readonly lateFilling = false, private readonly searchScenario?: "tool-less" | "cached-read" | "prior-prose") {
    super();
    queueMicrotask(() => {
      this.readyState = 1;
      this.dispatchEvent(new Event("open"));
      this.message({
        type: "event",
        event: "connect.challenge",
        payload: { nonce: "nonce", ts: 1 },
      });
    });
  }

  private message(payload: Record<string, unknown>): void {
    this.dispatchEvent(
      new MessageEvent("message", { data: JSON.stringify(payload) })
    );
  }

  send(data: string): void {
    const frame = JSON.parse(data) as Record<string, unknown>;
    this.sent.push(frame);
    const params = frame.params as Record<string, unknown>;
    if (frame.method === "connect") {
      queueMicrotask(() =>
        this.message({
          type: "res",
          id: frame.id,
          ok: true,
          payload: {
            type: "hello-ok",
            protocol: 4,
            auth: {
              role: "operator",
              scopes: ["operator.read", "operator.write"],
            },
            features: {
              methods: ["chat.send", "chat.abort"],
              events: ["chat", "session.tool"],
            },
          },
        })
      );
    } else if (frame.method === "chat.send") {
      this.sessionKey = String(params.sessionKey);
      this.runId = "upstream-run";
      queueMicrotask(() => {
        this.message({
          type: "res",
          id: frame.id,
          ok: true,
          payload: { runId: this.runId, status: "accepted" },
        });
        if (this.searchScenario) {
          this.message({type:"event",event:"chat",payload:{sessionKey:this.sessionKey,runId:this.runId,state:"delta",deltaText:"Earlier leak was confirmed at 65–70s; Sammy has 58 live overflow events."}});
          if (this.searchScenario === "tool-less") {
            this.message({type:"event",event:"chat",payload:{sessionKey:this.sessionKey,runId:this.runId,state:"final",message:{content:"Earlier leak was confirmed at 65–70s; Sammy has 58 live overflow events."}}});
            return;
          }
        }
        this.message({
          type: "event",
          event: "session.tool",
          payload: {
            sessionKey: this.sessionKey,
            runId: this.runId,
            data:
              this.imageMode === "read"
                ? {
                    id: "tool-1",
                    name: "tool_call",
                    phase: "started",
                    args: {
                      id: "openclaw:core:read",
                      args: {
                        path: "/sandbox/.openclaw/workspace/warehouse_safety_0002_12.5s.jpg",
                      },
                    },
                  }
                : { id: "tool-1", name: this.searchScenario === "cached-read" ? "read" : this.operatorResult ? "vss_cli" : "vss", phase: "started" },
          },
        });
        this.message({
          type: "event",
          event: "session.tool",
          payload: {
            sessionKey: this.sessionKey,
            runId: this.runId,
            data: {
              id: "tool-1",
              name: this.searchScenario === "cached-read" ? "read" : this.operatorResult ? "vss_cli" : "vss",
              phase: "completed",
              result:
                this.imageMode === "read"
                  ? '{"result":{"content":[]}}[... 100 more characters truncated; rerun with narrower args if needed]'
                  : this.operatorResult || { ok: true },
            },
          },
        });
        if (this.lateFilling) {
          this.message({type: "event", event: "session.tool", payload: {
            sessionKey: this.sessionKey, runId: this.runId,
            data: {id: "unrelated-filling", name: "vss_cli", phase: "started", args: {args: ["filling", "live", "status"]}},
          }});
        }
        this.message({
          type: "event",
          event: "chat",
          payload: {
            sessionKey: this.sessionKey,
            runId: this.runId,
            state: "delta",
            deltaText: "Found it",
          },
        });
        this.message({
          type: "event",
          event: "chat",
          payload: {
            sessionKey: this.sessionKey,
            runId: this.runId,
            state: "final",
            ...(this.imageMode === "final"
              ? {
                  message: {
                    content: [
                      { type: "text", text: "Found it" },
                      {
                        type: "image",
                        url: `/api/chat/media/outgoing/${encodeURIComponent(
                          this.sessionKey
                        )}/797be77f-b6ce-47bd-b4ef-a95fdc2586d9/full`,
                        alt: "warehouse_safety_0002_12.5s---939218c3-85e1-4221-9fcd-ba731cadfa93.jpg",
                        mimeType: "image/jpeg",
                      },
                    ],
                  },
                }
              : {}),
          },
        });
      });
    } else if (frame.method === "chat.abort") {
      queueMicrotask(() => this.message({type: "res", id: frame.id, ok: this.abortAccepted,
        ...(this.abortAccepted ? {payload: {ok: true, aborted: true, runIds: [this.runId]}} : {error: {message: "Abort rejected"}})}));
    }
  }

  close(): void {
    this.readyState = 3;
    this.dispatchEvent(new Event("close"));
  }
}

describe("embedded adapter connectors", () => {
  const originalFetch = global.fetch;

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it("normalizes Responses text and tool events while keeping credentials server-side", async () => {
    global.fetch = jest.fn().mockResolvedValue(
      sseResponse(
        [
          "response.output_item.added",
          {
            response: { id: "resp_1" },
            item: {
              type: "function_call",
              id: "item-1",
              call_id: "call-1",
              name: "search",
            },
          },
        ],
        [
          "response.function_call_arguments.delta",
          { item_id: "item-1", delta: '{"q":"fire"}' },
        ],
        [
          "response.output_item.done",
          {
            item: {
              type: "function_call",
              id: "item-1",
              call_id: "call-1",
              name: "search",
              status: "completed",
              arguments: '{"q":"fire"}',
            },
          },
        ],
        ["response.output_text.delta", { delta: "Found it" }],
        ["response.completed", { response: { id: "resp_1" } }]
      )
    ) as jest.Mock;

    const connector = new ResponsesConnector(config());
    const events = [];
    for await (const event of connector.run(
      requestWithInstructions,
      "run-1",
      new AbortController().signal
    )) {
      events.push(event);
    }

    expect(events.map((event) => event.type)).toEqual([
      "tool.started",
      "tool.arguments.delta",
      "tool.completed",
      "message.delta",
    ]);
    const [, options] = (global.fetch as jest.Mock).mock.calls[0];
    expect((options.headers as Headers).get("Authorization")).toBe(
      "Bearer backend-secret"
    );
    expect((options.headers as Headers).get("X-Agent-Session")).toMatch(
      /^vss-ui:/
    );
    expect(JSON.parse(options.body as string)).toEqual(
      expect.objectContaining({
        model: "agent",
        stream: true,
        store: true,
        tools: [
          expect.objectContaining({
            name: "vss_ui_publish_artifact",
            parameters: expect.objectContaining({
              properties: expect.objectContaining({
                kind: expect.objectContaining({
                  enum: expect.arrayContaining(["vss.media.image"]),
                }),
              }),
            }),
          }),
        ],
        instructions: expect.stringContaining(
          'VSS UI request parameters for this turn (JSON):\n{"llm_reasoning":true}'
        ),
      })
    );
  });

  it("extracts snapshot artifacts from legacy agent tool payloads", async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(
        `intermediate_data: ${JSON.stringify({
          id: "snapshot-1",
          name: "snapshot",
          status: "completed",
          payload: {
            kind: "snapshot",
            media_url: "http://vios:30888/storage/temp/snapshot.jpg",
            name: "warehouse_safety_0001",
            at: "2026-09-15T00:00:05Z",
          },
        })}\ndata: [DONE]\n`,
        { headers: { "Content-Type": "text/event-stream" } }
      )
    );
    const service = new AgentAdapterService(
      config({
        backendProtocol: "legacy-chat",
        backendPath: "/chat/completions",
      })
    );
    const { record } = service.createRun(requestWithInstructions);

    for (let attempt = 0; attempt < 20 && !record.terminal; attempt += 1) {
      await new Promise<void>((resolve) => setImmediate(resolve));
    }

    expect(record.terminal).toBe(true);
    expect(record.eventsAfter(0)).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          type: "artifact.created",
          data: expect.objectContaining({
            kind: "vss.media.image",
            payload: expect.objectContaining({
              media_url: "/vst/storage/temp/snapshot.jpg",
              sensor: "warehouse_safety_0001",
            }),
          }),
        }),
      ])
    );
    expect(service.capabilities()).toEqual(
      expect.objectContaining({
        artifact_protocol: expect.objectContaining({
          kinds: expect.arrayContaining(["vss.media.image"]),
        }),
      })
    );
  });

  it("uses native OpenClaw chat and tool events with narrow requested scopes", async () => {
    const socket = new FakeOpenClawSocket();
    const connector = new OpenClawConnector(
      config({
        backendProtocol: "openclaw-ws",
        backendUrl: "ws://agent.local",
        backendPath: "/",
        backendSessionField: undefined,
        backendSessionHeader: undefined,
      }),
      () => socket
    );
    const events = [];
    for await (const event of connector.run(
      requestWithInstructions,
      "run-1",
      new AbortController().signal
    )) {
      events.push(event);
    }

    expect(events.map((event) => event.type)).toEqual([
      "tool.started",
      "tool.completed",
      "message.delta",
    ]);
    expect(events[1].data._artifact_source).toEqual({ ok: true });
    const connect = socket.sent.find((frame) => frame.method === "connect");
    expect((connect?.params as Record<string, unknown>).scopes).toEqual([
      "operator.read",
      "operator.write",
    ]);
    const send = socket.sent.find((frame) => frame.method === "chat.send");
    expect((send?.params as Record<string, unknown>).message).toBe(
      'VSS UI instructions:\nVSS UI request parameters for this turn (JSON):\n{"llm_reasoning":true}\n\nUser:\nDescribe a clip'
    );
  });

  it("materializes final OpenClaw managed images as private artifact sources", async () => {
    global.fetch = jest
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ available: true, mediaTicket: "media-ticket" }),
          { headers: { "Content-Type": "application/json" } }
        )
      )
      .mockResolvedValueOnce(
        new Response(Uint8Array.from([0xff, 0xd8, 0xff]), {
          headers: {
            "Content-Type": "image/jpeg",
            "Content-Length": "3",
          },
        })
      ) as jest.Mock;
    const connector = new OpenClawConnector(
      config({
        backendProtocol: "openclaw-ws",
        backendUrl: "ws://agent.local/gateway",
        backendPath: "/",
        backendSessionField: undefined,
        backendSessionHeader: undefined,
      }),
      () => new FakeOpenClawSocket("final")
    );

    const events = [];
    for await (const event of connector.run(
      requestWithInstructions,
      "run-1",
      new AbortController().signal
    )) {
      events.push(event);
    }

    expect(events.map((event) => event.type)).toEqual([
      "tool.started",
      "tool.completed",
      "message.delta",
      "artifact.source",
    ]);
    expect(events[3].data.source).toEqual({
      type: "image",
      data: "/9j/",
      mimeType: "image/jpeg",
      alt: "warehouse_safety_0002_12.5s---939218c3-85e1-4221-9fcd-ba731cadfa93.jpg",
    });
    const metadataUrl = new URL(
      (global.fetch as jest.Mock).mock.calls[0][0] as URL
    );
    expect(metadataUrl.pathname).toBe(
      "/gateway/__openclaw__/assistant-media"
    );
    expect(metadataUrl.searchParams.get("source")).toBe(
      "/sandbox/.openclaw/workspace/warehouse_safety_0002_12.5s.jpg"
    );
    expect(
      ((global.fetch as jest.Mock).mock.calls[0][1].headers as Headers).get(
        "Authorization"
      )
    ).toBe("Bearer backend-secret");
  });

  it("recovers image reads when OpenClaw truncates the tool result", async () => {
    global.fetch = jest
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ available: true, mediaTicket: "media-ticket" }),
          { headers: { "Content-Type": "application/json" } }
        )
      )
      .mockResolvedValueOnce(
        new Response(Uint8Array.from([0xff, 0xd8, 0xff]), {
          headers: {
            "Content-Type": "image/jpeg",
            "Content-Length": "3",
          },
        })
      ) as jest.Mock;
    const connector = new OpenClawConnector(
      config({
        backendProtocol: "openclaw-ws",
        backendUrl: "ws://agent.local",
        backendPath: "/",
        backendSessionField: undefined,
        backendSessionHeader: undefined,
      }),
      () => new FakeOpenClawSocket("read")
    );

    const events = [];
    for await (const event of connector.run(
      requestWithInstructions,
      "run-1",
      new AbortController().signal
    )) {
      events.push(event);
    }

    expect(events.map((event) => event.type)).toEqual([
      "tool.started",
      "tool.completed",
      "artifact.source",
      "message.delta",
    ]);
    expect(events[2].data.source).toEqual({
      type: "image",
      data: "/9j/",
      mimeType: "image/jpeg",
      alt: "VSS snapshot",
    });
  });

  it("cancels managed image recovery with the agent run", async () => {
    const controller = new AbortController();
    global.fetch = jest.fn((_url, options) => {
      const signal = (options as RequestInit).signal as AbortSignal;
      queueMicrotask(() => controller.abort());
      return new Promise<Response>((_resolve, reject) => {
        signal.addEventListener("abort", () => reject(signal.reason), {
          once: true,
        });
      });
    }) as jest.Mock;
    const connector = new OpenClawConnector(
      config({
        backendProtocol: "openclaw-ws",
        backendUrl: "ws://agent.local",
        backendPath: "/",
        backendSessionField: undefined,
        backendSessionHeader: undefined,
        requestTimeoutMs: 900_000,
      }),
      () => new FakeOpenClawSocket("final")
    );

    const events = [];
    for await (const event of connector.run(
      requestWithInstructions,
      "run-1",
      controller.signal
    )) {
      events.push(event);
    }

    expect(controller.signal.aborted).toBe(true);
    expect(events.map((event) => event.type)).toEqual([
      "tool.started",
      "tool.completed",
      "message.delta",
    ]);
    expect(global.fetch).toHaveBeenCalledTimes(1);
  });

  it("continues ordinary OpenClaw follow-ups in the same session", async () => {
    const sockets: FakeOpenClawSocket[] = [];
    const connector = new OpenClawConnector(
      config({
        backendProtocol: "openclaw-ws",
        backendUrl: "ws://agent.local",
        backendPath: "/",
        backendSessionField: undefined,
        backendSessionHeader: undefined,
      }),
      () => {
        const socket = new FakeOpenClawSocket();
        sockets.push(socket);
        return socket;
      }
    );
    const followUp = parseCreateRunRequest({
      thread_id: "thread-1",
      input: [{ role: "user", content: "Use the base profile" }],
    });

    for await (const _event of connector.run(
      requestWithInstructions,
      "run-1",
      new AbortController().signal
    )) {
      // Drain the first completed turn.
    }
    for await (const _event of connector.run(
      followUp,
      "run-2",
      new AbortController().signal
    )) {
      // Drain the normal follow-up turn.
    }

    const sessionKeys = sockets.map((socket) => {
      const send = socket.sent.find((frame) => frame.method === "chat.send");
      return (send?.params as Record<string, unknown>).sessionKey;
    });
    expect(sessionKeys).toHaveLength(2);
    expect(sessionKeys[1]).toBe(sessionKeys[0]);
  });
});


describe('native authoritative filling presentation', () => {
  const context = {mode:'live',scope:'selected_live_session',session_id:'s',stream_id:'camera',selected_bottles:[{track_id:'s:epoch-1:cycle-1'}]};
  const measured = {mode:'live',view:'operator',render_policy:'authoritative_measurement',session_id:'s',stream_id:'camera',query_status:'ok',selected_track_ids:['s:epoch-1:cycle-1'],matches:[{event_id:'e',track_id:'s:epoch-1:cycle-1'}],snapshot_evidences:[],display_markdown:'Bottle cycle-1: normal. Final height73%.'};
  const request = {...requestWithInstructions,input:[{role:'user' as const,content:`[Context: ${JSON.stringify([context])}]\n\nWhat happened with this bottle?`}]};
  function connector(result: Record<string, unknown>) {return new OpenClawConnector(config({backendProtocol:'openclaw-ws',backendUrl:'ws://agent.local',backendPath:'/',backendSessionField:undefined,backendSessionHeader:undefined}),()=>new FakeOpenClawSocket(undefined,result));}
  it('returns actual authoritative facts rather than the later model paraphrase', async () => {
    const events=[];
    for await (const event of connector(measured).run(request,'run-1',new AbortController().signal)) events.push(event);
    expect(events.filter(event=>event.type==='message.delta').map(event=>event.data.delta)).toEqual([measured.display_markdown]);
    expect(events.some(event=>event.type==='tool.completed')).toBe(true);
  });
  it('keeps ordinary fallback only for general live status questions', async () => {
    const general = {...request,input:[{role:'user' as const,content:`[Context: ${JSON.stringify([{...context,selected_bottles:[]}])}]\n\nWhat is the live session status?`}]};
    const events=[];
    for await (const event of connector({ok:true}).run(general,'run-1',new AbortController().signal)) events.push(event);
    expect(events.filter(event=>event.type==='message.delta').map(event=>event.data.delta)).toEqual(['Found it']);
  });
  it.each([
    ['What happened with this bottle?', [], 'Select exactly one bottle'],
    ['Compare these two bottles.', [], 'Select exactly two bottles'],
    ['Compare these two bottles.', context.selected_bottles, 'Select exactly two bottles'],
  ])('clarifies an unbound inspection even when a status tool returns a live current bottle: %s', async (question, selections, expected) => {
    const unbound = {...request,input:[{role:'user' as const,content:`[Context: ${JSON.stringify([{...context,selected_bottles:selections}])}]\n\n${question}`}]};
    const events=[];
    for await (const event of connector({status:'running',current:{track_id:'cycle-86'}}).run(unbound,'run-1',new AbortController().signal)) events.push(event);
    const texts=events.filter(event=>event.type==='message.delta').map(event=>event.data.delta);
    expect(texts).toHaveLength(1); expect(texts[0]).toContain(expected); expect(texts[0]).not.toContain('cycle-86'); expect(texts[0]).not.toContain('Found it');
  });
  it.each(['What happened with this bottle?', 'What happened with bottle cycle-99?'])('fails closed when a bound inspection has no validated operator result: %s', async (question) => {
    const bound={...request,input:[{role:'user' as const,content:`[Context: ${JSON.stringify([context])}]\n\n${question}`}]};
    const events=[];
    for await (const event of connector({status:'running',current:{track_id:'cycle-86'}}).run(bound,'run-1',new AbortController().signal)) events.push(event);
    expect(events.filter(event=>event.type==='message.delta').map(event=>event.data.delta)).toEqual(['No verified inspection result was returned for the requested bottle or bottles. Please try again.']);
  });
  it('fails a wrong-source measurement instead of painting its text or artifacts', async () => {
    await expect((async()=>{for await (const _event of connector({...measured,stream_id:'other'}).run(request,'run-1',new AbortController().signal)) { /* exhaust */ }})()).rejects.toThrow('source or contract');
  });
  it.each(['final', 'read'] as const)('does not attach unbound %s images to a Filling inspection', async (imageMode) => {
    const originalFetch = global.fetch;
    const fetchMock = jest.fn().mockResolvedValueOnce(new Response(JSON.stringify({available:true,mediaTicket:'ticket'}),{headers:{'Content-Type':'application/json'}})).mockResolvedValueOnce(new Response(Uint8Array.from([0xff,0xd8,0xff]),{headers:{'Content-Type':'image/jpeg','Content-Length':'3'}}));
    global.fetch = fetchMock;
    try {
      const scoped = new OpenClawConnector(config({backendProtocol:'openclaw-ws',backendUrl:'ws://agent.local',backendPath:'/',backendSessionField:undefined,backendSessionHeader:undefined}),()=>new FakeOpenClawSocket(imageMode,measured));
      const events = [];
      for await (const event of scoped.run(request,'run-1',new AbortController().signal)) events.push(event);
      expect(events.some(event=>event.type==='artifact.source')).toBe(false);
      expect(fetchMock).not.toHaveBeenCalled();
      const completed = events.find(event=>event.type==='tool.completed');
      if (imageMode === 'final') expect(completed?.data._artifact_source).toEqual(measured);
      else expect(completed?.data._artifact_source).toBeUndefined();
    } finally {global.fetch = originalFetch;}
  });

});


describe('standalone archive Search completion', () => {
  const payload = {data: [{sensor_id: 'real-recording', start_offset: 60, end_offset: 70}], search_messages: [], job_id: 'search-observed'};
  const receipt = {event: 'vss_job_completed', group: 'search', job_id: payload.job_id, status: 'completed', exit_hint: 0};
  const cli = (data = payload.data) => ({command: '/usr/local/bin/vss search run embed --query leaking --top-k 3', exitCode: 0, signal: null, timedOut: false, truncated: false, stdout: `${JSON.stringify({...payload, data})}\n${JSON.stringify(receipt)}\n`});
  function setup(question: string, result: Record<string, unknown>, abortAccepted = true) {
    const socket = new FakeOpenClawSocket(undefined, result, abortAccepted, true);
    const connector = new OpenClawConnector(config({backendProtocol:'openclaw-ws',backendUrl:'ws://agent.local',backendPath:'/',backendSessionField:undefined,backendSessionHeader:undefined}), () => socket);
    const request = parseCreateRunRequest({thread_id:'archive-thread', input:[{role:'user', content:question}]});
    return {socket, connector, request};
  }
  it.each([['Find liquid leaking from bottles', cli(), '1 clip candidate'], ['liquid leaking from bottle', cli([]), 'no clip candidates']] as const)('ends %s at the real Search result and stops only that upstream run', async (question, result, expected) => {
    const {socket, connector, request} = setup(question, result);
    const events=[];
    for await (const event of connector.run(request, 'archive-run', new AbortController().signal)) events.push(event);
    expect(events.map(event => event.type)).toEqual(['tool.started', 'tool.completed', 'message.delta']);
    expect(events.at(-1)?.data.delta).toContain(expected);
    expect(events.some(event => event.data.delta === 'Found it')).toBe(false);
    expect(events.some(event => event.data.tool_call_id === 'unrelated-filling')).toBe(false);
    expect(events.find(event => event.type === 'tool.completed')?.data._artifact_source).toEqual(result);
    const sent=socket.sent.find(frame => frame.method === 'chat.send');
    const aborted=socket.sent.find(frame => frame.method === 'chat.abort');
    expect(aborted?.params).toEqual({sessionKey: (sent?.params as Record<string,unknown>).sessionKey, runId:'upstream-run'});
    expect(socket.sent.filter(frame => frame.method === 'chat.abort')).toHaveLength(1);
  });
  it.each([
    'Find spilling clips and compare measured bottle fill levels.',
    '[Context: [{"mode":"live","scope":"selected_live_session","session_id":"s","stream_id":"camera","selected_bottles":[]}]]\n\nWhat is the live session status?',
  ])('does not cut an explicit inspection or mixed request: %s', async question => {
    const {socket, connector, request} = setup(question, cli());
    const events=[];
    for await (const event of connector.run(request, 'archive-run', new AbortController().signal)) events.push(event);
    expect(socket.sent.some(frame => frame.method === 'chat.abort')).toBe(false);
    expect(events.some(event => event.data.delta === 'Found it')).toBe(true);
  });
  it('leaves inventory questions without a SearchOutput on the ordinary route', async () => {
    const {socket, connector, request} = setup('What videos are available?', {command:'/usr/local/bin/vss vios list',exitCode:0,stdout:'{"sensors":[]}'});
    const events=[];
    for await (const event of connector.run(request,'inventory-run',new AbortController().signal)) events.push(event);
    expect(socket.sent.some(frame => frame.method === 'chat.abort')).toBe(false);
    expect(events.some(event => event.data.delta === 'Found it')).toBe(true);
  });
  it('fails closed if the upstream abort is rejected instead of claiming the turn was stopped', async () => {
    const {connector, request} = setup('Find liquid leaking from bottles', cli(), false);
    await expect((async()=>{for await (const _event of connector.run(request,'archive-run',new AbortController().signal)) { /* exhaust */ }})()).rejects.toThrow('agent continuation could not be stopped');
  });
});


describe('fresh Search cannot answer from contaminated native history', () => {
  const payload = {data:[{sensor_id:'current-source',start_offset:11,end_offset:17,verification:{result:'rejected',criteria_met:{leaking:false}}}],search_messages:[],job_id:'new-job'};
  const receipt = {event:'vss_job_completed',group:'search',job_id:payload.job_id,status:'completed',exit_hint:0};
  const result = {command:'/usr/local/bin/vss search run embed --query liquid leaking from bottles',exitCode:0,stdout:`${JSON.stringify(payload)}\n${JSON.stringify(receipt)}\n`};
  function setup(question: string, scenario: 'tool-less'|'cached-read'|'prior-prose', output = result) {
    const socket = new FakeOpenClawSocket(undefined,output,true,false,scenario);
    const connector = new OpenClawConnector(config({backendProtocol:'openclaw-ws',backendUrl:'ws://agent.local',backendPath:'/',backendSessionField:undefined,backendSessionHeader:undefined}),()=>socket);
    const request = parseCreateRunRequest({thread_id:'existing-contaminated-thread',input:[{role:'user',content:question}],instructions:'Existing UI settings remain.'});
    return {socket,connector,request};
  }
  it.each(['Find liquid leaking from bottles','Show me liquid leaking from bottles','Rerun that search'])('fails instead of forwarding remembered claims without a current tool call: %s', async question => {
    const {socket,connector,request}=setup(question,'tool-less');const events=[];
    await expect((async()=>{for await(const e of connector.run(request,'fresh-run',new AbortController().signal))events.push(e);})()).rejects.toMatchObject({code:'fresh_search_required'});
    expect(events).toEqual([]);
    expect(socket.sent.filter(f=>f.method==='chat.send')).toHaveLength(1);
    const message=String((socket.sent.find(f=>f.method==='chat.send')!.params as Record<string,unknown>).message);
    expect(message).toContain('Existing UI settings remain.');expect(message).toContain('Execute an actual vss_cli search run during this turn');expect(message).toContain(`User:\n${question}`);expect(message).toContain('--no-merge-adjacent');expect(message).toContain('Preserve an explicit user request for merged results');
    expect(request.input[0].content).toBe(question);expect(request.instructions).toBe('Existing UI settings remain.');
  });
  it('ignores a cached read that contains an otherwise valid old Search receipt',async()=>{
    const {connector,request}=setup('Show me liquid leaking from bottles','cached-read');const events=[];
    await expect((async()=>{for await(const e of connector.run(request,'read-run',new AbortController().signal))events.push(e);})()).rejects.toMatchObject({code:'fresh_search_required'});
    expect(events.some(e=>e.type==='message.delta'||e.type==='artifact.source'||e.data._artifact_source)).toBe(false);
  });
  it('suppresses stale streamed prose but keeps real current tool output and current verdict',async()=>{
    const {connector,request,socket}=setup('Find liquid leaking from bottles','prior-prose');const events=[];
    for await(const e of connector.run(request,'current-run',new AbortController().signal))events.push(e);
    expect(events.filter(e=>e.type==='message.delta').map(e=>e.data.delta)).toEqual(['Search returned 1 clip candidate. Review the clips and their verification verdicts in the Search tab.']);
    expect(events.find(e=>e.type==='tool.completed')?.data._artifact_source).toEqual(result);
    expect(socket.sent.filter(f=>f.method==='chat.abort')).toHaveLength(1);
  });
  it.each(['What videos are available?','Why was that rejected?','What did the previous search find?','Show me previous search results','What happened with bottle cycle-305?','Find leaks and then generate a report'])('leaves ordinary follow-ups and mixed requests on the existing native path: %s',async question=>{
    const {connector,request,socket}=setup(question,'tool-less');const events=[];
    for await(const e of connector.run(request,'ordinary-run',new AbortController().signal))events.push(e);
    expect(events.some(e=>e.type==='message.delta')).toBe(true);
    expect(String((socket.sent.find(f=>f.method==='chat.send')!.params as Record<string,unknown>).message)).not.toContain('This turn requests a NEW archive visual search');
  });
  it('keeps the same native session across repeated Search requests in one thread',async()=>{
    const one=setup('Find liquid leaking from bottles','prior-prose');
    for await(const _e of one.connector.run(one.request,'run-one',new AbortController().signal)){};
    const two=setup('Show me liquid leaking from bottles','prior-prose');
    for await(const _e of two.connector.run(two.request,'run-two',new AbortController().signal)){};
    const sent=(socket:FakeOpenClawSocket)=>socket.sent.find(f=>f.method==='chat.send')!.params as Record<string,unknown>;
    expect(sent(one.socket).sessionKey).toBe(sent(two.socket).sessionKey);expect(sent(one.socket).idempotencyKey).not.toBe(sent(two.socket).idempotencyKey);
  });
});
