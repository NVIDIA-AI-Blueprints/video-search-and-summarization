// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { parseStepPayload, prettyPrintStructured } from '../lib-src/stepPayload';

describe('prettyPrintStructured', () => {
  it('indents compact JSON', () => {
    expect(prettyPrintStructured('{"query":"forklift"}')).toBe('{\n  "query": "forklift"\n}');
  });

  it('pretty-prints a Python-repr dict without breaking nested JSON strings', () => {
    const pretty = prettyPrintStructured(
      "{'incident_id': 'abc', 'info': {'objectTimeLine': '{\"280345\":[{\"start\":\"t\"}]}'}}",
    );
    expect(pretty).toContain('"incident_id": "abc"');
    expect(pretty).toContain('"280345"');
    expect(pretty).toContain('"start": "t"');
    expect(JSON.parse(pretty!)).toEqual({
      incident_id: 'abc',
      info: { objectTimeLine: { '280345': [{ start: 't' }] } },
    });
  });

  it('converts True/False/None', () => {
    expect(JSON.parse(prettyPrintStructured("{'ok': True, 'n': None, 'off': False}")!)).toEqual({
      ok: true,
      n: null,
      off: false,
    });
  });

  it('keeps an apostrophe inside a Python string', () => {
    expect(JSON.parse(prettyPrintStructured("{'note': 'it\\'s close'}")!)).toEqual({
      note: "it's close",
    });
  });

  it('returns null for prose and real Python source', () => {
    expect(prettyPrintStructured('Waiting for the agent backend...')).toBeNull();
    expect(prettyPrintStructured('3 results')).toBeNull();
    expect(prettyPrintStructured('def foo():\n  return 1')).toBeNull();
  });

  it('does not round integers that JavaScript cannot represent exactly', () => {
    const python = "{'id': 9007199254740993}";
    const json = '{"id": 9007199254740993}';
    expect(prettyPrintStructured(python)).toBeNull();
    expect(prettyPrintStructured(json)).toBeNull();
    expect(parseStepPayload(python)).toEqual([
      { kind: 'code', language: 'json', value: python },
    ]);
    expect(prettyPrintStructured("{'id': 9007199254740991}")).toContain('9007199254740991');
    expect(prettyPrintStructured("{'note': '9007199254740993'}")).toContain('9007199254740993');
  });
});

describe('parseStepPayload', () => {
  it('turns a compact JSON payload into one json code block', () => {
    expect(parseStepPayload('{"query":"forklift"}')).toEqual([
      { kind: 'code', language: 'json', value: '{\n  "query": "forklift"\n}' },
    ]);
  });

  it('leaves agent status prose as text', () => {
    expect(parseStepPayload('Waiting for the agent backend...')).toEqual([
      { kind: 'text', value: 'Waiting for the agent backend...' },
    ]);
  });

  it('splits markdown Input/Output fences into labeled json blocks', () => {
    const payload = [
      '**Input:**',
      '```python',
      "{'incident_id': 'abc', 'alert_sensor_id': 'Camera_01'}",
      '```',
      '**Output:**',
      '```python',
      "{'ok': True}",
      '```',
    ].join('\n');

    const blocks = parseStepPayload(payload);
    expect(blocks).toHaveLength(2);
    expect(blocks[0]).toMatchObject({ kind: 'code', language: 'json', label: 'Input' });
    expect(blocks[1]).toMatchObject({ kind: 'code', language: 'json', label: 'Output' });
    expect(JSON.parse(blocks[0].value)).toEqual({
      incident_id: 'abc',
      alert_sensor_id: 'Camera_01',
    });
    expect(JSON.parse(blocks[1].value)).toEqual({ ok: true });
  });

  it('attaches Function Input to an unclosed fence while the step is still streaming', () => {
    const blocks = parseStepPayload("**Function Input:**\n```python\n{'alert_sensor_id': 'Camera_01'");
    expect(blocks).toEqual([
      {
        kind: 'code',
        language: 'python',
        label: 'Function Input',
        value: "{'alert_sensor_id': 'Camera_01'",
      },
    ]);
  });

  it('does not treat ordinary prose as a labeled block', () => {
    expect(parseStepPayload('Input is missing from the clip')).toEqual([
      { kind: 'text', value: 'Input is missing from the clip' },
    ]);
  });

  it('pretty-prints an unfenced labeled Python dict', () => {
    const blocks = parseStepPayload("**Function Input:**\n{'alert_sensor_id': 'Camera_01', 'info': {}}");
    expect(blocks).toHaveLength(1);
    expect(blocks[0]).toMatchObject({ kind: 'code', language: 'json', label: 'Function Input' });
    expect(JSON.parse(blocks[0].value)).toEqual({ alert_sensor_id: 'Camera_01', info: {} });
  });

  it('recognizes Function Input without markdown bold', () => {
    const blocks = parseStepPayload("Function Input: {'alert_sensor_id': 'Camera_01'}");
    expect(blocks[0]).toMatchObject({ kind: 'code', language: 'json', label: 'Function Input' });
    expect(JSON.parse(blocks[0].value)).toEqual({ alert_sensor_id: 'Camera_01' });
  });

  it('keeps a fence language extra token from becoming the language', () => {
    const blocks = parseStepPayload('```python highlight\n{"ok": true}\n```');
    expect(blocks).toEqual([
      { kind: 'code', language: 'json', value: '{\n  "ok": true\n}' },
    ]);
  });
});
