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
import React, { useState, useCallback, useRef, useEffect } from 'react';
import {
    Stack,
    Box,
    TextField,
    Typography,
    Switch,
    Divider,
    Tooltip,
    Paper,
    LinearProgress,
    FormControl,
    InputLabel,
    OutlinedInput,
    Grid2 as Grid,
    IconButton,
    Button,
    Chip,
} from '@mui/material';
import HelpOutlineIcon from '@mui/icons-material/HelpOutline';
import CloudUploadIcon from '@mui/icons-material/CloudUpload';
import CancelIcon from '@mui/icons-material/Cancel';
import AutorenewIcon from '@mui/icons-material/Autorenew';
import { v4 as generateUUID } from 'uuid';
import { useSnackbar } from 'notistack';
import axios, { AxiosResponse, AxiosError, CancelTokenSource } from 'axios';
import nvAxios from '../../services/Axios';
import SingleSensorSelector from '../../components/sensorSelector/SingleSensorSelector';
import useVSTUIStore from '../../services/StateManagement';
import type { Sensor } from '../../interfaces/interfaces';
import config from '../../config';
import { updateSensorsAndStreams } from '../../utils/misc/updateSensorsAndStreams';

// Types
interface UploadOptions {
    onSuccess: (response: string) => void;
    onError: (error: { error: unknown }) => void;
    onProgress: (event: { percent: number }) => void;
    file: File;
}

interface UploadHeaders {
    'content-type': string;
    'nvstreamer-chunk-number': number;
    'nvstreamer-total-chunks': number;
    'nvstreamer-is-last-chunk': boolean | string;
    'nvstreamer-identifier': string;
    'nvstreamer-file-name': string;
    'nvstreamer-enable-transcode'?: boolean;
    'transcode-framerate'?: string;
    'transcode-bitrate'?: string;
    'transcode-keyframe-interval'?: string;
    streamId?: string;
    [key: string]: string | number | boolean | undefined;
}

interface Metadata {
    eventInfo?: string;
    timestamp?: number | string;
    streamName?: string;
    tag?: string;
    sensorId?: string;
    checksum?: string;
    [key: string]: string | number | undefined;
}

const VIDEO_UPLOAD_TIMEOUT = 999999999;

const toolTipHelperText =
    'If enabled the media file will be divided into small chunks and each chunk will be uploaded separately. Enable this setting if timeout is occuring during upload.';

function hasWhiteSpace(s: string): boolean {
    return /\s/g.test(s);
}

const MediaUpload = () => {
    const { enqueueSnackbar } = useSnackbar();
    const sensors = useVSTUIStore(state => state.sensorServiceSensors);
    const vstAdaptorType = useVSTUIStore(state => state.vstAdaptorType);
    const [enableTranscode, setEnableTranscode] = useState(false);
    const [frameRate, setFrameRate] = useState('');
    const [bitrate, setBitrate] = useState('');
    const [keyframeInterval, setKeyframeInterval] = useState('');
    const [tags, setTags] = useState('');
    const [enableChunkUpload, setEnableChunkUpload] = useState(false);
    const [chunkSize, setChunkSize] = useState<number>(50);
    const [isDragging, setIsDragging] = useState(false);
    const [selectedSensor, setSelectedSensor] = useState<Sensor | null>(null);
    const [totalFiles, setTotalFiles] = useState<number>(0);
    const [uploadedFiles, setUploadedFiles] = useState<number>(0);
    const [failedFiles, setFailedFiles] = useState<string[]>([]);
    const [fileProgress, setFileProgress] = useState<{ [key: string]: number }>({});
    const [showProgress, setShowProgress] = useState(false);
    const [cancelledFiles, setCancelledFiles] = useState<string[]>([]);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const cancelTokensRef = useRef<Map<string, CancelTokenSource>>(new Map());
    const [eventInfo, setEventInfo] = useState('');
    const [timestampInput, setTimestampInput] = useState(''); // Store the raw input string
    const [streamName, setStreamName] = useState('');
    const [metadataTag, setMetadataTag] = useState('');
    const [sensorId, setSensorId] = useState('');
    const [checksum, setChecksum] = useState('');

    // Update the UploadState interface
    interface UploadState {
        enableTranscode: boolean;
        frameRate: string;
        bitrate: string;
        keyframeInterval: string;
        enableChunkUpload: boolean;
        chunkSize: number;
        eventInfo: string;
        timestampInput: string;
        streamName: string;
        metadataTag: string;
        sensorId: string;
        checksum: string;
    }

    const stateRef = useRef<UploadState>({
        enableTranscode,
        frameRate,
        bitrate,
        keyframeInterval,
        enableChunkUpload,
        chunkSize,
        eventInfo,
        timestampInput,
        streamName,
        metadataTag,
        sensorId,
        checksum,
    });

    // Update useEffect to include metadata fields
    useEffect(() => {
        stateRef.current = {
            enableTranscode,
            frameRate,
            bitrate,
            keyframeInterval,
            enableChunkUpload,
            chunkSize,
            eventInfo,
            timestampInput,
            streamName,
            metadataTag,
            sensorId,
            checksum,
        };
    }, [
        enableTranscode,
        frameRate,
        bitrate,
        keyframeInterval,
        enableChunkUpload,
        chunkSize,
        eventInfo,
        timestampInput,
        streamName,
        metadataTag,
        sensorId,
        checksum,
    ]);

    // Add debug logging for state changes
    useEffect(() => {
        console.log('State updated:', {
            eventInfo,
            timestampInput,
            streamName,
            metadataTag,
            sensorId,
            checksum,
        });
    }, [eventInfo, timestampInput, streamName, metadataTag, sensorId, checksum]);

    // Hide progress when all files are processed
    useEffect(() => {
        if (showProgress && totalFiles > 0 && uploadedFiles >= totalFiles) {
            const timer = setTimeout(() => {
                setShowProgress(false);
            }, 2000); // Keep showing for 2 seconds after completion
            return () => clearTimeout(timer);
        }
    }, [showProgress, totalFiles, uploadedFiles]);

    const handleRefresh = async () => {
        try {
            await updateSensorsAndStreams();
        } catch (error) {
            console.error('Failed to refresh sensors and streams:', error);
        }
    };

    const handleSensorSelection = useCallback((selection: Sensor | null) => {
        setSelectedSensor(selection);
    }, []);

    const handleEnableTranscode = useCallback(() => {
        console.log('Toggling transcode from:', enableTranscode, 'to:', !enableTranscode);
        setEnableTranscode(!enableTranscode);
    }, [enableTranscode]);

    const handleFrameRateChange = useCallback(
        (e: React.ChangeEvent<HTMLInputElement>) => {
            const { value } = e.target;
            console.log('Setting frame rate to:', value);
            if (value === '' || /^\d+$/.test(value)) {
                setFrameRate(value);
            } else {
                enqueueSnackbar(`Error - Framerate can only be Integer or Empty String`, {
                    variant: 'error',
                    anchorOrigin: {
                        horizontal: 'right',
                        vertical: 'bottom',
                    },
                });
            }
        },
        [enqueueSnackbar]
    );

    const handleBitRateChange = useCallback(
        (e: React.ChangeEvent<HTMLInputElement>) => {
            const { value } = e.target;
            console.log('Setting bitrate to:', value);
            if (value === '' || /^\d+$/.test(value)) {
                setBitrate(value);
            } else {
                enqueueSnackbar(`Error - Bitrate can only be Integer or Empty String`, {
                    variant: 'error',
                    anchorOrigin: {
                        horizontal: 'right',
                        vertical: 'bottom',
                    },
                });
            }
        },
        [enqueueSnackbar]
    );

    const handleKeyframeIntervalChange = useCallback(
        (e: React.ChangeEvent<HTMLInputElement>) => {
            const { value } = e.target;
            if (value === '' || /^\d+$/.test(value)) {
                setKeyframeInterval(value);
            } else {
                enqueueSnackbar(`Error - Keyframe interval can only be Integer or Empty String`, {
                    variant: 'error',
                    anchorOrigin: {
                        horizontal: 'right',
                        vertical: 'bottom',
                    },
                });
            }
        },
        [enqueueSnackbar]
    );

    const setFileTag = (fileTags: string, deviceId: string) => {
        const jsonData = {
            tags: fileTags,
        };
        nvAxios
            .post(`${config.sensorManagementEndpoint}/api/v1/sensor/${deviceId}/info`, jsonData)
            .then((res: AxiosResponse) => {
                console.log('set tag response: ', res.data);
            })
            .catch((e: AxiosError) => {
                console.debug('failed to set tags', e);
            });
    };

    const customFileUpload = useCallback((options: UploadOptions) => {
        setShowProgress(true);
        const currentState = stateRef.current;
        console.log('Processing file with state:', currentState);

        if (!currentState.enableChunkUpload) {
            console.log('Using single chunk upload for file:', options.file.name);
            uploadFileAsSingleChunk(options);
        } else {
            console.log('Using chunk upload for file:', options.file.name);
            uploadFileInChunks(options);
        }
    }, []);

    const isNumeric = (value: string): boolean => /^-?\d+$/.test(value);

    const uploadFileInChunks = async (options: UploadOptions) => {
        const handleSuccess = (
            response: AxiosResponse,
            chunkNumber: number,
            totalChunkCount: number,
            onSuccess: (response: string) => void
        ) => {
            console.log(`Chunk ${chunkNumber}/${totalChunkCount} uploaded successfully`);
            if (chunkNumber === totalChunkCount) {
                enqueueSnackbar(`Success - File upload successfully ${response.data.filename}`, {
                    variant: 'success',
                    anchorOrigin: {
                        horizontal: 'right',
                        vertical: 'bottom',
                    },
                });
                console.log('file upload response', response.data);
                onSuccess('Ok');
                handleRefresh();
            }
        };

        const handleUploadError = (
            error: AxiosError<{ error_message: string }>,
            chunkNumber: number,
            cancelToken: CancelTokenSource,
            onError: (error: { error: unknown }) => void
        ) => {
            cancelToken.cancel();
            console.error(`Error uploading chunk ${chunkNumber}:`, error);
            onError({ error });
            const errorMessage = error.response?.data?.error_message || 'File upload failed';
            enqueueSnackbar(`Error - ${errorMessage}`, {
                variant: 'error',
                anchorOrigin: { horizontal: 'right', vertical: 'bottom' },
            });
            handleRefresh();
        };

        const { onSuccess, onError, file, onProgress } = options;
        const currentState = stateRef.current;

        // Create metadata object once at the start - only include keys with values
        const metadata: Metadata = {};
        if (currentState.eventInfo && currentState.eventInfo.trim()) {
            metadata.eventInfo = currentState.eventInfo.trim();
        }
        if (currentState.timestampInput.trim()) {
            // Send the original format: number if Unix timestamp, string if ISO format
            if (/^\d+$/.test(currentState.timestampInput.trim())) {
                metadata.timestamp = Number(currentState.timestampInput.trim());
            } else {
                metadata.timestamp = currentState.timestampInput.trim();
            }
        }
        if (currentState.streamName && currentState.streamName.trim()) {
            metadata.streamName = currentState.streamName.trim();
        }
        if (currentState.metadataTag && currentState.metadataTag.trim()) {
            metadata.tag = currentState.metadataTag.trim();
        }
        if (currentState.sensorId && currentState.sensorId.trim()) {
            metadata.sensorId = currentState.sensorId.trim();
        }
        if (currentState.checksum && currentState.checksum.trim()) {
            metadata.checksum = currentState.checksum.trim();
        }

        // Metadata validation is now optional - all fields are conditionally included

        const currentChunkSize = currentState.chunkSize;
        if (!isNumeric(currentChunkSize.toString())) {
            enqueueSnackbar(`Error - chunkSize must be a valid number`, {
                variant: 'error',
                anchorOrigin: { horizontal: 'right', vertical: 'bottom' },
            });
            onError({ error: new Error('Invalid chunk size') });
            return;
        }
        if (Number(currentChunkSize) <= 10) {
            enqueueSnackbar('Error - chunkSize must be more than 10 MB', {
                variant: 'error',
                anchorOrigin: { horizontal: 'right', vertical: 'bottom' },
            });
            onError({ error: new Error('Chunk size too small') });
            return;
        }

        let chunkFailed = false;
        let chunkNumber = 0;
        const chunkSizeBytes = Number(currentChunkSize) * 1024 * 1024;
        const totalChunkCount = Math.ceil(file.size / chunkSizeBytes);
        const fileSize = file.size;
        const uniqueIdentifier = generateUUID();
        const cancelVideoUpload = axios.CancelToken.source();
        cancelTokensRef.current.set(file.name, cancelVideoUpload);

        // Process each chunk
        for (let start = 0; start < fileSize; start += chunkSizeBytes) {
            if (chunkFailed) break;
            chunkNumber += 1;
            const chunk = file.slice(start, start + chunkSizeBytes);
            const isLastChunk = chunkNumber === totalChunkCount;

            const fd = new FormData();
            fd.append('mediaFile', chunk);
            fd.append('filename', file.name);

            // Add metadata to the first chunk
            if (chunkNumber === 1) {
                // Add metadata to the first chunk
                fd.append('metadata', JSON.stringify(metadata));
            }

            interface ChunkUploadHeaders extends UploadHeaders {
                'content-type': string;
                'nvstreamer-identifier': string;
                'nvstreamer-file-name': string;
                'nvstreamer-chunk-number': number;
                'nvstreamer-total-chunks': number;
                'nvstreamer-is-last-chunk': string;
                streamId?: string;
                'nvstreamer-enable-transcode'?: boolean;
                'transcode-framerate'?: string;
                'transcode-bitrate'?: string;
                'transcode-keyframe-interval'?: string;
            }

            const uploadHeaders: ChunkUploadHeaders = {
                'content-type': 'multipart/form-data',
                'nvstreamer-identifier': uniqueIdentifier,
                'nvstreamer-file-name': file.name,
                'nvstreamer-chunk-number': chunkNumber,
                'nvstreamer-total-chunks': totalChunkCount,
                'nvstreamer-is-last-chunk': isLastChunk ? 'true' : 'false',
            };

            if (metadata.sensorId) {
                uploadHeaders.streamId = metadata.sensorId;
            }

            // Add transcode headers if enabled
            if (currentState.enableTranscode) {
                uploadHeaders['nvstreamer-enable-transcode'] = true;
                if (currentState.frameRate) {
                    uploadHeaders['transcode-framerate'] = currentState.frameRate;
                }
                if (currentState.bitrate) {
                    uploadHeaders['transcode-bitrate'] = currentState.bitrate;
                }
                if (currentState.keyframeInterval) {
                    uploadHeaders['transcode-keyframe-interval'] = currentState.keyframeInterval;
                }
            }

            try {
                const response = await nvAxios.post(`${config.storageManagementEndpoint}/api/v1/storage/file`, fd, {
                    timeout: VIDEO_UPLOAD_TIMEOUT,
                    headers: uploadHeaders,
                    cancelToken: cancelVideoUpload.token,
                    onUploadProgress: progressEvent => {
                        const progress = ((start + progressEvent.loaded) / fileSize) * 100;
                        onProgress({ percent: progress });
                    },
                });

                if (response.data && Object.prototype.hasOwnProperty.call(response.data, 'filename')) {
                    if (isLastChunk) {
                        setFileTag(tags, response.data.id);
                    }
                    handleSuccess(response, chunkNumber, totalChunkCount, onSuccess);
                }
            } catch (error: unknown) {
                if (axios.isCancel(error)) {
                    cancelTokensRef.current.delete(file.name);
                    break;
                }
                cancelTokensRef.current.delete(file.name);
                if (error instanceof AxiosError) {
                    handleUploadError(error, chunkNumber, cancelVideoUpload, onError);
                } else {
                    handleUploadError(new AxiosError('Unknown error occurred'), chunkNumber, cancelVideoUpload, onError);
                }
                chunkFailed = true;
                chunkNumber -= 1;
            }
        }
    };

    const uploadFileAsSingleChunk = useCallback(
        (options: UploadOptions) => {
            const { onSuccess, onError, file, onProgress } = options;
            const currentState = stateRef.current;

            // Create metadata object with current state values - only include keys with values
            const metadata: Metadata = {};
            if (currentState.eventInfo && currentState.eventInfo.trim()) {
                metadata.eventInfo = currentState.eventInfo.trim();
            }
            if (currentState.timestampInput.trim()) {
                // Send the original format: number if Unix timestamp, string if ISO format
                if (/^\d+$/.test(currentState.timestampInput.trim())) {
                    metadata.timestamp = Number(currentState.timestampInput.trim());
                } else {
                    metadata.timestamp = currentState.timestampInput.trim();
                }
            }
            if (currentState.streamName && currentState.streamName.trim()) {
                metadata.streamName = currentState.streamName.trim();
            }
            if (currentState.metadataTag && currentState.metadataTag.trim()) {
                metadata.tag = currentState.metadataTag.trim();
            }
            if (currentState.sensorId && currentState.sensorId.trim()) {
                metadata.sensorId = currentState.sensorId.trim();
            }
            if (currentState.checksum && currentState.checksum.trim()) {
                metadata.checksum = currentState.checksum.trim();
            }

            // Validate metadata
            // No validation needed as all fields are now optional

            const fd = new FormData();
            fd.append('mediaFile', file);
            fd.append('filename', file.name);

            fd.append('metadata', JSON.stringify(metadata));

            const setupUploadHeaders = (file: File): UploadHeaders => {
                const currentState = stateRef.current;
                const headers: UploadHeaders = {
                    'content-type': 'multipart/form-data',
                    'nvstreamer-chunk-number': 1,
                    'nvstreamer-total-chunks': 1,
                    'nvstreamer-is-last-chunk': true,
                    'nvstreamer-identifier': generateUUID(),
                    'nvstreamer-file-name': file.name,
                };

                // Add streamId header if sensor is selected
                if (selectedSensor?.sensorId) {
                    headers['streamId'] = selectedSensor.sensorId;
                }

                if (currentState.enableTranscode) {
                    headers['nvstreamer-enable-transcode'] = true;
                    if (currentState.frameRate) {
                        headers['transcode-framerate'] = currentState.frameRate;
                    }
                    if (currentState.bitrate) {
                        headers['transcode-bitrate'] = currentState.bitrate;
                    }
                    if (currentState.keyframeInterval) {
                        headers['transcode-keyframe-interval'] = currentState.keyframeInterval;
                    }
                }
                return headers;
            };

            const handleResponse = (response: AxiosResponse, onSuccess: (response: string) => void) => {
                if (response.data && Object.prototype.hasOwnProperty.call(response.data, 'filename')) {
                    onSuccess('Ok');
                    enqueueSnackbar(`Success - File upload successfully ${response.data.filename}`, {
                        variant: 'success',
                        anchorOrigin: {
                            horizontal: 'right',
                            vertical: 'bottom',
                        },
                    });
                    handleRefresh();
                }
            };

            const handleError = (error: AxiosError, onError: (error: { error: unknown }) => void) => {
                console.error('File upload error', error);
                onError({ error });
                const errorMessage =
                    error.response?.data && typeof error.response.data === 'object' && 'error_message' in error.response.data
                        ? error.response.data.error_message
                        : 'Unknown error occurred';
                enqueueSnackbar(`Error - ${errorMessage}`, {
                    variant: 'error',
                    anchorOrigin: { horizontal: 'right', vertical: 'bottom' },
                });
                handleRefresh();
            };

            const uploadHeaders = setupUploadHeaders(file);
            console.log('Single file upload headers:', uploadHeaders);

            const cancelTokenSource = axios.CancelToken.source();
            cancelTokensRef.current.set(file.name, cancelTokenSource);

            nvAxios
                .post(`${config.storageManagementEndpoint}/api/v1/storage/file`, fd, {
                    timeout: VIDEO_UPLOAD_TIMEOUT,
                    headers: uploadHeaders,
                    cancelToken: cancelTokenSource.token,
                    onUploadProgress: event => {
                        const total = event.total || 0;
                        const progress = (event.loaded / total) * 100;
                        onProgress({ percent: progress });
                    },
                })
                .then(response => {
                    cancelTokensRef.current.delete(file.name);
                    setFileTag(tags, response.data.id);
                    handleResponse(response, onSuccess);
                })
                .catch((error: unknown) => {
                    cancelTokensRef.current.delete(file.name);
                    if (axios.isCancel(error)) {
                        return;
                    }
                    if (error instanceof AxiosError) {
                        console.error('Upload error:', error);
                        console.error('Error response:', error.response?.data);
                        handleError(error, onError);
                    } else {
                        console.error('Unknown error:', error);
                        handleError(new AxiosError('Unknown error occurred'), onError);
                    }
                });
        },
        [tags, enqueueSnackbar, selectedSensor]
    );

    const handleFileDrop = useCallback(
        (e: React.DragEvent<HTMLDivElement>) => {
            e.preventDefault();
            setIsDragging(false);

            const files = Array.from(e.dataTransfer.files);
            if (files.length === 0) return;

            setTotalFiles(files.length);
            setUploadedFiles(0);
            setFailedFiles([]);
            setCancelledFiles([]);
            cancelTokensRef.current.clear();
            // Initialize progress for all files
            const initialProgress = files.reduce(
                (acc, file) => ({
                    ...acc,
                    [file.name]: 0,
                }),
                {}
            );
            setFileProgress(initialProgress);
            setShowProgress(true);

            // Get current state for all files
            const currentState = stateRef.current;
            console.log('Processing multiple files with state:', currentState);

            files.forEach(file => {
                if (hasWhiteSpace(file.name)) {
                    enqueueSnackbar(`Error - whitespaces not allowed in file name: ${file.name}`, {
                        variant: 'error',
                        anchorOrigin: {
                            horizontal: 'right',
                            vertical: 'bottom',
                        },
                    });
                    setFailedFiles(prev => [...prev, file.name]);
                    setUploadedFiles(prev => prev + 1);
                    return;
                }

                customFileUpload({
                    file,
                    onSuccess: () => {
                        setUploadedFiles(prev => prev + 1);
                        // Set progress to 100% on success
                        setFileProgress(prev => ({
                            ...prev,
                            [file.name]: 100,
                        }));
                    },
                    onError: ({ error }) => {
                        console.error('Upload error:', error);
                        setFailedFiles(prev => [...prev, file.name]);
                        setUploadedFiles(prev => prev + 1);
                        // Keep the last progress value on error
                    },
                    onProgress: ({ percent }) => {
                        setFileProgress(prev => ({
                            ...prev,
                            [file.name]: percent,
                        }));
                    },
                });
            });
        },
        [vstAdaptorType, enqueueSnackbar, totalFiles, selectedSensor]
    );

    const handleDragOver = useCallback((e: React.DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        setIsDragging(true);
    }, []);

    const handleDragLeave = useCallback((e: React.DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        setIsDragging(false);
    }, []);

    const handleFileSelect = useCallback(
        (e: React.ChangeEvent<HTMLInputElement>) => {
            const files = Array.from(e.target.files || []);
            if (files.length === 0) return;

            setTotalFiles(files.length);
            setUploadedFiles(0);
            setFailedFiles([]);
            setCancelledFiles([]);
            cancelTokensRef.current.clear();
            // Initialize progress for all files
            const initialProgress = files.reduce(
                (acc, file) => ({
                    ...acc,
                    [file.name]: 0,
                }),
                {}
            );
            setFileProgress(initialProgress);
            setShowProgress(true);

            // Get current state for all files
            const currentState = stateRef.current;
            console.log('Processing multiple files with state:', currentState);

            files.forEach(file => {
                if (hasWhiteSpace(file.name)) {
                    enqueueSnackbar(`Error - whitespaces not allowed in file name: ${file.name}`, {
                        variant: 'error',
                        anchorOrigin: {
                            horizontal: 'right',
                            vertical: 'bottom',
                        },
                    });
                    setFailedFiles(prev => [...prev, file.name]);
                    setUploadedFiles(prev => prev + 1);
                    return;
                }

                customFileUpload({
                    file,
                    onSuccess: () => {
                        setUploadedFiles(prev => prev + 1);
                        // Set progress to 100% on success
                        setFileProgress(prev => ({
                            ...prev,
                            [file.name]: 100,
                        }));
                    },
                    onError: ({ error }) => {
                        console.error('Upload error:', error);
                        setFailedFiles(prev => [...prev, file.name]);
                        setUploadedFiles(prev => prev + 1);
                        // Keep the last progress value on error
                    },
                    onProgress: ({ percent }) => {
                        setFileProgress(prev => ({
                            ...prev,
                            [file.name]: percent,
                        }));
                    },
                });
            });
        },
        [vstAdaptorType, enqueueSnackbar, totalFiles, selectedSensor]
    );

    const handleUploadAreaClick = useCallback(() => {
        if (fileInputRef.current) {
            fileInputRef.current.click();
        }
    }, []);

    const handleCancelUpload = useCallback(
        (fileName: string) => {
            const cancelToken = cancelTokensRef.current.get(fileName);
            if (cancelToken) {
                cancelToken.cancel('Upload cancelled by user');
                cancelTokensRef.current.delete(fileName);
            }
            setCancelledFiles(prev => [...prev, fileName]);
            setUploadedFiles(prev => prev + 1);
            enqueueSnackbar(`Upload cancelled: ${fileName}`, {
                variant: 'info',
                anchorOrigin: { horizontal: 'right', vertical: 'bottom' },
            });
        },
        [enqueueSnackbar]
    );

    const handleCancelAllUploads = useCallback(() => {
        const inProgressFiles = Array.from(cancelTokensRef.current.keys());
        if (inProgressFiles.length === 0) return;
        cancelTokensRef.current.forEach(cancelToken => {
            cancelToken.cancel('Upload cancelled by user');
        });
        cancelTokensRef.current.clear();
        setCancelledFiles(prev => [...prev, ...inProgressFiles]);
        setUploadedFiles(prev => prev + inProgressFiles.length);
        enqueueSnackbar('All uploads cancelled', {
            variant: 'info',
            anchorOrigin: { horizontal: 'right', vertical: 'bottom' },
        });
    }, [enqueueSnackbar]);

    // Modify form field handlers to ensure state updates
    const handleMetadataChange = (field: string, value: string) => {
        switch (field) {
            case 'eventInfo':
                setEventInfo(value);
                break;
            case 'timestamp': {
                setTimestampInput(value); // Store the raw input for API
                // We don't need to convert or validate here since we send the original format
                break;
            }
            case 'streamName':
                setStreamName(value);
                break;
            case 'tag':
                setMetadataTag(value);
                break;
            case 'sensorId':
                setSensorId(value);
                break;
            case 'checksum':
                setChecksum(value);
                break;
        }
    };

    return (
        <Box>
            <input type='file' ref={fileInputRef} onChange={handleFileSelect} multiple style={{ display: 'none' }} />
            <Stack spacing={2} sx={{ mb: 2 }}>
                {vstAdaptorType === 'streamer' && (
                    <>
                        <SingleSensorSelector
                            sensors={sensors}
                            selectedSensors={selectedSensor}
                            onChange={handleSensorSelection}
                            showSensorId={true}
                        />
                        <TextField
                            fullWidth
                            label='Tags'
                            value={tags}
                            onChange={e => {
                                setTags(e.target.value);
                            }}
                            placeholder='tag1, tag2... tagn'
                        />
                    </>
                )}
                <Divider sx={{ mb: 1 }}>
                    <Typography variant='caption' sx={{ color: 'text.secondary' }}>
                        Transcode options
                    </Typography>
                </Divider>

                <Paper sx={{ p: 2 }}>
                    <Grid container spacing={2}>
                        <Grid size={{ xs: 12 }}>
                            <Stack direction='row' alignItems='center'>
                                <Typography variant='subtitle2'>Transcode:</Typography>
                                <Stack direction='row' spacing={1} alignItems='center' sx={{ ml: 1 }}>
                                    <Switch checked={enableTranscode} onChange={handleEnableTranscode} />
                                </Stack>
                            </Stack>
                        </Grid>
                        <Grid size={{ xs: 12, sm: 6 }}>
                            <TextField
                                fullWidth
                                label='Transcode framerate'
                                value={frameRate}
                                onChange={handleFrameRateChange}
                                placeholder=''
                                type='text'
                                disabled={!enableTranscode}
                            />
                        </Grid>
                        <Grid size={{ xs: 12, sm: 6 }}>
                            <TextField
                                fullWidth
                                label='Transcode bitrate'
                                value={bitrate}
                                onChange={handleBitRateChange}
                                placeholder=''
                                type='text'
                                disabled={!enableTranscode}
                            />
                        </Grid>
                        <Grid size={{ xs: 12, sm: 6 }}>
                            <TextField
                                fullWidth
                                label='Keyframe interval'
                                value={keyframeInterval}
                                onChange={handleKeyframeIntervalChange}
                                placeholder='60'
                                type='text'
                                disabled={!enableTranscode}
                            />
                        </Grid>
                    </Grid>
                </Paper>

                <Divider sx={{ mb: 1 }}>
                    <Typography variant='caption' sx={{ color: 'text.secondary' }}>
                        Metadata Information
                    </Typography>
                </Divider>

                <Paper sx={{ p: 2 }}>
                    <Grid container spacing={2}>
                        <Grid size={{ xs: 12, sm: 6 }}>
                            <FormControl fullWidth>
                                <InputLabel>Sensor ID</InputLabel>
                                <OutlinedInput
                                    value={sensorId}
                                    onChange={e => handleMetadataChange('sensorId', e.target.value)}
                                    label='Sensor ID'
                                />
                            </FormControl>
                        </Grid>

                        <Grid size={{ xs: 12, sm: 6 }}>
                            <FormControl fullWidth>
                                <InputLabel>Timestamp (Unix ms or ISO 8601)</InputLabel>
                                <OutlinedInput
                                    value={timestampInput}
                                    onChange={e => handleMetadataChange('timestamp', e.target.value)}
                                    label='Timestamp (Unix ms or ISO 8601)'
                                    placeholder='1758024305976 or 2025-09-16T12:05:05.976Z'
                                />
                            </FormControl>
                        </Grid>

                        <Grid size={{ xs: 12, sm: 6 }}>
                            <TextField
                                label='Event Info'
                                value={eventInfo}
                                onChange={e => handleMetadataChange('eventInfo', e.target.value)}
                                fullWidth
                            />
                        </Grid>

                        <Grid size={{ xs: 12, sm: 6 }}>
                            <TextField
                                label='Stream Name'
                                value={streamName}
                                onChange={e => handleMetadataChange('streamName', e.target.value)}
                                fullWidth
                            />
                        </Grid>

                        <Grid size={{ xs: 12, sm: 6 }}>
                            <TextField
                                label='Tag'
                                value={metadataTag}
                                onChange={e => handleMetadataChange('tag', e.target.value)}
                                fullWidth
                            />
                        </Grid>

                        <Grid size={{ xs: 12, sm: 6 }}>
                            <TextField
                                label='Checksum'
                                value={checksum}
                                onChange={e => handleMetadataChange('checksum', e.target.value)}
                                fullWidth
                            />
                        </Grid>
                    </Grid>
                </Paper>

                <Divider sx={{ mb: 1 }}>
                    <Typography variant='caption' sx={{ color: 'text.secondary' }}>
                        Chunk upload
                    </Typography>
                </Divider>

                <Stack direction='row' alignItems='center'>
                    <Typography variant='subtitle2'>Enable multi-part upload:</Typography>
                    <Stack direction='row' spacing={1} alignItems='center' sx={{ ml: 1 }}>
                        <Switch
                            checked={enableChunkUpload}
                            onChange={e => {
                                console.log('Toggle changed to:', e.target.checked);
                                setEnableChunkUpload(e.target.checked);
                            }}
                        />
                        <Tooltip title={toolTipHelperText}>
                            <HelpOutlineIcon />
                        </Tooltip>
                    </Stack>
                </Stack>

                <TextField
                    fullWidth
                    label='Chunk size in MB'
                    value={chunkSize}
                    onChange={e => {
                        const value = e.target.value;
                        if (value === '' || /^\d+$/.test(value)) {
                            setChunkSize(Number(value));
                        } else {
                            enqueueSnackbar('Error - Chunk size must be a positive integer', {
                                variant: 'error',
                                anchorOrigin: {
                                    horizontal: 'right',
                                    vertical: 'bottom',
                                },
                            });
                        }
                    }}
                    placeholder=''
                    disabled={!enableChunkUpload}
                    sx={{ mt: 1 }}
                />

                <Divider sx={{ mb: 1 }}>
                    <Typography variant='caption' sx={{ color: 'text.secondary' }}>
                        Upload files
                    </Typography>
                </Divider>

                <Paper
                    sx={{
                        p: 3,
                        border: '2px dashed',
                        borderColor: isDragging ? 'primary.main' : 'grey.300',
                        bgcolor: isDragging ? 'action.hover' : 'background.paper',
                        cursor: 'pointer',
                        transition: 'all 0.2s ease-in-out',
                        '&:hover': {
                            borderColor: 'primary.main',
                            bgcolor: 'action.hover',
                        },
                    }}
                    onDrop={handleFileDrop}
                    onDragOver={handleDragOver}
                    onDragLeave={handleDragLeave}
                    onClick={handleUploadAreaClick}
                >
                    <Stack spacing={2} alignItems='center'>
                        <CloudUploadIcon sx={{ fontSize: 48, color: 'primary.main' }} />
                        <Typography variant='h6' align='center'>
                            Click or drag file to this area to upload
                        </Typography>
                        <Typography variant='body2' color='text.secondary' align='center'>
                            Single or bulk upload supported. Provide tags before uploading files.
                        </Typography>
                        {showProgress && (
                            <Box sx={{ width: '100%', mt: 2 }}>
                                <Typography variant='body2' color='text.secondary' align='center' sx={{ mb: 2 }}>
                                    Uploading {totalFiles} file
                                    {totalFiles !== 1 ? 's' : ''} ({uploadedFiles} completed)
                                </Typography>
                                {Object.entries(fileProgress).map(([fileName, progress]) => {
                                    const isFailed = failedFiles.includes(fileName);
                                    const isCancelled = cancelledFiles.includes(fileName);
                                    const isTranscoding = progress === 100 && !isFailed && !isCancelled;
                                    const isInProgress = progress < 100 && !isFailed && !isCancelled;

                                    return (
                                        <Box key={fileName} sx={{ mb: 1 }}>
                                            <Stack direction='row' alignItems='center' spacing={1} sx={{ mb: 0.5 }}>
                                                <Typography variant='body2' color='text.secondary' sx={{ flexGrow: 1 }}>
                                                    {fileName}
                                                    {!isTranscoding && `: ${Math.round(progress)}%`}
                                                    {isFailed && ' (Failed)'}
                                                    {isCancelled && ' (Cancelled)'}
                                                </Typography>
                                                {isTranscoding && (
                                                    <Chip
                                                        icon={
                                                            <AutorenewIcon
                                                                sx={{
                                                                    animation: 'spin 1.2s linear infinite',
                                                                    '@keyframes spin': {
                                                                        '0%': { transform: 'rotate(0deg)' },
                                                                        '100%': { transform: 'rotate(360deg)' },
                                                                    },
                                                                }}
                                                            />
                                                        }
                                                        label='Processing...'
                                                        size='small'
                                                        color='primary'
                                                        variant='outlined'
                                                    />
                                                )}
                                                {isInProgress && (
                                                    <Tooltip title='Cancel upload'>
                                                        <IconButton
                                                            size='small'
                                                            onClick={e => {
                                                                e.stopPropagation();
                                                                handleCancelUpload(fileName);
                                                            }}
                                                            color='error'
                                                        >
                                                            <CancelIcon fontSize='small' />
                                                        </IconButton>
                                                    </Tooltip>
                                                )}
                                            </Stack>
                                            <LinearProgress
                                                variant={isTranscoding ? 'indeterminate' : 'determinate'}
                                                value={isTranscoding ? undefined : progress}
                                                color={isFailed ? 'error' : isCancelled ? 'warning' : 'primary'}
                                                aria-label={isTranscoding ? 'Processing' : undefined}
                                            />
                                        </Box>
                                    );
                                })}
                                {Object.entries(fileProgress).filter(
                                    ([fileName, progress]) =>
                                        progress < 100 && !failedFiles.includes(fileName) && !cancelledFiles.includes(fileName)
                                ).length > 1 && (
                                    <Box sx={{ display: 'flex', justifyContent: 'center', mt: 1 }}>
                                        <Button
                                            variant='outlined'
                                            color='error'
                                            size='small'
                                            onClick={e => {
                                                e.stopPropagation();
                                                handleCancelAllUploads();
                                            }}
                                            startIcon={<CancelIcon />}
                                        >
                                            Cancel All Uploads
                                        </Button>
                                    </Box>
                                )}
                                {failedFiles.length > 0 && (
                                    <Typography variant='body2' color='error' align='center' sx={{ mt: 1 }}>
                                        Failed files: {failedFiles.join(', ')}
                                    </Typography>
                                )}
                            </Box>
                        )}
                    </Stack>
                </Paper>
            </Stack>
        </Box>
    );
};

export default MediaUpload;
