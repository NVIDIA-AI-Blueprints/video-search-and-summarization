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
import React, { useState, useEffect } from 'react';
import { Grid2 as Grid, Card, CardContent, Typography, CardActions, Button, Alert, Skeleton } from '@mui/material';
import { useSnackbar } from 'notistack';
import {
    CameraSettings,
    CameraProfile,
    ImageSettings,
    EncodingOptions,
    EnumField,
    RangeField,
    EncodeAndImageValues,
    ImageValues,
    EncodeValues,
    Sensor,
} from '../../interfaces/interfaces';
import { RangeFieldComponent, EnumFieldComponent, ResolutionFieldComponent } from '../sensorSettingsFormFields/SensorSettingsFormFields';
import { addIfExists, adjustCameraSettings, parseSettings } from '../../utils/misc/utils';
import nvAxios from '../../services/Axios';
import LOG from '../../utils/misc/Logger';
import config from '../../config';

interface CameraSettingsFormProps {
    sensor: Sensor | null;
}

const CameraSettingsForm: React.FC<CameraSettingsFormProps> = ({ sensor }) => {
    const [settings, setSettings] = useState<CameraSettings | null>(null);
    const [loading, setLoading] = useState<boolean>(true);
    const { enqueueSnackbar } = useSnackbar();

    useEffect(() => {
        if (sensor) {
            const updateSensorSettings = () => {
                setLoading(true);
                nvAxios
                    .get(`${config.sensorManagementEndpoint}/api/v1/sensor/${sensor?.sensorId}/settings`)
                    .then(response => {
                        if (response.data) {
                            const adjustedSettings = adjustCameraSettings(response.data as CameraSettings);
                            const parsedSettings = parseSettings(adjustedSettings);
                            setSettings(parsedSettings);
                        } else {
                            setSettings(null);
                        }
                    })
                    .catch(() => {
                        LOG.error(`Failed to get sensor settings for ${sensor?.name}`);
                        setSettings(null);
                    })
                    .finally(() => {
                        setLoading(false);
                    });
            };
            updateSensorSettings();
        } else {
            setSettings(null);
            setLoading(false);
        }
    }, [sensor]);

    const getValue = (obj: unknown, path: string): string | undefined => {
        const parts = path.split('.');
        let current: unknown = obj;
        for (const part of parts) {
            if (current && typeof current === 'object' && part in current) {
                current = (current as Record<string, unknown>)[part];
            } else {
                return undefined;
            }
        }
        return typeof current === 'string' ? current : undefined;
    };

    const getEncodingOptions = (profile: CameraProfile, encodingType: string) => {
        if (profile && profile.Encode && Array.isArray(profile.Encode.Options)) {
            for (const option of profile.Encode.Options) {
                if (option[encodingType]) {
                    return option[encodingType];
                }
            }
        }
        return null; // Return null if the encoding type is not found
    };

    const handleSubmit = () => {
        if (!settings) {
            return;
        }
        const profile = Object.values(settings)[0]; // Assuming there's only one
        const payload: EncodeAndImageValues = {};
        // Encode section
        const encode: EncodeValues = {};
        const selectedEncoding = getValue(profile, 'Encode.Encoding.Value');
        if (selectedEncoding) {
            encode.Encoding = selectedEncoding;
            const optionsPath = getEncodingOptions(profile, selectedEncoding);
            addIfExists(encode, 'Bitrate', optionsPath?.Bitrate?.Value);
            addIfExists(encode, 'FrameRate', optionsPath?.FrameRate?.Value);
            addIfExists(encode, 'GovLength', optionsPath?.GovLength?.Value);
            addIfExists(encode, 'Profiles', optionsPath?.Profiles?.Value);
            addIfExists(encode, 'Quality', optionsPath?.Quality?.Value);

            const height = optionsPath?.Resolution?.Value?.Height;
            const width = optionsPath?.Resolution?.Value?.Width;
            if (height && width) {
                encode.Resolution = { Height: height, Width: width };
            }
        }

        if (Object.keys(encode).length > 0) {
            payload.Encode = encode;
        }

        // Image section
        const image: ImageValues = {};
        addIfExists(image, 'BacklightCompensationMode', profile?.Image?.BacklightCompensationMode?.Value);
        addIfExists(image, 'Brightness', profile?.Image?.Brightness?.Value);
        addIfExists(image, 'ColorSaturation', profile?.Image?.ColorSaturation?.Value);
        addIfExists(image, 'Contrast', profile?.Image?.Contrast?.Value);
        addIfExists(image, 'ExposureMode', profile?.Image?.ExposureMode?.Value);
        addIfExists(image, 'IrCutFilterMode', profile?.Image?.IrCutFilterMode?.Value);
        addIfExists(image, 'Sharpness', profile?.Image?.Sharpness?.Value);
        addIfExists(image, 'WhiteBalanceMode', profile?.Image?.WhiteBalanceMode?.Value);
        addIfExists(image, 'WideDynamicRangeMode', profile?.Image?.WideDynamicRangeMode?.Value);

        if (Object.keys(image).length > 0) {
            payload.Image = image;
        }

        nvAxios
            .post(`${config.sensorManagementEndpoint}/api/v1/sensor/${sensor?.sensorId}/settings`, payload, {
                headers: { streamId: sensor?.sensorId },
            })
            .then(() => {
                enqueueSnackbar('Settings updated successfully', { variant: 'success' });
            })
            .catch(() => {
                LOG.error(`Failed to set sensor settings for ${sensor?.name}`);
                enqueueSnackbar('Failed to update settings', { variant: 'error' });
            });
    };

    const handleSettingChange = (profileKey: string, section: keyof CameraProfile, field: string, value: string) => {
        setSettings(prevSettings => {
            if (!prevSettings) return null;
            const newSettings: CameraSettings = JSON.parse(JSON.stringify(prevSettings));
            const profile = newSettings[profileKey];

            if (section === 'Encode') {
                const encodeSettings = profile.Encode;
                if (field === 'Encoding') {
                    encodeSettings.Encoding.Value = value;
                    encodeSettings.Options = encodeSettings.Options.map(opt => {
                        const encodingType = Object.keys(opt)[0] as keyof typeof opt;
                        const newOpt = { ...opt };
                        newOpt[encodingType] = {
                            ...newOpt[encodingType],
                            isActive: encodingType === value,
                        };
                        return newOpt;
                    });
                } else {
                    const currentEncoding = encodeSettings.Encoding.Value;
                    const options = encodeSettings.Options.find((opt): opt is { [key: string]: EncodingOptions } => currentEncoding in opt);
                    if (options) {
                        const encodingOptions = options[currentEncoding];
                        if (field in encodingOptions) {
                            const fieldOptions = encodingOptions[field as keyof EncodingOptions];
                            if (fieldOptions && typeof fieldOptions === 'object') {
                                if ('Max' in fieldOptions) {
                                    // It's a RangeField
                                    (fieldOptions as RangeField).Value = value;
                                } else if ('AllowedValues' in fieldOptions) {
                                    // It's an EnumField
                                    (fieldOptions as EnumField).Value = value;
                                }
                            }
                        }
                    }
                }
            } else if (section === 'Image') {
                const imageSettings = profile.Image;
                if (field in imageSettings) {
                    const fieldOptions = imageSettings[field as keyof ImageSettings];
                    if ('Max' in fieldOptions) {
                        // It's a RangeField
                        (fieldOptions as RangeField).Value = value;
                    } else if ('AllowedValues' in fieldOptions) {
                        // It's an EnumField
                        (fieldOptions as EnumField).Value = value;
                    }
                }
            }

            console.log('newSettings: ', newSettings);
            return newSettings;
        });
    };

    if (loading) {
        return (
            <Grid container spacing={2}>
                <Grid size={{ xs: 12 }}>
                    <Card>
                        <CardContent>
                            <Typography variant='h5' gutterBottom>
                                <Skeleton width='40%' />
                            </Typography>
                            <Grid container spacing={2}>
                                <Grid size={{ xs: 12, md: 6 }}>
                                    <Typography variant='h6'>
                                        <Skeleton width='30%' />
                                    </Typography>
                                    <Skeleton height={56} sx={{ mb: 2 }} />
                                    <Skeleton height={56} sx={{ mb: 2 }} />
                                    <Skeleton height={56} sx={{ mb: 2 }} />
                                    <Skeleton height={56} sx={{ mb: 2 }} />
                                </Grid>
                                <Grid size={{ xs: 12, md: 6 }}>
                                    <Typography variant='h6'>
                                        <Skeleton width='30%' />
                                    </Typography>
                                    <Skeleton height={56} sx={{ mb: 2 }} />
                                    <Skeleton height={56} sx={{ mb: 2 }} />
                                    <Skeleton height={56} sx={{ mb: 2 }} />
                                    <Skeleton height={56} sx={{ mb: 2 }} />
                                </Grid>
                            </Grid>
                        </CardContent>
                        <CardActions>
                            <Skeleton width={100} height={36} />
                        </CardActions>
                    </Card>
                </Grid>
            </Grid>
        );
    }

    if (!settings) {
        return (
            <Grid container spacing={2}>
                <Grid size={{ xs: 12 }}>
                    <Alert severity='warning'>
                        No settings available for this sensor. Sensor settings are only available for ONVIF enabled sensors.
                    </Alert>
                </Grid>
            </Grid>
        );
    }

    return (
        <Grid container spacing={2}>
            {Object.entries(settings).map(([profileKey, profile], index) => (
                <Grid size={{ xs: 12 }} key={profileKey}>
                    <Card>
                        <CardContent>
                            <Typography variant='h5' gutterBottom>
                                <b>{index === 0 ? 'Main Stream' : `Sub Stream ${index}`}</b>
                            </Typography>
                            <Grid container spacing={2}>
                                <Grid size={{ xs: 12, md: 6 }}>
                                    <Typography variant='h6'>Encode Settings</Typography>
                                    {profile.Encode && profile.Encode.Encoding && (
                                        <EnumFieldComponent
                                            disabled={index !== 0}
                                            field={profile.Encode.Encoding}
                                            label='Encoding'
                                            onChange={value => handleSettingChange(profileKey, 'Encode', 'Encoding', value)}
                                        />
                                    )}

                                    {profile.Encode.Options.map(option => {
                                        const encoding = Object.keys(option)[0];
                                        const encodingOptions = option[encoding];
                                        if (encoding === profile.Encode.Encoding.Value) {
                                            return (
                                                <React.Fragment key={encoding}>
                                                    {encodingOptions.Bitrate && (
                                                        <RangeFieldComponent
                                                            disabled={index !== 0}
                                                            field={encodingOptions.Bitrate}
                                                            label='Bitrate'
                                                            onChange={value => handleSettingChange(profileKey, 'Encode', 'Bitrate', value)}
                                                        />
                                                    )}
                                                    {encodingOptions.FrameRate && (
                                                        <EnumFieldComponent
                                                            disabled={index !== 0}
                                                            field={encodingOptions.FrameRate}
                                                            label='Frame Rate'
                                                            onChange={value =>
                                                                handleSettingChange(profileKey, 'Encode', 'FrameRate', value)
                                                            }
                                                        />
                                                    )}
                                                    {encodingOptions.GovLength && (
                                                        <RangeFieldComponent
                                                            disabled={index !== 0}
                                                            field={encodingOptions.GovLength}
                                                            label='GOV Length'
                                                            onChange={value =>
                                                                handleSettingChange(profileKey, 'Encode', 'GovLength', value)
                                                            }
                                                        />
                                                    )}
                                                    {encodingOptions.Profiles && (
                                                        <EnumFieldComponent
                                                            disabled={index !== 0}
                                                            field={encodingOptions.Profiles}
                                                            label='Profile'
                                                            onChange={value => handleSettingChange(profileKey, 'Encode', 'Profiles', value)}
                                                        />
                                                    )}
                                                    {encodingOptions.Quality && (
                                                        <RangeFieldComponent
                                                            disabled={index !== 0}
                                                            field={encodingOptions.Quality}
                                                            label='Quality'
                                                            onChange={value => handleSettingChange(profileKey, 'Encode', 'Quality', value)}
                                                        />
                                                    )}
                                                    {encodingOptions.Resolution && (
                                                        <ResolutionFieldComponent
                                                            disabled={index !== 0}
                                                            field={encodingOptions.Resolution}
                                                            onChange={value =>
                                                                handleSettingChange(
                                                                    profileKey,
                                                                    'Encode',
                                                                    'Resolution',
                                                                    JSON.stringify(value)
                                                                )
                                                            }
                                                        />
                                                    )}
                                                </React.Fragment>
                                            );
                                        }
                                        return null;
                                    })}
                                </Grid>
                                <Grid size={{ xs: 12, md: 6 }}>
                                    <Typography variant='h6'>Image Settings</Typography>
                                    {profile?.Image?.BacklightCompensationMode && (
                                        <EnumFieldComponent
                                            disabled={index !== 0}
                                            field={profile.Image.BacklightCompensationMode}
                                            label='Backlight Compensation Mode'
                                            onChange={value => handleSettingChange(profileKey, 'Image', 'BacklightCompensationMode', value)}
                                        />
                                    )}
                                    {profile?.Image?.Brightness && (
                                        <RangeFieldComponent
                                            disabled={index !== 0}
                                            field={profile.Image.Brightness}
                                            label='Brightness'
                                            onChange={value => handleSettingChange(profileKey, 'Image', 'Brightness', value)}
                                        />
                                    )}
                                    {profile?.Image?.ColorSaturation && (
                                        <RangeFieldComponent
                                            disabled={index !== 0}
                                            field={profile.Image.ColorSaturation}
                                            label='Color Saturation'
                                            onChange={value => handleSettingChange(profileKey, 'Image', 'ColorSaturation', value)}
                                        />
                                    )}
                                    {profile?.Image?.Contrast && (
                                        <RangeFieldComponent
                                            disabled={index !== 0}
                                            field={profile.Image.Contrast}
                                            label='Contrast'
                                            onChange={value => handleSettingChange(profileKey, 'Image', 'Contrast', value)}
                                        />
                                    )}
                                    {profile?.Image?.ExposureMode && (
                                        <EnumFieldComponent
                                            disabled={index !== 0}
                                            field={profile.Image.ExposureMode}
                                            label='Exposure Mode'
                                            onChange={value => handleSettingChange(profileKey, 'Image', 'ExposureMode', value)}
                                        />
                                    )}
                                    {profile?.Image?.IrCutFilterMode && (
                                        <EnumFieldComponent
                                            disabled={index !== 0}
                                            field={profile.Image.IrCutFilterMode}
                                            label='IR Cut Filter Mode'
                                            onChange={value => handleSettingChange(profileKey, 'Image', 'IrCutFilterMode', value)}
                                        />
                                    )}
                                    {profile?.Image?.Sharpness && (
                                        <RangeFieldComponent
                                            disabled={index !== 0}
                                            field={profile.Image.Sharpness}
                                            label='Sharpness'
                                            onChange={value => handleSettingChange(profileKey, 'Image', 'Sharpness', value)}
                                        />
                                    )}
                                    {profile?.Image?.WhiteBalanceMode && (
                                        <EnumFieldComponent
                                            disabled={index !== 0}
                                            field={profile.Image.WhiteBalanceMode}
                                            label='White Balance Mode'
                                            onChange={value => handleSettingChange(profileKey, 'Image', 'WhiteBalanceMode', value)}
                                        />
                                    )}
                                    {profile?.Image?.WideDynamicRangeMode && (
                                        <EnumFieldComponent
                                            disabled={index !== 0}
                                            field={profile.Image.WideDynamicRangeMode}
                                            label='Wide Dynamic Range Mode'
                                            onChange={value => handleSettingChange(profileKey, 'Image', 'WideDynamicRangeMode', value)}
                                        />
                                    )}
                                </Grid>
                            </Grid>
                        </CardContent>
                        {index === 0 && (
                            <CardActions>
                                <Button variant='contained' onClick={handleSubmit} disabled={sensor == null}>
                                    Submit
                                </Button>
                            </CardActions>
                        )}
                    </Card>
                </Grid>
            ))}
        </Grid>
    );
};

export default CameraSettingsForm;
