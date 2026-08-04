/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
import config from '../../config';
import LOG from './Logger';

/**
 * Thin backward-compatible wrappers that delegate to the {@link LOG} singleton.
 * Prefer importing `LOG` directly in new code. Output is gated by `config.enableLogs`
 * (legacy behaviour) in addition to LOG's own level threshold.
 */
export const logInfo = (...messages: unknown[]) => {
    if (config.enableLogs) {
        LOG.info(...messages);
    }
};

export const logError = (...messages: unknown[]) => {
    if (config.enableLogs) {
        LOG.error(...messages);
    }
};
