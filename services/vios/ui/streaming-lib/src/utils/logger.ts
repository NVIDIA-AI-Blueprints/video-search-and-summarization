/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

/* eslint-disable no-console */
export class Logger {
    private static instance: Logger;
    private enableLogs: boolean = true;

    private constructor() {}

    public static getInstance(): Logger {
        if (!Logger.instance) {
            Logger.instance = new Logger();
        }
        return Logger.instance;
    }

    public setLoggingEnabled(enabled: boolean): void {
        this.enableLogs = enabled;
    }

    private formatMessage(prefix: string, ...args: unknown[]): string {
        const timestamp = new Date().toISOString();
        const formattedArgs = args.map(arg => (typeof arg === 'object' ? JSON.stringify(arg) : String(arg))).join(' ');
        return `[${timestamp}] ${prefix} ${formattedArgs}`;
    }

    public info(...args: unknown[]): void {
        if (this.enableLogs) {
            console.log(this.formatMessage('INFO:', ...args));
        }
    }

    public error(...args: unknown[]): void {
        if (this.enableLogs) {
            console.error(this.formatMessage('ERROR:', ...args));
        }
    }

    public warn(...args: unknown[]): void {
        if (this.enableLogs) {
            console.warn(this.formatMessage('WARN:', ...args));
        }
    }

    public debug(...args: unknown[]): void {
        if (this.enableLogs) {
            console.debug(this.formatMessage('DEBUG:', ...args));
        }
    }
}

// Export singleton instance
export const logger = Logger.getInstance();

// For backward compatibility
export const logInfo = (...args: unknown[]): void => logger.info(...args);
export const logError = (...args: unknown[]): void => logger.error(...args);
