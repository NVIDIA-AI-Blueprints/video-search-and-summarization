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
/* eslint-disable no-console -- this module is the sanctioned console boundary for the app */
/**
 * Severity levels, ordered low → high. Messages below the active threshold are dropped.
 * `SILENT` disables all output.
 */
export enum LogLevel {
    VERBOSE = 0,
    INFO = 1,
    WARN = 2,
    ERROR = 3,
    SILENT = 4,
}

type LogFunction = (message?: unknown, ...args: unknown[]) => void;

const LEVEL_NAMES: Record<LogLevel, string> = {
    [LogLevel.VERBOSE]: 'VERBOSE',
    [LogLevel.INFO]: 'INFO',
    [LogLevel.WARN]: 'WARN',
    [LogLevel.ERROR]: 'ERROR',
    [LogLevel.SILENT]: 'SILENT',
};

/**
 * Resolve the initial threshold. Production defaults to INFO (no verbose noise); development
 * defaults to VERBOSE. Either can be overridden at runtime via `localStorage.LOG_LEVEL`
 * (e.g. `localStorage.LOG_LEVEL = 'WARN'`) without a rebuild — a standard field-debugging affordance.
 */
const resolveInitialLevel = (): LogLevel => {
    try {
        const override = typeof localStorage !== 'undefined' ? localStorage.getItem('LOG_LEVEL') : null;
        // Numeric enums emit reverse mappings ('0' -> 'VERBOSE'), so guard against numeric keys:
        // only accept named levels, otherwise LogLevel['0'] would return the string 'VERBOSE'.
        const nameKeys = Object.keys(LogLevel).filter((k) => isNaN(Number(k)));
        if (override && nameKeys.includes(override.toUpperCase())) {
            return LogLevel[override.toUpperCase() as keyof typeof LogLevel];
        }
    } catch {
        // localStorage may be unavailable (SSR/sandboxed); fall back to the env default.
    }
    return process.env.NODE_ENV === 'development' ? LogLevel.VERBOSE : LogLevel.INFO;
};

class Logger {
    private static instance: Logger;
    private minLevel: LogLevel = resolveInitialLevel();

    public static getInstance(): Logger {
        if (!Logger.instance) {
            Logger.instance = new Logger();
        }
        return Logger.instance;
    }

    /** Raise or lower the active threshold at runtime. */
    public setLevel(level: LogLevel): void {
        this.minLevel = level;
    }

    public getLevel(): LogLevel {
        return this.minLevel;
    }

    /** Convenience toggle: `false` silences all output, `true` restores the env default. */
    public setEnabled(enabled: boolean): void {
        this.minLevel = enabled ? resolveInitialLevel() : LogLevel.SILENT;
    }

    private isEnabled(level: LogLevel): boolean {
        return level >= this.minLevel && this.minLevel !== LogLevel.SILENT;
    }

    private formatMessage(level: LogLevel, message: unknown, args: unknown[]): string {
        const timestamp = new Date().toISOString();
        const stringify = (arg: unknown): string => (typeof arg === 'object' && arg !== null ? JSON.stringify(arg, null, 2) : String(arg));
        const head = stringify(message);
        const tail = args.map(stringify).join(' ');
        return `[${timestamp}] [${LEVEL_NAMES[level]}] ${head}${tail ? ` ${tail}` : ''}`;
    }

    private log(level: LogLevel, message: unknown, args: unknown[]): void {
        if (!this.isEnabled(level)) {
            return;
        }
        const formattedMessage = this.formatMessage(level, message, args);
        switch (level) {
            case LogLevel.VERBOSE:
                console.debug(formattedMessage);
                break;
            case LogLevel.INFO:
                console.log(formattedMessage);
                break;
            case LogLevel.WARN:
                console.warn(formattedMessage);
                break;
            case LogLevel.ERROR:
                console.error(formattedMessage);
                break;
            default:
                break;
        }
    }

    public verbose: LogFunction = (message, ...args) => {
        this.log(LogLevel.VERBOSE, message, args);
    };

    public info: LogFunction = (message, ...args) => {
        this.log(LogLevel.INFO, message, args);
    };

    public warn: LogFunction = (message, ...args) => {
        this.log(LogLevel.WARN, message, args);
    };

    public error: LogFunction = (message, ...args) => {
        this.log(LogLevel.ERROR, message, args);
    };

    public group(label: string): void {
        if (this.minLevel !== LogLevel.SILENT) {
            console.group(label);
        }
    }

    public groupEnd(): void {
        if (this.minLevel !== LogLevel.SILENT) {
            console.groupEnd();
        }
    }

    public table(tabularData: Record<string, unknown>[], properties?: readonly string[] | string[]): void {
        if (this.minLevel !== LogLevel.SILENT) {
            console.table(tabularData, properties as string[] | undefined);
        }
    }

    public time(label: string): void {
        if (this.minLevel !== LogLevel.SILENT) {
            console.time(label);
        }
    }

    public timeEnd(label: string): void {
        if (this.minLevel !== LogLevel.SILENT) {
            console.timeEnd(label);
        }
    }
}

const LOG = Logger.getInstance();
export default LOG;
