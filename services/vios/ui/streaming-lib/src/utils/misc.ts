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

// Simple UUID generator, replace with better generator if use-case is for cryptographic purposes
export const generateUUID = (): string =>
    'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c: string): string => {
        const r: number = (Math.random() * 16) | 0;
        const v: number = c === 'x' ? r : (r & 0x3) | 0x8;
        return v.toString(16);
    });

export const rewriteSdp = (sdp: { sdp: string }, maxBitrate: number, minBitrate: number, startBitrate: number): { sdp: string } => {
    const sdpStringFind = 'a=fmtp:(.*) (.*)';
    const sdpStringReplace = `a=fmtp:$1 $2;x-google-max-bitrate=${maxBitrate};x-google-min-bitrate=${minBitrate};x-google-start-bitrate=${startBitrate}`;
    let newSDP = sdp.sdp.toString();
    newSDP = newSDP.replace(new RegExp(sdpStringFind, 'g'), sdpStringReplace);
    sdp.sdp = newSDP;
    return sdp;
};

export const getButtonById = (id: string): HTMLButtonElement | undefined => {
    const button = document.getElementById(id) as HTMLButtonElement | null;
    if (button) {
        return button;
    }
    return undefined;
};
