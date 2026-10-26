// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * Turn an intermediate-step payload into labeled, pretty-printed blocks.
 *
 * Agent tool rows commonly arrive as markdown:
 *
 *   **Input:**
 *   ```python
 *   {'incident_id': '...', 'info': {'objectTimeLine': '{"x": 1}'}}
 *   ```
 *
 * Dumping that into a wrapping <pre> shows the fences as literal text and
 * wraps mid-key. Parse the fences here and pretty-print JSON / Python-repr
 * literals so ChatSteps can render them as code blocks.
 *
 * Tokenizing and label matching are linear scans. Nested regex quantifiers on
 * untrusted tool payloads are a ReDoS hotspot (S5852).
 */

export type StepPayloadBlock =
  | { kind: 'text'; value: string; label?: string }
  | { kind: 'code'; language: string; value: string; label?: string };

const MAX_STRUCTURE_DEPTH = 64;
/** Longer names first so "Function Input" wins over "Input". */
const KNOWN_LABELS = ['Function Input', 'Function Output', 'Input', 'Output'] as const;

/** True when `value` looks like a JSON/Python object or array, even if incomplete. */
export function looksStructured(value: string): boolean {
  const trimmed = value.trimStart();
  return trimmed.startsWith('{') || trimmed.startsWith('[') || trimmed.startsWith('(');
}

/**
 * Pretty-print JSON or a Python-repr literal. Nested strings that are themselves
 * JSON objects/arrays are expanded so a stored `objectTimeLine` blob becomes
 * readable instead of one escaped line.
 */
export function prettyPrintStructured(value: string): string | null {
  const parsed = parseStructured(value);
  if (parsed === undefined) return null;
  try {
    return JSON.stringify(inflateJsonStrings(parsed), null, 2);
  } catch {
    return null;
  }
}

export function parseStepPayload(payload: string): StepPayloadBlock[] {
  const src = payload.replaceAll('\r\n', '\n').trim();
  if (!src) return [];

  const tokens = tokenize(src);
  const blocks: StepPayloadBlock[] = [];
  let pendingLabel: string | undefined;

  for (const token of tokens) {
    if (token.type === 'fence') {
      blocks.push(toCodeBlock(token.value, token.language, pendingLabel));
      pendingLabel = undefined;
      continue;
    }

    for (const part of splitLabeledText(token.value)) {
      if (!part.body) {
        if (part.label) pendingLabel = part.label;
        continue;
      }
      blocks.push(toBlock(part.body, part.label ?? pendingLabel));
      pendingLabel = undefined;
    }
  }

  return blocks.length ? blocks : [{ kind: 'text', value: payload }];
}

type Token =
  | { type: 'text'; value: string }
  | { type: 'fence'; language: string; value: string };

function tokenize(src: string): Token[] {
  const tokens: Token[] = [];
  let i = 0;
  while (i < src.length) {
    const open = src.indexOf('```', i);
    if (open === -1) {
      tokens.push({ type: 'text', value: src.slice(i) });
      break;
    }
    if (open > i) tokens.push({ type: 'text', value: src.slice(i, open) });

    let j = open + 3;
    while (j < src.length && src.charAt(j) !== '\n' && src.charAt(j) !== '`') j += 1;
    const language = firstWord(src.slice(open + 3, j));
    if (j < src.length && src.charAt(j) === '\n') j += 1;

    const close = src.indexOf('```', j);
    const bodyEnd = close === -1 ? src.length : close;
    let value = src.slice(j, bodyEnd);
    if (value.endsWith('\n')) value = value.slice(0, -1);
    tokens.push({ type: 'fence', language, value });
    i = close === -1 ? src.length : close + 3;
  }
  return tokens.length ? tokens : [{ type: 'text', value: src }];
}

function firstWord(value: string): string {
  const trimmed = value.trim();
  let end = 0;
  while (end < trimmed.length && !isAsciiWs(trimmed.charAt(end))) end += 1;
  return trimmed.slice(0, end);
}

function splitLabeledText(text: string): { label?: string; body: string }[] {
  const parts: { label?: string; body: string }[] = [];
  let label: string | undefined;
  let buf: string[] = [];

  const flush = () => {
    const body = buf.join('\n').trim();
    buf = [];
    if (label || body) parts.push({ ...(label ? { label } : {}), body });
    label = undefined;
  };

  for (const line of text.split('\n')) {
    const parsed = parseLabelLine(line.trim());
    if (parsed) {
      flush();
      label = parsed.label;
      if (parsed.rest) buf.push(parsed.rest);
    } else {
      buf.push(line);
    }
  }
  flush();
  return parts;
}

function parseLabelLine(line: string): { label: string; rest: string } | undefined {
  return parseBoldLabel(line) ?? parseKnownLabel(line);
}

function parseBoldLabel(line: string): { label: string; rest: string } | undefined {
  if (!line.startsWith('**')) return undefined;
  const close = line.indexOf('**', 2);
  if (close < 3) return undefined;
  let i = close + 2;
  i = skipAsciiWs(line, i);
  if (i < line.length && line.charAt(i) === ':') i = skipAsciiWs(line, i + 1);
  const label = stripTrailingColons(line.slice(2, close)).trim();
  if (!label) return undefined;
  return { label, rest: line.slice(i).trim() };
}

function parseKnownLabel(line: string): { label: string; rest: string } | undefined {
  for (const label of KNOWN_LABELS) {
    if (!startsWithIgnoreCase(line, label)) continue;
    if (line.charAt(label.length) !== ':') continue;
    return { label, rest: line.slice(label.length + 1).trim() };
  }
  return undefined;
}

function startsWithIgnoreCase(text: string, prefix: string): boolean {
  if (text.length < prefix.length) return false;
  for (let i = 0; i < prefix.length; i += 1) {
    if (text.charAt(i).toLowerCase() !== prefix.charAt(i).toLowerCase()) return false;
  }
  return true;
}

function stripTrailingColons(value: string): string {
  let end = value.length;
  while (end > 0 && value.charAt(end - 1) === ':') end -= 1;
  return value.slice(0, end);
}

function skipAsciiWs(value: string, start: number): number {
  let i = start;
  while (i < value.length && isAsciiWs(value.charAt(i))) i += 1;
  return i;
}

function isAsciiWs(ch: string): boolean {
  return ch === ' ' || ch === '\t' || ch === '\n' || ch === '\r' || ch === '\f' || ch === '\v';
}

function toBlock(body: string, label?: string): StepPayloadBlock {
  const pretty = prettyPrintStructured(body);
  if (pretty) return { kind: 'code', language: 'json', value: pretty, ...(label ? { label } : {}) };
  if (looksStructured(body)) {
    return { kind: 'code', language: 'json', value: body, ...(label ? { label } : {}) };
  }
  return { kind: 'text', value: body, ...(label ? { label } : {}) };
}

function toCodeBlock(raw: string, language: string, label?: string): StepPayloadBlock {
  const pretty = prettyPrintStructured(raw);
  return {
    kind: 'code',
    language: pretty ? 'json' : language || 'text',
    value: pretty ?? raw,
    ...(label ? { label } : {}),
  };
}

function parseStructured(value: string): unknown {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  // JSON.parse and JS Number both round integers past 2^53. Skip formatting
  // rather than pretty-print a different id than the tool sent.
  if (sourceHasUnsafeInteger(trimmed)) return undefined;
  try {
    return JSON.parse(trimmed);
  } catch {
    // Python-repr tool arguments from NVIDIA Agent Toolkit.
  }
  return parsePythonLiteral(trimmed);
}

function inflateJsonStrings(value: unknown, depth = 0): unknown {
  if (depth > MAX_STRUCTURE_DEPTH) return value;
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (
      (trimmed.startsWith('{') && trimmed.endsWith('}')) ||
      (trimmed.startsWith('[') && trimmed.endsWith(']'))
    ) {
      if (sourceHasUnsafeInteger(trimmed)) return value;
      try {
        return inflateJsonStrings(JSON.parse(trimmed), depth + 1);
      } catch {
        return value;
      }
    }
    return value;
  }
  if (Array.isArray(value)) return value.map((item) => inflateJsonStrings(item, depth + 1));
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([key, child]) => [
        key,
        inflateJsonStrings(child, depth + 1),
      ]),
    );
  }
  return value;
}

class PyScanner {
  i = 0;
  constructor(readonly src: string) {}

  parse(): unknown {
    const value = this.parseValue(0);
    this.skipWs();
    if (this.i !== this.src.length) throw new SyntaxError('trailing python literal content');
    return value;
  }

  private parseValue(depth: number): unknown {
    if (depth > MAX_STRUCTURE_DEPTH) throw new SyntaxError('python literal too deep');
    this.skipWs();
    const char = this.src.charAt(this.i);
    if (char === '{') return this.parseDict(depth);
    if (char === '[') return this.parseList(depth);
    if (char === '(') return this.parseTuple(depth);
    if (char === "'" || char === '"') return this.parseString();
    if (this.eatWord('True') || this.eatWord('true')) return true;
    if (this.eatWord('False') || this.eatWord('false')) return false;
    if (this.eatWord('None') || this.eatWord('null')) return null;
    return this.parseNumber();
  }

  private parseDict(depth: number): Record<string, unknown> {
    this.i += 1;
    const obj: Record<string, unknown> = {};
    this.skipWs();
    if (this.src.charAt(this.i) === '}') {
      this.i += 1;
      return obj;
    }
    for (;;) {
      this.skipWs();
      const key = this.parseKey(depth);
      this.skipWs();
      if (this.src.charAt(this.i) !== ':') throw new SyntaxError('expected : in python dict');
      this.i += 1;
      obj[key] = this.parseValue(depth + 1);
      this.skipWs();
      if (this.src.charAt(this.i) === ',') {
        this.i += 1;
        this.skipWs();
        if (this.src.charAt(this.i) === '}') {
          this.i += 1;
          break;
        }
        continue;
      }
      if (this.src.charAt(this.i) === '}') {
        this.i += 1;
        break;
      }
      throw new SyntaxError('expected , or } in python dict');
    }
    return obj;
  }

  private parseList(depth: number): unknown[] {
    this.i += 1;
    return this.parseSequence(']', depth);
  }

  private parseTuple(depth: number): unknown[] {
    this.i += 1;
    return this.parseSequence(')', depth);
  }

  private parseSequence(closer: ']' | ')', depth: number): unknown[] {
    const items: unknown[] = [];
    this.skipWs();
    if (this.src.charAt(this.i) === closer) {
      this.i += 1;
      return items;
    }
    for (;;) {
      items.push(this.parseValue(depth + 1));
      this.skipWs();
      if (this.src.charAt(this.i) === ',') {
        this.i += 1;
        this.skipWs();
        if (this.src.charAt(this.i) === closer) {
          this.i += 1;
          break;
        }
        continue;
      }
      if (this.src.charAt(this.i) === closer) {
        this.i += 1;
        break;
      }
      throw new SyntaxError(`expected , or ${closer} in python sequence`);
    }
    return items;
  }

  private parseKey(depth: number): string {
    const key = this.parseValue(depth + 1);
    if (typeof key === 'string' || typeof key === 'number' || typeof key === 'boolean') {
      return String(key);
    }
    throw new SyntaxError('python dict key must be a string, number, or boolean');
  }

  private parseString(): string {
    const quote = this.src.charAt(this.i);
    this.i += 1;
    let out = '';
    while (this.i < this.src.length) {
      const char = this.src.charAt(this.i);
      if (char === quote) {
        this.i += 1;
        return out;
      }
      if (char === '\\') {
        this.i += 1;
        out += this.parseEscape();
        continue;
      }
      out += char;
      this.i += 1;
    }
    throw new SyntaxError('unterminated python string');
  }

  private parseEscape(): string {
    if (this.i >= this.src.length) throw new SyntaxError('unterminated python escape');
    const char = this.src.charAt(this.i);
    this.i += 1;
    switch (char) {
      case 'n':
        return '\n';
      case 't':
        return '\t';
      case 'r':
        return '\r';
      case 'b':
        return '\b';
      case 'f':
        return '\f';
      case '\\':
      case "'":
      case '"':
        return char;
      case 'x':
        return this.parseHexChars(2);
      case 'u':
        return this.parseHexChars(4);
      case 'U':
        return this.parseHexChars(8);
      default:
        return char;
    }
  }

  private parseHexChars(count: number): string {
    const hex = this.src.slice(this.i, this.i + count);
    if (hex.length !== count) throw new SyntaxError('invalid python hex escape');
    for (let n = 0; n < hex.length; n += 1) {
      if (!isHexChar(hex.charAt(n))) throw new SyntaxError('invalid python hex escape');
    }
    this.i += count;
    return String.fromCodePoint(Number.parseInt(hex, 16));
  }

  private parseNumber(): number {
    const start = this.i;
    if (this.src.charAt(this.i) === '-') this.i += 1;
    if (!isDigit(this.src.charAt(this.i))) throw new SyntaxError('expected python value');
    if (this.src.charAt(this.i) === '0') {
      this.i += 1;
    } else {
      while (isDigit(this.src.charAt(this.i))) this.i += 1;
    }
    if (this.src.charAt(this.i) === '.') {
      this.i += 1;
      if (!isDigit(this.src.charAt(this.i))) throw new SyntaxError('expected python value');
      while (isDigit(this.src.charAt(this.i))) this.i += 1;
    }
    const exp = this.src.charAt(this.i);
    if (exp === 'e' || exp === 'E') {
      this.i += 1;
      const sign = this.src.charAt(this.i);
      if (sign === '+' || sign === '-') this.i += 1;
      if (!isDigit(this.src.charAt(this.i))) throw new SyntaxError('expected python value');
      while (isDigit(this.src.charAt(this.i))) this.i += 1;
    }
    const next = this.src.charAt(this.i);
    if (next && isIdentChar(next)) throw new SyntaxError('invalid python number');
    const literal = this.src.slice(start, this.i);
    if (isIntegerLiteral(literal) && String(Number(literal)) !== literal) {
      throw new SyntaxError('integer exceeds JSON number precision');
    }
    return Number(literal);
  }

  private eatWord(word: string): boolean {
    if (!this.src.startsWith(word, this.i)) return false;
    const next = this.src.charAt(this.i + word.length);
    if (next && isWordChar(next)) return false;
    this.i += word.length;
    return true;
  }

  private skipWs(): void {
    this.i = skipAsciiWs(this.src, this.i);
  }
}

function isDigit(ch: string): boolean {
  return ch >= '0' && ch <= '9';
}

/** True when `src` has an integer token JS cannot round-trip through Number. */
function sourceHasUnsafeInteger(src: string): boolean {
  let i = 0;
  while (i < src.length) {
    const ch = src.charAt(i);
    if (ch === '"' || ch === "'") {
      i = skipQuoted(src, i);
      continue;
    }
    const prev = i > 0 ? src.charAt(i - 1) : '';
    const startsNumber =
      (ch === '-' && isDigit(src.charAt(i + 1)) && !isWordChar(prev)) ||
      (isDigit(ch) && !isWordChar(prev));
    if (!startsNumber) {
      i += 1;
      continue;
    }
    const start = i;
    if (ch === '-') i += 1;
    while (isDigit(src.charAt(i))) i += 1;
    if (src.charAt(i) === '.') {
      i += 1;
      while (isDigit(src.charAt(i))) i += 1;
    }
    const exp = src.charAt(i);
    if (exp === 'e' || exp === 'E') {
      i += 1;
      if (src.charAt(i) === '+' || src.charAt(i) === '-') i += 1;
      while (isDigit(src.charAt(i))) i += 1;
    }
    const literal = src.slice(start, i);
    if (isIntegerLiteral(literal) && String(Number(literal)) !== literal) return true;
  }
  return false;
}

function skipQuoted(src: string, start: number): number {
  const quote = src.charAt(start);
  let i = start + 1;
  while (i < src.length) {
    const ch = src.charAt(i);
    if (ch === '\\') {
      i += 2;
      continue;
    }
    if (ch === quote) return i + 1;
    i += 1;
  }
  return src.length;
}

function isIntegerLiteral(literal: string): boolean {
  let i = literal.charAt(0) === '-' ? 1 : 0;
  if (i >= literal.length) return false;
  while (i < literal.length) {
    if (!isDigit(literal.charAt(i))) return false;
    i += 1;
  }
  return true;
}

function isHexChar(ch: string): boolean {
  return isDigit(ch) || (ch >= 'a' && ch <= 'f') || (ch >= 'A' && ch <= 'F');
}

function isIdentChar(ch: string): boolean {
  return (ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') || ch === '_';
}

function isWordChar(ch: string): boolean {
  return isIdentChar(ch) || isDigit(ch);
}

function parsePythonLiteral(source: string): unknown {
  try {
    return new PyScanner(source).parse();
  } catch {
    return undefined;
  }
}
