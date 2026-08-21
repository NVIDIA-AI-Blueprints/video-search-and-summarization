/*
 * SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
import {
    CameraSettings,
    EncodeSettings,
    EncodingOptions,
    EnumField,
    ImageSettings,
    RangeField,
    Resolution,
    Timeline,
    WebRTCStats,
} from '../../interfaces/interfaces';
import nvAxios from '../../services/Axios';
import LOG from './Logger';

export function parseSettings(jsonString: string): CameraSettings {
    try {
        const parsedData = JSON.parse(jsonString) as CameraSettings;
        return parsedData;
    } catch (error) {
        LOG.error('Error parsing JSON:', error);
        throw new Error('Invalid JSON format');
    }
}

export function adjustCameraSettings(input: CameraSettings): string {
    const adjustedSettings: CameraSettings = JSON.parse(JSON.stringify(input));

    for (const profileKey in adjustedSettings) {
        const profile = adjustedSettings[profileKey];

        // Adjust Encode settings
        if ('Encode' in profile) {
            adjustEncodeSettings(profile.Encode);
        }

        // Adjust Image settings
        if ('Image' in profile) {
            adjustImageSettings(profile.Image);
        }
    }

    return JSON.stringify(adjustedSettings);
}

function adjustEncodeSettings(encode: EncodeSettings): void {
    if ('Encoding' in encode) {
        adjustEnumField(encode.Encoding);
    }
    if ('Options' in encode) {
        encode.Options.forEach(option => {
            adjustEncodingOptions(option[Object.keys(option)[0]]);
        });
    }
}

function adjustEncodingOptions(encodingOption: EncodingOptions): void {
    if ('Bitrate' in encodingOption) {
        adjustRangeField(encodingOption.Bitrate);
    }
    if ('FrameRate' in encodingOption) {
        adjustEnumField(encodingOption.FrameRate);
    }
    if ('GovLength' in encodingOption) {
        adjustRangeField(encodingOption.GovLength);
    }
    if ('Profiles' in encodingOption) {
        adjustEnumField(encodingOption.Profiles);
    }
    if ('Quality' in encodingOption) {
        adjustRangeField(encodingOption.Quality);
    }
    if ('Resolution' in encodingOption) {
        adjustResolution(encodingOption.Resolution);
    }
}

function adjustImageSettings(image: ImageSettings): void {
    if ('BacklightCompensationMode' in image) {
        adjustEnumField(image.BacklightCompensationMode);
    }
    if ('Brightness' in image) {
        adjustRangeField(image.Brightness);
    }
    if ('ColorSaturation' in image) {
        adjustRangeField(image.ColorSaturation);
    }
    if ('Contrast' in image) {
        adjustRangeField(image.Contrast);
    }
    if ('ExposureMode' in image) {
        adjustEnumField(image.ExposureMode);
    }
    if ('IrCutFilterMode' in image) {
        adjustEnumField(image.IrCutFilterMode);
    }
    if ('Sharpness' in image) {
        adjustRangeField(image.Sharpness);
    }
    if ('WhiteBalanceMode' in image) {
        adjustEnumField(image.WhiteBalanceMode);
    }
    if ('WideDynamicRangeMode' in image) {
        adjustEnumField(image.WideDynamicRangeMode);
    }
}

function adjustEnumField(field: EnumField): void {
    if (!field.AllowedValues.includes(field.Value)) {
        const midIndex = Math.floor(field.AllowedValues.length / 2);
        field.Value = field.AllowedValues[midIndex];
    }
}

function adjustRangeField(field: RangeField): void {
    const value = parseFloat(field.Value);
    const min = parseFloat(field.Min);
    const max = parseFloat(field.Max);

    if (isNaN(value)) {
        field.Value = Math.round((min + max) / 2).toString();
    } else if (value < min) {
        field.Value = field.Min;
    } else if (value > max) {
        field.Value = field.Max;
    } else {
        field.Value = Math.round(value).toString();
    }
}

function adjustResolution(field: { AllowedValues: Resolution[]; Value: Resolution }): void {
    const isValidResolution = field.AllowedValues.some(res => res.Height === field.Value.Height && res.Width === field.Value.Width);

    if (!isValidResolution) {
        const midIndex = Math.floor(field.AllowedValues.length / 2);
        field.Value = field.AllowedValues[midIndex];
    }
}

export const getValueSafely = (obj: unknown, ...keys: string[]): string | undefined => {
    for (const key of keys) {
        if (obj && typeof obj === 'object' && key in obj) {
            obj = (obj as Record<string, unknown>)[key];
        } else {
            return undefined;
        }
    }
    return typeof obj === 'string' ? obj : undefined;
};

export const addIfExists = <T>(obj: T, key: keyof T, value: string | undefined): void => {
    if (value !== undefined) {
        obj[key] = value as T[keyof T];
    }
};

export const getTimelineGaps = (timelines: Timeline[]) => {
    const timelineGaps: Timeline[] = [];
    if (timelines == null) {
        return timelines;
    }
    if (timelines.length === 0) {
        return timelineGaps;
    }
    for (let i = 0; i < timelines.length; i += 1) {
        const d1 = new Date(new Date(timelines[i].endTime).getTime() + 1);
        const d2 = i < timelines.length - 1 ? new Date(new Date(timelines[i + 1].startTime).getTime() - 1) : null;
        if (d2 == null) {
            break;
        }
        timelineGaps.push({
            startTime: d1.toISOString(),
            endTime: d2.toISOString(),
        });
    }
    return timelineGaps;
};

export async function getWebRTCStats(peerConnection: RTCPeerConnection): Promise<{ basicStats: WebRTCStats; fullStats: RTCStatsReport }> {
    const basicStats: WebRTCStats = {
        fps: 0,
        pli: 0,
        nack: 0,
        rtt: 0,
        packetsLost: 0,
        jitter: 0,
        bitrate: 0,
        fir: 0,
        qualityLimitationReason: undefined,
    };

    const report = await peerConnection.getStats();

    report.forEach(stat => {
        if (stat.type === 'inbound-rtp' && stat.kind === 'video') {
            basicStats.pli += stat.pliCount || 0;
            basicStats.nack += stat.nackCount || 0;
            basicStats.fir += stat.firCount || 0;
            basicStats.packetsLost += stat.packetsLost || 0;
            basicStats.jitter = stat.jitter || 0;

            if (stat.framesPerSecond) {
                basicStats.fps = stat.framesPerSecond;
            }
        }
        // Get RTT from candidate-pair stats
        if (stat.type === 'candidate-pair' && stat.currentRoundTripTime) {
            basicStats.rtt = stat.currentRoundTripTime * 1000; // Convert to milliseconds
        }
        // Get quality limitation reason
        if (stat.type === 'track' && stat.kind === 'video') {
            basicStats.qualityLimitationReason = stat.qualityLimitationReason || undefined;
        }
    });

    return {
        basicStats,
        fullStats: report,
    };
}

export function isAudioTrackPresentInPeerConnection(peerConnection: RTCPeerConnection): boolean {
    const receivers = peerConnection.getReceivers();
    const audioReceivers = receivers.filter(
        receiver => receiver.track && receiver.track.kind === 'audio' && receiver.track.readyState === 'live'
    );
    const filteredArray = audioReceivers.filter(obj => Object.keys(obj).length !== 0);
    LOG.info('audioReceivers: ', filteredArray);
    return filteredArray.length > 0;
}

/**
 * Copies text to clipboard with fallbacks for non-secure contexts (HTTP).
 * navigator.clipboard is unavailable over HTTP, so we fall back to
 * a hidden textarea + execCommand('copy').
 */
export async function copyToClipboard(text: string): Promise<void> {
    if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return;
    }

    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.left = '-9999px';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);

    const selection = document.getSelection();
    const previousRange = selection && selection.rangeCount > 0 ? selection.getRangeAt(0) : null;

    textarea.select();
    textarea.setSelectionRange(0, text.length);

    try {
        if (!document.execCommand('copy')) {
            throw new Error('execCommand copy returned false');
        }
    } finally {
        document.body.removeChild(textarea);
        if (previousRange && selection) {
            selection.removeAllRanges();
            selection.addRange(previousRange);
        }
    }
}

// Service availability check function
export const checkServiceAvailability = async (endpoint: string, servicePath: string): Promise<boolean> => {
    try {
        await nvAxios.get(`${endpoint}/api/v1/${servicePath}/version`);
        return true;
    } catch (err: unknown) {
        LOG.error(`Failed to check service availability for ${endpoint}/${servicePath}:`, err);
        const status = (err as { response?: { status?: number } }).response?.status;

        // If no status code (network error, CORS, etc.), service is unavailable
        if (status === undefined) {
            LOG.warn(`Service ${servicePath} unavailable due to network error`);
            return false;
        }

        // Return false for 404 and 502 status codes
        if (status === 404 || status === 502) {
            LOG.warn(`Service ${servicePath} unavailable with status ${status}`);
            return false;
        }

        // For other HTTP errors, consider service available (temporary issues)
        return true;
    }
};
