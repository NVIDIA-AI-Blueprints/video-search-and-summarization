// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import assert from "node:assert/strict";
import { EventEmitter, once } from "node:events";
import { chmod, mkdtemp, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, before, test } from "node:test";

import { registerTools } from "../dist/index.js";

const RELAY_ENV = ["VSS_RELAY_URL", "VSS_RELAY_RUN", "VSS_RELAY_TRIAL"];
const TEST_ENV = [...RELAY_ENV, "CHILD_GATE_URL", "UNRELATED_CREDENTIAL"];
let fixtureDir;
let fixtureBin;

before(async () => {
  fixtureDir = await mkdtemp(join(tmpdir(), "vss-relay-plugin-"));
  fixtureBin = join(fixtureDir, "fake-vss.mjs");
  await writeFile(
    fixtureBin,
    `#!/usr/bin/env node
const gate = process.env.CHILD_GATE_URL;
if (gate) {
  const response = await fetch(gate);
  await response.body?.cancel();
}
const [stdout = "", stderr = "", code = "0"] = process.argv.slice(2);
process.stdout.write(stdout);
process.stderr.write(stderr);
process.exitCode = Number(code);
`,
  );
  await chmod(fixtureBin, 0o755);
});

after(async () => {
  await rm(fixtureDir, { recursive: true, force: true });
});

async function withEnv(values, fn) {
  const saved = new Map(TEST_ENV.map((key) => [key, process.env[key]]));
  for (const key of TEST_ENV) delete process.env[key];
  for (const [key, value] of Object.entries(values)) process.env[key] = value;
  try {
    return await fn();
  } finally {
    for (const [key, value] of saved) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

function relayEnv(url) {
  return { VSS_RELAY_URL: url, VSS_RELAY_RUN: "run-1", VSS_RELAY_TRIAL: "trial__abc" };
}

async function listen(server) {
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  return `http://127.0.0.1:${server.address().port}`;
}

async function eventSink({ status = 204, redirectTo, stalled = false } = {}) {
  const bus = new EventEmitter();
  const events = [];
  let releaseResponse;
  let released = !stalled;
  const responseGate = stalled
    ? new Promise((resolve) => {
        releaseResponse = () => {
          released = true;
          resolve();
        };
      })
    : Promise.resolve();
  const server = createServer(async (request, response) => {
    response.on("error", () => {});
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const event = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    events.push({
      event,
      headers: request.headers,
      path: request.url,
    });
    bus.emit("event");
    await responseGate;
    response.writeHead(status, {
      ...(redirectTo ? { Location: redirectTo } : {}),
      "Content-Length": "0",
    });
    response.end();
  });
  const origin = await listen(server);
  return {
    url: `${origin}/relay/events`,
    events,
    get released() {
      return released;
    },
    release() {
      releaseResponse?.();
    },
    async waitFor(count, timeout = 1_000) {
      while (events.length < count) {
        await once(bus, "event", { signal: AbortSignal.timeout(timeout) });
      }
    },
    async close() {
      releaseResponse?.();
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
    },
  };
}

async function childGate() {
  const bus = new EventEmitter();
  let hits = 0;
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const server = createServer(async (_request, response) => {
    hits += 1;
    bus.emit("hit");
    await gate;
    response.writeHead(204, { "Content-Length": "0" });
    response.end();
  });
  const origin = await listen(server);
  return {
    url: `${origin}/go`,
    release,
    async wait() {
      if (hits === 0) await once(bus, "hit", { signal: AbortSignal.timeout(1_000) });
    },
    async close() {
      release();
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
    },
  };
}

function registeredTool(config = { vssBin: fixtureBin }) {
  let registered;
  registerTools({
    pluginConfig: config,
    registerTool(tool) {
      assert.equal(registered, undefined, "vss plugin registered more than one tool");
      registered = tool;
    },
  });
  assert.equal(registered?.name, "vss_cli");
  assert.equal(typeof registered.execute, "function");
  return registered;
}

async function executeTool(params, { config, signal, toolCallId = "tool-call-private" } = {}) {
  const wrapped = await registeredTool(config).execute(toolCallId, params, signal, undefined);
  assert.deepEqual(JSON.parse(wrapped.content[0].text), wrapped.details);
  return wrapped.details;
}

function endEvent(sink) {
  assert.equal(sink.events.length, 2);
  assert.deepEqual(
    sink.events.map(({ event }) => event.scope_category),
    ["start", "end"],
  );
  return sink.events[1].event;
}

async function bounded(promise, timeout = 750) {
  let timer;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(`operation exceeded ${timeout}ms`)), timeout);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

test("disabled telemetry preserves the actual registered tool result", async () => {
  const sink = await eventSink();
  try {
    const result = await withEnv({}, () => executeTool({ args: ["stdout", "stderr", "0"] }));
    assert.deepEqual(result, {
      command: `${fixtureBin} stdout stderr 0`,
      exitCode: 0,
      signal: null,
      timedOut: false,
      stdout: "stdout",
      stderr: "stderr",
      truncated: false,
    });
    assert.equal(sink.events.length, 0);
  } finally {
    await sink.close();
  }
});

test("invalid sink or trial identity stays disabled", async () => {
  const sink = await eventSink();
  const valid = relayEnv(sink.url);
  const cases = [
    { VSS_RELAY_RUN: valid.VSS_RELAY_RUN, VSS_RELAY_TRIAL: valid.VSS_RELAY_TRIAL },
    { VSS_RELAY_URL: valid.VSS_RELAY_URL, VSS_RELAY_TRIAL: valid.VSS_RELAY_TRIAL },
    { VSS_RELAY_URL: valid.VSS_RELAY_URL, VSS_RELAY_RUN: valid.VSS_RELAY_RUN },
    { ...valid, VSS_RELAY_URL: `${sink.url}?credential=private` },
    { ...valid, VSS_RELAY_URL: `${sink.url}#fragment` },
    { ...valid, VSS_RELAY_URL: sink.url.replace("http://", "http://user@") },
    { ...valid, VSS_RELAY_URL: sink.url.replace("http://", "http://@") },
    { ...valid, VSS_RELAY_URL: sink.url.replace("http://", "ftp://") },
    { ...valid, VSS_RELAY_URL: "http://127.0.0.1:99999/relay/events" },
    { ...valid, VSS_RELAY_RUN: "../other-run" },
    { ...valid, VSS_RELAY_RUN: "run\nother" },
    { ...valid, VSS_RELAY_TRIAL: "t".repeat(256) },
  ];
  try {
    for (const environment of cases) {
      const result = await withEnv(environment, () => executeTool({ args: ["out", "err", "0"] }));
      assert.equal(result.exitCode, 0);
      assert.equal(result.stdout, "out");
      assert.equal(result.stderr, "err");
    }
    assert.equal(sink.events.length, 0);
  } finally {
    await sink.close();
  }
});

test("event name is the registered tool, never parsed from its arguments", async () => {
  const sink = await eventSink();
  const cases = [
    ["search", "run", "tag"],
    ["configure", "--timeout", "1", "show"],
  ];
  try {
    await withEnv(relayEnv(sink.url), async () => {
      for (const args of cases) {
        const before = sink.events.length;
        const result = await executeTool({ args }, { config: { vssBin: "/bin/true" } });
        assert.equal(result.exitCode, 0);
        await sink.waitFor(before + 2);
        assert.deepEqual(
          sink.events.slice(before).map(({ event }) => event.name),
          ["vss_cli", "vss_cli"],
        );
      }
    });
  } finally {
    await sink.close();
  }
});

test("start is live while the child is pending and end is correlated without private data", async () => {
  const baseline = await withEnv({}, () => executeTool({ args: ["private-stdout", "private-stderr", "0"] }));
  const sink = await eventSink();
  const gate = await childGate();
  try {
    let settled = false;
    const call = withEnv(
      {
        ...relayEnv(sink.url),
        CHILD_GATE_URL: gate.url,
        UNRELATED_CREDENTIAL: "credential-must-not-leak",
      },
      () => executeTool(
        { args: ["private-stdout", "private-stderr", "0"] },
        { toolCallId: "private-tool-call-id" },
      ),
    ).finally(() => {
      settled = true;
    });
    await Promise.all([sink.waitFor(1), gate.wait()]);
    assert.equal(settled, false);
    gate.release();
    assert.deepEqual(await call, baseline);
    await sink.waitFor(2);

    const [{ event: start, headers, path }, { event: end }] = sink.events;
    assert.equal(path, "/relay/events");
    assert.equal(headers["x-relay-run"], "run-1");
    assert.equal(headers["x-relay-trial"], "trial__abc");
    assert.equal(start.kind, "scope");
    assert.equal(start.category, "tool");
    assert.equal(start.name, "vss_cli");
    assert.equal(start.data_schema, "nvidia.vss.openclaw.tool-lifecycle/v1");
    assert.equal(start.uuid, end.uuid);
    assert.equal(start.metadata.session_id, "vss-eval/run-1/trial__abc");
    assert.equal(start.metadata.source, "vss-openclaw");
    assert.equal("duration_ms" in start.metadata, false);
    assert.ok(end.metadata.duration_ms >= 0);
    assert.deepEqual(end.data, { exit_code: 0, signal: null });

    const encoded = JSON.stringify(sink.events);
    for (const privateValue of [
      "private-stdout",
      "private-stderr",
      "private-tool-call-id",
      "credential-must-not-leak",
    ]) {
      assert.equal(encoded.includes(privateValue), false);
    }
  } finally {
    gate.release();
    await gate.close();
    await sink.close();
  }
});

test("nonzero exit and spawn failure preserve results and emit generic failures", async () => {
  const sink = await eventSink();
  try {
    const nonzeroParams = { args: ["out-7", "err-7", "7"] };
    const nonzeroBaseline = await withEnv({}, () => executeTool(nonzeroParams));
    const nonzero = await withEnv(relayEnv(sink.url), () => executeTool(nonzeroParams));
    assert.deepEqual(nonzero, nonzeroBaseline);
    await sink.waitFor(2);
    assert.deepEqual(endEvent(sink).data, {
      exit_code: 7,
      signal: null,
      error: "Tool execution failed",
    });

    sink.events.length = 0;
    const missing = join(fixtureDir, "does-not-exist");
    const config = { vssBin: missing };
    const spawnBaseline = await withEnv({}, () => executeTool({ args: [] }, { config }));
    const spawnFailure = await withEnv(relayEnv(sink.url), () => executeTool({ args: [] }, { config }));
    assert.deepEqual(spawnFailure, spawnBaseline);
    assert.equal(spawnFailure.exitCode, null);
    assert.match(spawnFailure.stderr, /ENOENT/);
    await sink.waitFor(2);
    assert.deepEqual(endEvent(sink).data, {
      exit_code: null,
      signal: null,
      error: "Tool execution failed",
    });
  } finally {
    await sink.close();
  }
});

async function interruptedRun(kind, relayUrl) {
  const gate = await childGate();
  const controller = new AbortController();
  try {
    return await withEnv(
      { ...(relayUrl ? relayEnv(relayUrl) : {}), CHILD_GATE_URL: gate.url },
      async () => {
        const call = executeTool(
          { args: ["never-written", "never-written", "0"], ...(kind === "timeout" ? { timeoutSec: 1 } : {}) },
          { signal: kind === "abort" ? controller.signal : undefined },
        );
        await gate.wait();
        if (kind === "abort") controller.abort();
        return await call;
      },
    );
  } finally {
    gate.release();
    await gate.close();
  }
}

for (const kind of ["abort", "timeout"]) {
  test(`${kind} preserves the existing result and emits its actual completion`, async () => {
    const baseline = await interruptedRun(kind);
    const sink = await eventSink();
    try {
      const result = await interruptedRun(kind, sink.url);
      assert.deepEqual(result, baseline);
      await sink.waitFor(2, 1_500);
      const end = endEvent(sink);
      assert.equal(end.data.exit_code, result.exitCode);
      assert.equal(end.data.signal, result.signal);
      assert.equal(end.data.error, "Tool execution failed");
      assert.equal(JSON.stringify(sink.events).includes("never-written"), false);
    } finally {
      await sink.close();
    }
  });
}

test("collector HTTP failure is ignored without retrying or changing the result", async () => {
  const baseline = await withEnv({}, () => executeTool({ args: ["out", "err", "0"] }));
  const sink = await eventSink({ status: 503 });
  try {
    const result = await withEnv(relayEnv(sink.url), () => executeTool({ args: ["out", "err", "0"] }));
    assert.deepEqual(result, baseline);
    await sink.waitFor(2);
    assert.equal(sink.events.length, 2);
  } finally {
    await sink.close();
  }
});

test("a stalled collector cannot hold the child result", async () => {
  const baseline = await withEnv({}, () => executeTool({ args: ["out", "err", "0"] }));
  const sink = await eventSink({ stalled: true });
  const gate = await childGate();
  try {
    const call = withEnv(
      { ...relayEnv(sink.url), CHILD_GATE_URL: gate.url },
      () => executeTool({ args: ["out", "err", "0"] }),
    );
    await Promise.all([sink.waitFor(1), gate.wait()]);
    assert.equal(sink.released, false);
    gate.release();
    assert.deepEqual(await bounded(call), baseline);
    assert.equal(sink.released, false);
    await sink.waitFor(2);
  } finally {
    sink.release();
    gate.release();
    await gate.close();
    await sink.close();
  }
});

test("the tool result does not await a fetch promise that ignores its abort signal", async () => {
  const baseline = await withEnv({}, () => executeTool({ args: ["out", "err", "0"] }));
  const originalFetch = globalThis.fetch;
  const bus = new EventEmitter();
  const pending = [];
  const waitForCalls = async (count) => {
    while (pending.length < count) {
      await once(bus, "call", { signal: AbortSignal.timeout(1_000) });
    }
  };
  globalThis.fetch = (_url, _options) => new Promise((resolve) => {
    pending.push({ resolve, resolved: false });
    bus.emit("call");
  });
  try {
    await withEnv(relayEnv("http://relay.invalid/events"), async () => {
      const call = executeTool({ args: ["out", "err", "0"] });
      await waitForCalls(1);
      assert.deepEqual(await bounded(call), baseline);
      assert.equal(pending[0].resolved, false);

      pending[0].resolved = true;
      pending[0].resolve(new Response(null, { status: 204 }));
      await waitForCalls(2);
      pending[1].resolved = true;
      pending[1].resolve(new Response(null, { status: 204 }));
      await new Promise((resolve) => setImmediate(resolve));
    });
  } finally {
    for (const request of pending) {
      if (!request.resolved) request.resolve(new Response(null, { status: 204 }));
    }
    globalThis.fetch = originalFetch;
  }
});

test("redirects never receive trial identity", async () => {
  const destination = await eventSink();
  const source = await eventSink({ status: 307, redirectTo: destination.url });
  try {
    const result = await withEnv(relayEnv(source.url), () => executeTool({ args: ["out", "err", "0"] }));
    assert.equal(result.exitCode, 0);
    await source.waitFor(2);
    await assert.rejects(destination.waitFor(1, 300));
    assert.equal(destination.events.length, 0);
  } finally {
    await source.close();
    await destination.close();
  }
});

test("a synchronous execFile rejection is rethrown unchanged and recorded generically", async () => {
  const sink = await eventSink();
  try {
    await withEnv(relayEnv(sink.url), async () => {
      await assert.rejects(
        executeTool({ args: ["argument\0must-not-leak"] }),
        (error) => error?.code === "ERR_INVALID_ARG_VALUE",
      );
    });
    await sink.waitFor(2);
    assert.deepEqual(endEvent(sink).data, {
      exit_code: null,
      signal: null,
      error: "Tool execution failed",
    });
    assert.equal(JSON.stringify(sink.events).includes("must-not-leak"), false);
  } finally {
    await sink.close();
  }
});
