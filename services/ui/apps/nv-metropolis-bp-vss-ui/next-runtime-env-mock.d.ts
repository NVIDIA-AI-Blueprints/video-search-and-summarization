// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/** Jest mock (__mocks__/next-runtime-env.js) adds setMockEnv/clearMockEnv for tests. */
declare module 'next-runtime-env' {
  export function env(key: string): string | undefined;
  export function setMockEnv(key: string, value: string): void;
  export function clearMockEnv(): void;
}
