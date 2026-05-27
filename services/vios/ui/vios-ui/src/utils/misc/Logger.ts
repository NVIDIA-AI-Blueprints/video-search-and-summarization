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
enum LogLevel {
    VERBOSE,
    INFO,
    WARN,
    ERROR,
}

type LogFunction = (message: string, ...args: unknown[]) => void;

class Logger {
    private static instance: Logger;

    public static getInstance(): Logger {
        if (!Logger.instance) {
            Logger.instance = new Logger();
        }
        return Logger.instance;
    }

    private formatMessage(_level: string, message: string, args: unknown[]): string {
        const timestamp = new Date().toISOString();
        const formattedArgs = args.map(arg => (typeof arg === 'object' ? JSON.stringify(arg, null, 2) : String(arg))).join(' ');
        return `[${timestamp}] ${message} ${formattedArgs}`;
    }

    private log(level: LogLevel, message: string, args: unknown[]): void {
        const formattedMessage = this.formatMessage(LogLevel[level], message, args);
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
        console.group(label);
    }

    public groupEnd(): void {
        console.groupEnd();
    }

    public table(tabularData: Record<string, unknown>[], properties?: readonly string[] | string[]): void {
        console.table(tabularData, properties as string[] | undefined);
    }

    public time(label: string): void {
        console.time(label);
    }

    public timeEnd(label: string): void {
        console.timeEnd(label);
    }
}

const LOG = Logger.getInstance();
export default LOG;
