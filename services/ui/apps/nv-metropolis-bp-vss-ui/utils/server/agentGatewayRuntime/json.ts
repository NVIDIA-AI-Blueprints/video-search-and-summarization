// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { JsonObject } from "./contract";

/** Parse JSON while rejecting duplicate object keys and non-finite numbers. */
export const strictJsonParse = (source: string): unknown => {
  let cursor = 0;

  const fail = (): never => {
    throw new SyntaxError("invalid strict JSON");
  };
  const whitespace = (): void => {
    while (/[\u0009\u000a\u000d\u0020]/u.test(source[cursor] ?? "")) {
      cursor += 1;
    }
  };
  const stringValue = (): string => {
    if (source[cursor] !== '"') fail();
    const start = cursor;
    cursor += 1;
    let escaped = false;
    while (cursor < source.length) {
      const character = source[cursor];
      if (!escaped && character === '"') {
        cursor += 1;
        return JSON.parse(source.slice(start, cursor)) as string;
      }
      if (!escaped && character.charCodeAt(0) < 0x20) fail();
      if (!escaped && character === "\\") {
        escaped = true;
      } else {
        escaped = false;
      }
      cursor += 1;
    }
    return fail();
  };
  const value = (depth: number): unknown => {
    if (depth > 1_000) fail();
    whitespace();
    const character = source[cursor];
    if (character === '"') return stringValue();
    if (character === "{") {
      cursor += 1;
      whitespace();
      // JSON keys such as "__proto__" must remain ordinary own properties
      // instead of invoking Object.prototype setters.
      const result = Object.create(null) as JsonObject;
      const keys = new Set<string>();
      if (source[cursor] === "}") {
        cursor += 1;
        return result;
      }
      while (cursor < source.length) {
        whitespace();
        const key = stringValue();
        if (keys.has(key)) fail();
        keys.add(key);
        whitespace();
        if (source[cursor] !== ":") fail();
        cursor += 1;
        result[key] = value(depth + 1);
        whitespace();
        if (source[cursor] === "}") {
          cursor += 1;
          return result;
        }
        if (source[cursor] !== ",") fail();
        cursor += 1;
      }
      return fail();
    }
    if (character === "[") {
      cursor += 1;
      whitespace();
      const result: unknown[] = [];
      if (source[cursor] === "]") {
        cursor += 1;
        return result;
      }
      while (cursor < source.length) {
        result.push(value(depth + 1));
        whitespace();
        if (source[cursor] === "]") {
          cursor += 1;
          return result;
        }
        if (source[cursor] !== ",") fail();
        cursor += 1;
      }
      return fail();
    }
    for (const [token, parsed] of [
      ["true", true],
      ["false", false],
      ["null", null],
    ] as const) {
      if (source.startsWith(token, cursor)) {
        cursor += token.length;
        return parsed;
      }
    }
    const match = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/u.exec(
      source.slice(cursor)
    );
    if (!match) return fail();
    cursor += match[0].length;
    const parsed = Number(match[0]);
    return Number.isFinite(parsed) ? parsed : fail();
  };

  try {
    const parsed = value(0);
    whitespace();
    if (cursor !== source.length) fail();
    return parsed;
  } catch (error) {
    if (error instanceof SyntaxError) throw error;
    throw new SyntaxError("invalid strict JSON", { cause: error });
  }
};

export const isJsonObject = (value: unknown): value is JsonObject =>
  !!value && typeof value === "object" && !Array.isArray(value);
