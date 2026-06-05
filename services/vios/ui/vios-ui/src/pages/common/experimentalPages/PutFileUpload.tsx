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
import React, { useState, useCallback, useRef } from 'react';
import {
    Stack,
    Box,
    Typography,
    Paper,
    LinearProgress,
    FormControl,
    InputLabel,
    OutlinedInput,
    Grid2 as Grid,
    Button,
    Card,
    CardHeader,
    CardContent,
    Alert,
    FormControlLabel,
    Checkbox,
} from '@mui/material';
import CloudUploadIcon from '@mui/icons-material/CloudUpload';
import { useSnackbar } from 'notistack';
import { AxiosError } from 'axios';
import nvAxios from '../../../services/Axios';
import config from '../../../config';

const VIDEO_UPLOAD_TIMEOUT = 120000000; // ~33 hours, effectively no timeout for large uploads

interface UploadProgress {
    fileName: string;
    progress: number;
}

interface UploadResponse {
    fileName: string;
    response: {
        status: number;
        statusText: string;
        headers: Record<string, string>;
        data: unknown;
        error?: boolean;
    };
    timestamp: string;
}

const PutFileUpload: React.FC = () => {
    const { enqueueSnackbar } = useSnackbar();
    const [sensorId, setSensorId] = useState('');
    const [timestampInput, setTimestampInput] = useState('');
    const [customFilename, setCustomFilename] = useState('');
    const [isLegacy, setIsLegacy] = useState(false);
    const [isDragging, setIsDragging] = useState(false);
    const [isUploading, setIsUploading] = useState(false);
    const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
    const [uploadComplete, setUploadComplete] = useState<boolean>(false);
    const [uploadFailed, setUploadFailed] = useState<boolean>(false);
    const [uploadResponse, setUploadResponse] = useState<UploadResponse | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

    const validateInputs = useCallback((): boolean => {
        if (!isLegacy && !sensorId.trim()) {
            enqueueSnackbar('Sensor ID is required for non-legacy PUT upload', { variant: 'error' });
            return false;
        }

        if (!timestampInput.trim()) {
            enqueueSnackbar('Timestamp is required for PUT upload', { variant: 'error' });
            return false;
        }

        return true;
    }, [isLegacy, sensorId, timestampInput, enqueueSnackbar]);

    const formatTimestamp = (input: string): string => {
        try {
            const numericTimestamp = Number(input);
            const parsedTimestamp = !Number.isNaN(numericTimestamp) && numericTimestamp > 0 ? new Date(numericTimestamp) : new Date(input);
            if (Number.isNaN(parsedTimestamp.getTime())) {
                throw new Error('Invalid timestamp');
            }
            return parsedTimestamp.toISOString();
        } catch (error) {
            throw new Error('Invalid timestamp format. Use Unix timestamp or ISO 8601');
        }
    };

    const uploadFileWithPut = useCallback(
        async (file: File): Promise<boolean> => {
            try {
                console.log('uploadFileWithPut called for:', file.name);

                if (!validateInputs()) {
                    console.log('Validation failed, aborting upload');
                    return false;
                }

                const timestamp = formatTimestamp(timestampInput);
                const fileName = customFilename.trim() || file.name;

                let url: string;
                if (isLegacy) {
                    // Legacy API: timestamp as path parameter, no sensorId
                    url = `${config.storageManagementEndpoint}/api/v1/storage/file/${fileName}/${encodeURIComponent(timestamp)}`;
                } else {
                    // Current API: timestamp and sensorId as query parameters
                    url = `${config.storageManagementEndpoint}/api/v1/storage/file/${fileName}?sensorId=${encodeURIComponent(sensorId)}&timestamp=${encodeURIComponent(timestamp)}`;
                }

                console.log(`PUT upload URL (${isLegacy ? 'Legacy' : 'Current'}):`, url);
                console.log('File details:', {
                    originalName: file.name,
                    uploadName: fileName,
                    isCustomName: !!customFilename.trim(),
                    size: file.size,
                    type: file.type,
                });

                setUploadProgress({ fileName, progress: 0 });
                setUploadComplete(false);
                setUploadFailed(false);

                const response = await nvAxios.put(url, file, {
                    timeout: VIDEO_UPLOAD_TIMEOUT,
                    headers: {
                        'Content-Type': file.type || 'application/octet-stream',
                    },
                    onUploadProgress: event => {
                        const total = event.total || 0;
                        const progress = (event.loaded / total) * 100;
                        setUploadProgress({ fileName, progress });
                    },
                });

                console.log('PUT upload successful:', response.data);
                console.log('Full response:', response);

                // Store the response data
                setUploadResponse({
                    fileName,
                    response: {
                        status: response.status,
                        statusText: response.statusText,
                        headers: response.headers as Record<string, string>,
                        data: response.data,
                    },
                    timestamp: new Date().toISOString(),
                });

                setUploadComplete(true);
                setUploadProgress({ fileName, progress: 100 });
                console.log('Upload completed successfully, states updated');
                enqueueSnackbar(`File "${fileName}" uploaded successfully`, { variant: 'success' });
                return true;
            } catch (error) {
                console.error('PUT upload error:', error);
                console.error('Error details:', {
                    message: error instanceof Error ? error.message : 'Unknown error',
                    response: error instanceof AxiosError ? error.response : null,
                    request: error instanceof AxiosError ? error.request : null,
                });

                setUploadFailed(true);
                console.log('Upload failed, states updated, ready for next upload');

                // Store error response data if available
                if (error instanceof AxiosError && error.response) {
                    const errorResponse = error.response;
                    setUploadResponse({
                        fileName: file.name,
                        response: {
                            status: errorResponse.status,
                            statusText: errorResponse.statusText,
                            headers: errorResponse.headers as Record<string, string>,
                            data: errorResponse.data,
                            error: true,
                        },
                        timestamp: new Date().toISOString(),
                    });
                }

                if (error instanceof AxiosError) {
                    const errorMsg = error.response?.data?.message || error.message;
                    enqueueSnackbar(`Upload failed for "${file.name}": ${errorMsg}`, { variant: 'error' });
                } else {
                    enqueueSnackbar(`Upload failed for "${file.name}": Unknown error`, { variant: 'error' });
                }
                return false;
            }
        },
        [sensorId, timestampInput, customFilename, isLegacy, enqueueSnackbar]
    );

    const handleFileUpload = useCallback(
        async (file: File) => {
            if (isUploading) {
                enqueueSnackbar('Upload already in progress', { variant: 'warning' });
                return;
            }

            if (!validateInputs()) {
                return;
            }

            // Clear all previous states completely and start upload
            console.log('Starting PUT upload for file:', file.name);
            console.log('Clearing all previous states...');

            setIsUploading(true);
            setUploadComplete(false);
            setUploadFailed(false);
            setUploadProgress(null);
            setUploadResponse(null);

            try {
                await uploadFileWithPut(file);
            } catch (error) {
                console.error('Upload failed:', error);
                setUploadFailed(true);
            } finally {
                setIsUploading(false);
                console.log('Upload process completed, ready for next upload');
            }
        },
        [uploadFileWithPut, validateInputs, isUploading, enqueueSnackbar]
    );

    const handleFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
        const files = event.target.files;
        if (files && files.length > 0) {
            handleFileUpload(files[0]); // Only take the first file
        }
        // Clear the input value to allow selecting the same file again
        event.target.value = '';
    };

    const handleFileDrop = useCallback(
        (event: React.DragEvent<HTMLElement>) => {
            event.preventDefault();
            setIsDragging(false);
            const files = event.dataTransfer.files;
            if (files && files.length > 0) {
                handleFileUpload(files[0]); // Only take the first file
            }
        },
        [handleFileUpload]
    );

    const handleDragOver = useCallback((event: React.DragEvent<HTMLElement>) => {
        event.preventDefault();
        setIsDragging(true);
    }, []);

    const handleDragLeave = useCallback((event: React.DragEvent<HTMLElement>) => {
        event.preventDefault();
        setIsDragging(false);
    }, []);

    const handleUploadAreaClick = () => {
        fileInputRef.current?.click();
    };

    const clearResults = () => {
        setIsUploading(false);
        setUploadComplete(false);
        setUploadFailed(false);
        setUploadProgress(null);
        setUploadResponse(null);
    };

    const clearAllFields = () => {
        setSensorId('');
        setTimestampInput('');
        setCustomFilename('');
        setIsUploading(false);
        clearResults();
    };

    return (
        <Card>
            <CardHeader
                title='PUT File Upload (Experimental)'
                subheader='Direct file upload using PUT method with support for both current and legacy API formats'
            />
            <CardContent>
                <input type='file' ref={fileInputRef} onChange={handleFileSelect} style={{ display: 'none' }} />

                <Stack spacing={3}>
                    {/* API Mode Selection */}
                    <Paper sx={{ p: 2 }}>
                        <FormControlLabel
                            control={<Checkbox checked={isLegacy} onChange={e => setIsLegacy(e.target.checked)} />}
                            label='Use Legacy API Format'
                        />
                    </Paper>

                    {/* API Information */}
                    <Alert severity='info'>
                        <Typography variant='body2'>
                            {isLegacy ? (
                                <>
                                    <strong>Legacy API:</strong> PUT /vst/api/v1/storage/file/{'<filename>'}/{'<timestamp>'}
                                    <br />
                                    <strong>Body:</strong> File binary data
                                    <br />
                                    <strong>Note:</strong> No sensorId required, timestamp is path parameter
                                </>
                            ) : (
                                <>
                                    <strong>Current API:</strong> PUT /vst/api/v1/storage/file/{'<filename>'}?sensorId={'<sensor_id>'}
                                    &timestamp={'<timestamp>'}
                                    <br />
                                    <strong>Body:</strong> File binary data
                                    <br />
                                    <strong>Note:</strong> sensorId and timestamp are query parameters
                                </>
                            )}
                            <br />
                            <strong>Filename:</strong> Use custom filename or original file name
                            <br />
                            <strong>Timestamp Format:</strong> ISO 8601 (e.g., 2025-06-15T12:30:45.000Z)
                        </Typography>
                    </Alert>

                    {/* Required Parameters */}
                    <Paper sx={{ p: 2 }}>
                        <Typography variant='h6' gutterBottom>
                            Upload Parameters
                        </Typography>
                        <Grid container spacing={2}>
                            <Grid size={{ xs: 12, sm: isLegacy ? 6 : 4 }}>
                                <FormControl fullWidth>
                                    <InputLabel>Custom Filename (Optional)</InputLabel>
                                    <OutlinedInput
                                        value={customFilename}
                                        onChange={e => setCustomFilename(e.target.value)}
                                        label='Custom Filename (Optional)'
                                        placeholder='my_custom_file.mp4'
                                    />
                                </FormControl>
                            </Grid>
                            {!isLegacy && (
                                <Grid size={{ xs: 12, sm: 4 }}>
                                    <FormControl fullWidth required>
                                        <InputLabel>Sensor ID</InputLabel>
                                        <OutlinedInput
                                            value={sensorId}
                                            onChange={e => setSensorId(e.target.value)}
                                            label='Sensor ID'
                                            placeholder='my_sensor_id'
                                        />
                                    </FormControl>
                                </Grid>
                            )}
                            <Grid size={{ xs: 12, sm: isLegacy ? 6 : 4 }}>
                                <FormControl fullWidth required>
                                    <InputLabel>Timestamp</InputLabel>
                                    <OutlinedInput
                                        value={timestampInput}
                                        onChange={e => setTimestampInput(e.target.value)}
                                        label='Timestamp'
                                        placeholder='2025-06-15T12:30:45.000Z or 1758024305976'
                                    />
                                </FormControl>
                            </Grid>
                        </Grid>
                        <Box sx={{ mt: 2 }}>
                            <Alert severity='info'>
                                <Typography variant='body2'>
                                    <strong>Custom Filename:</strong> If provided, this will be used instead of the original file name.
                                    Leave empty to use the original filename from the selected file.
                                </Typography>
                            </Alert>
                            {isLegacy && (
                                <Alert severity='warning' sx={{ mt: 1 }}>
                                    <Typography variant='body2'>
                                        <strong>Legacy Mode:</strong> Sensor ID is not required. Only timestamp is needed as a path
                                        parameter.
                                    </Typography>
                                </Alert>
                            )}
                        </Box>
                    </Paper>

                    {/* Upload Area */}
                    <Paper
                        sx={{
                            p: 3,
                            border: '2px dashed',
                            borderColor: isDragging ? 'primary.main' : isUploading ? 'warning.main' : 'grey.300',
                            bgcolor: isDragging ? 'action.hover' : isUploading ? 'warning.light' : 'background.paper',
                            cursor: isUploading ? 'not-allowed' : 'pointer',
                            transition: 'all 0.2s ease-in-out',
                            opacity: isUploading ? 0.7 : 1,
                            '&:hover': !isUploading
                                ? {
                                      borderColor: 'primary.main',
                                      bgcolor: 'action.hover',
                                  }
                                : {},
                        }}
                        onDrop={!isUploading ? handleFileDrop : undefined}
                        onDragOver={!isUploading ? handleDragOver : undefined}
                        onDragLeave={!isUploading ? handleDragLeave : undefined}
                        onClick={!isUploading ? handleUploadAreaClick : undefined}
                    >
                        <Stack spacing={2} alignItems='center'>
                            <CloudUploadIcon
                                sx={{
                                    fontSize: 48,
                                    color: isUploading ? 'warning.main' : 'primary.main',
                                }}
                            />
                            <Typography variant='h6' align='center'>
                                {isUploading ? 'Upload in progress...' : 'Click or drag a file to upload'}
                            </Typography>
                            <Typography variant='body2' color='text.secondary' align='center'>
                                {isUploading
                                    ? 'Please wait while the file is being uploaded'
                                    : `Single file upload using PUT method (${isLegacy ? 'Legacy' : 'Current'} format)`}
                            </Typography>
                        </Stack>
                    </Paper>

                    {/* Upload Progress */}
                    {uploadProgress && (
                        <Paper sx={{ p: 2 }}>
                            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
                                <Typography variant='h6'>Upload Progress</Typography>
                                <Box sx={{ display: 'flex', gap: 1 }}>
                                    <Button onClick={clearResults} size='small'>
                                        Clear Results
                                    </Button>
                                    <Button onClick={clearAllFields} size='small' variant='outlined'>
                                        Clear All
                                    </Button>
                                </Box>
                            </Box>
                            <Box sx={{ mb: 2 }}>
                                <Typography variant='body2' sx={{ mb: 0.5 }}>
                                    {uploadProgress.fileName}: {Math.round(uploadProgress.progress)}%{uploadComplete && ' ✓ Completed'}
                                    {uploadFailed && ' ✗ Failed'}
                                </Typography>
                                <LinearProgress
                                    variant='determinate'
                                    value={uploadProgress.progress}
                                    color={uploadFailed ? 'error' : uploadComplete ? 'success' : 'primary'}
                                />
                            </Box>
                        </Paper>
                    )}

                    {/* Results Summary */}
                    {(uploadComplete || uploadFailed) && (
                        <Paper sx={{ p: 2 }}>
                            <Typography variant='h6' gutterBottom>
                                Upload Summary
                            </Typography>
                            {uploadComplete && <Alert severity='success'>Successfully uploaded file: {uploadProgress?.fileName}</Alert>}
                            {uploadFailed && <Alert severity='error'>Failed to upload file: {uploadProgress?.fileName}</Alert>}
                        </Paper>
                    )}

                    {/* Response Data */}
                    {uploadResponse && (
                        <Paper sx={{ p: 2 }}>
                            <Typography variant='h6' gutterBottom>
                                Response Data
                            </Typography>
                            <Box sx={{ p: 2, border: '1px solid', borderColor: 'divider', borderRadius: 1 }}>
                                <Typography variant='subtitle2' sx={{ mb: 1, fontWeight: 600 }}>
                                    File: {uploadResponse.fileName}
                                </Typography>
                                <Typography variant='caption' color='text.secondary' sx={{ mb: 2, display: 'block' }}>
                                    Uploaded at: {new Date(uploadResponse.timestamp).toLocaleString()}
                                </Typography>

                                <Box sx={{ mb: 2 }}>
                                    <Typography variant='body2' sx={{ fontWeight: 500 }}>
                                        Status:{' '}
                                        <span
                                            style={{
                                                color:
                                                    uploadResponse.response.status >= 200 && uploadResponse.response.status < 300
                                                        ? 'green'
                                                        : 'red',
                                            }}
                                        >
                                            {uploadResponse.response.status} {uploadResponse.response.statusText}
                                        </span>
                                        {uploadResponse.response.error && (
                                            <span style={{ color: 'red', marginLeft: '8px' }}>❌ FAILED</span>
                                        )}
                                    </Typography>
                                </Box>

                                <Box sx={{ mb: 2 }}>
                                    <Typography variant='body2' sx={{ fontWeight: 500, mb: 1 }}>
                                        Response Headers:
                                    </Typography>
                                    <Paper
                                        sx={{
                                            p: 2,
                                            bgcolor: 'background.default',
                                            maxHeight: 150,
                                            overflow: 'auto',
                                            border: '1px solid',
                                            borderColor: 'divider',
                                            '&::-webkit-scrollbar': {
                                                width: 6,
                                            },
                                            '&::-webkit-scrollbar-track': {
                                                backgroundColor: 'action.hover',
                                            },
                                            '&::-webkit-scrollbar-thumb': {
                                                backgroundColor: 'action.disabled',
                                                borderRadius: 3,
                                            },
                                        }}
                                    >
                                        <Typography
                                            variant='caption'
                                            component='pre'
                                            sx={{
                                                fontSize: '0.75rem',
                                                fontFamily: 'monospace',
                                                color: 'text.primary',
                                                whiteSpace: 'pre-wrap',
                                            }}
                                        >
                                            {JSON.stringify(uploadResponse.response.headers, null, 2)}
                                        </Typography>
                                    </Paper>
                                </Box>

                                <Box>
                                    <Typography variant='body2' sx={{ fontWeight: 500, mb: 1 }}>
                                        Response Body:
                                    </Typography>
                                    <Paper
                                        sx={{
                                            p: 2,
                                            bgcolor: 'background.default',
                                            maxHeight: 200,
                                            overflow: 'auto',
                                            border: '1px solid',
                                            borderColor: 'divider',
                                            '&::-webkit-scrollbar': {
                                                width: 6,
                                            },
                                            '&::-webkit-scrollbar-track': {
                                                backgroundColor: 'action.hover',
                                            },
                                            '&::-webkit-scrollbar-thumb': {
                                                backgroundColor: 'action.disabled',
                                                borderRadius: 3,
                                            },
                                        }}
                                    >
                                        <Typography
                                            variant='caption'
                                            component='pre'
                                            sx={{
                                                fontSize: '0.75rem',
                                                fontFamily: 'monospace',
                                                color: 'text.primary',
                                                whiteSpace: 'pre-wrap',
                                            }}
                                        >
                                            {JSON.stringify(uploadResponse.response.data, null, 2)}
                                        </Typography>
                                    </Paper>
                                </Box>
                            </Box>
                        </Paper>
                    )}
                </Stack>
            </CardContent>
        </Card>
    );
};

export default PutFileUpload;
