// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// Build-time path prefix; all browser requests stay on the app's own origin.
export const basePath = process.env.NEXT_PUBLIC_BASE_PATH || '';
export const appPath = (path: string) => `${basePath}${path}`;
