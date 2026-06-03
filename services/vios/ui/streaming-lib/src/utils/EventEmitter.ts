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

/** Type definition for event listener functions that can take any number of arguments */
type Listener<T = unknown> = (...args: T[]) => void;

/**
 * EventEmitter class that implements the observer pattern for event handling
 * Allows subscribing to events, unsubscribing, and emitting events to listeners
 */
export class EventEmitter {
    /** Object to store event names and their corresponding listener functions */
    private events: { [key: string]: Listener<unknown>[] } = {};

    /**
     * Subscribe a listener function to an event
     * @param event - Name of the event to listen for
     * @param listener - Function to be called when event is emitted
     */
    on<T = unknown>(event: string, listener: Listener<T>): void {
        if (!this.events[event]) {
            this.events[event] = [];
        }
        this.events[event].push(listener as Listener<unknown>);
    }

    /**
     * Unsubscribe a specific listener function from an event
     * @param event - Name of the event to unsubscribe from
     * @param listener - Function to remove from event listeners
     */
    off<T = unknown>(event: string, listener: Listener<T>): void {
        if (!this.events[event]) return;
        this.events[event] = this.events[event].filter(l => l !== listener);
    }

    /**
     * Emit an event, triggering all listener functions subscribed to it
     * @param event - Name of the event to emit
     * @param args - Arguments to pass to the listener functions
     */
    emit<T = unknown>(event: string, ...args: T[]): void {
        if (!this.events[event]) return;
        this.events[event].forEach(listener => listener(...args));
    }

    /**
     * Remove all listeners for a specific event, or all events if no event specified
     * @param event - Optional name of event to clear listeners for
     */
    removeAllListeners(event?: string): void {
        if (event) {
            delete this.events[event];
        } else {
            this.events = {};
        }
    }
}
