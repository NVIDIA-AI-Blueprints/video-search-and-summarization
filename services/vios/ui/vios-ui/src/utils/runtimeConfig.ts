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

interface ViosRuntimeConfig {
    basePath?: unknown;
}

declare global {
    interface Window {
        __VIOS_RUNTIME_CONFIG__?: ViosRuntimeConfig;
    }
}

const hasTraversalSegment = (path: string): boolean => {
    return path.split('/').some(segment => {
        try {
            const decodedSegment = decodeURIComponent(segment);
            return decodedSegment === '.' || decodedSegment === '..';
        } catch {
            return true;
        }
    });
};

const hasControlCharacter = (value: string): boolean => {
    return Array.from(value).some(character => {
        const codePoint = character.codePointAt(0) ?? 0;
        return codePoint < 0x20 || codePoint === 0x7f;
    });
};

export const normalizeBasePath = (value: string): string | null => {
    if (value === '' || value === '/') {
        return '';
    }

    if (
        value !== value.trim() ||
        !value.startsWith('/') ||
        hasControlCharacter(value) ||
        value.includes('?') ||
        value.includes('#') ||
        value.includes('\\') ||
        hasTraversalSegment(value)
    ) {
        return null;
    }

    return value.replace(/\/+$/, '');
};

export const inferBasePath = (documentBaseUri: string): string => {
    const documentPath = new URL(documentBaseUri).pathname;
    const documentDirectory = documentPath.endsWith('/')
        ? documentPath
        : documentPath.slice(0, documentPath.lastIndexOf('/') + 1);

    return normalizeBasePath(documentDirectory) ?? '';
};

export const resolveBasePath = (configuredValue: unknown, documentBaseUri: string): string => {
    if (typeof configuredValue === 'string') {
        const normalizedPath = normalizeBasePath(configuredValue);
        if (normalizedPath !== null) {
            return normalizedPath;
        }
    }

    return inferBasePath(documentBaseUri);
};

export const getPublicBasePath = (): string => {
    const configuredValue = window.__VIOS_RUNTIME_CONFIG__?.basePath;
    if (typeof configuredValue === 'string' && normalizeBasePath(configuredValue) === null) {
        // Runtime configuration is public deployment input. Fall back safely instead of
        // allowing an invalid path to redirect requests to another origin.
        console.warn(`Ignoring invalid VIOS UI base path: ${configuredValue}`);
    }

    return resolveBasePath(configuredValue, document.baseURI);
};

export const appendBasePath = (origin: string, basePath: string): string => {
    const normalizedPath = normalizeBasePath(basePath);
    if (normalizedPath === null) {
        throw new Error(`Invalid VIOS UI base path: ${basePath}`);
    }

    return `${origin.replace(/\/+$/, '')}${normalizedPath}`;
};

export const toWebSocketUrl = (httpUrl: string): string => {
    const protocol = new URL(httpUrl).protocol;
    if (protocol === 'https:') {
        return httpUrl.replace(/^https:/, 'wss:');
    }
    if (protocol === 'http:') {
        return httpUrl.replace(/^http:/, 'ws:');
    }

    throw new Error(`Unsupported VIOS endpoint protocol: ${protocol}`);
};
