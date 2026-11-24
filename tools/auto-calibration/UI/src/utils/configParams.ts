/*
* SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
* SPDX-License-Identifier: Apache-2.0
*
* Licensed under the Apache License, Version 2.0 (the "License");
* you may not use this file except in compliance with the License.
* You may obtain a copy of the License at
*
* http://www.apache.org/licenses/LICENSE-2.0
*
* Unless required by applicable law or agreed to in writing, software
* distributed under the License is distributed on an "AS IS" BASIS,
* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
* See the License for the specific language governing permissions and
* limitations under the License.
*/

const RECTIFICATION_KEYS = ['model', 'k1_min', 'k1_max', 'k1_step'] as const;
const RECTIFICATION_FIELDS = new Set<string>(RECTIFICATION_KEYS);
type RectificationField = typeof RECTIFICATION_KEYS[number];

/** Normal parameter edits must not overwrite rectification settings. */
export function calibrationOnlyParams<T extends object>(config: T): Omit<T, RectificationField> {
  return Object.fromEntries(
    Object.entries(config).filter(([key]) => !RECTIFICATION_FIELDS.has(key)),
  ) as Omit<T, RectificationField>;
}
