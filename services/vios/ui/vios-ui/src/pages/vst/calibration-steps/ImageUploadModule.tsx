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
import React, { useRef } from 'react';
import { Dialog, DialogTitle, DialogContent, DialogActions, Button, Box, Typography, LinearProgress, Paper } from '@mui/material';
import { CloudUpload, Upload } from '@mui/icons-material';
import { Sensor } from './types';

interface ImageUploadDialogProps {
    open: boolean;
    sensor: Sensor | null;
    uploadProgress: number;
    onClose: () => void;
    onFileSelect: (file: File, sensor: Sensor) => void;
}

const ImageUploadDialog: React.FC<ImageUploadDialogProps> = ({ open, sensor, uploadProgress, onClose, onFileSelect }) => {
    const fileInputRef = useRef<HTMLInputElement>(null);

    const handleFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
        const file = event.target.files?.[0];
        if (!file || !sensor) return;

        onFileSelect(file, sensor);

        // Clear file input
        if (fileInputRef.current) {
            fileInputRef.current.value = '';
        }
    };

    return (
        <Dialog open={open} onClose={onClose} maxWidth='sm' fullWidth>
            <DialogTitle>
                <Box
                    sx={{
                        display: 'flex',
                        alignItems: 'center',
                        bgcolor: 'primary.main',
                        color: 'primary.contrastText',
                        m: -3,
                        mb: 2,
                        p: 3,
                        borderRadius: '4px 4px 0 0',
                    }}
                >
                    <CloudUpload sx={{ mr: 2, fontSize: 28 }} />
                    <Box>
                        <Typography variant='h6' fontWeight='bold'>
                            Upload Calibration Image
                        </Typography>
                        <Typography variant='body2' sx={{ opacity: 0.9 }}>
                            Add a new calibration image for your sensor
                        </Typography>
                    </Box>
                </Box>
            </DialogTitle>
            <DialogContent sx={{ pt: 1 }}>
                {uploadProgress > 0 && (
                    <Paper elevation={2} sx={{ p: 2, mb: 3, bgcolor: 'background.default' }}>
                        <Typography variant='body2' color='text.secondary' gutterBottom sx={{ fontWeight: 'medium' }}>
                            Upload Progress: {uploadProgress}%
                        </Typography>
                        <LinearProgress
                            variant='determinate'
                            value={uploadProgress}
                            sx={{
                                height: 8,
                                borderRadius: 4,
                                bgcolor: 'action.hover',
                                '& .MuiLinearProgress-bar': {
                                    borderRadius: 4,
                                },
                            }}
                        />
                    </Paper>
                )}

                <Box
                    sx={{
                        textAlign: 'center',
                        p: 3,
                        border: '2px dashed',
                        borderColor: 'divider',
                        borderRadius: 2,
                        bgcolor: 'action.hover',
                        mb: 2,
                    }}
                >
                    <Typography variant='h6' gutterBottom color='text.primary' sx={{ fontWeight: 'medium' }}>
                        Sensor ID: {sensor?.sensorId}
                    </Typography>
                    <Typography variant='body2' color='text.secondary' sx={{ mb: 3 }}>
                        Select a calibration image file to upload
                    </Typography>

                    <Button
                        variant='contained'
                        component='label'
                        startIcon={<Upload />}
                        size='large'
                        sx={{
                            minWidth: 180,
                            py: 1.5,
                            borderRadius: 2,
                            boxShadow: 2,
                            '&:hover': {
                                boxShadow: 4,
                            },
                        }}
                    >
                        Choose Image File
                        <input ref={fileInputRef} type='file' accept='image/*' onChange={handleFileSelect} style={{ display: 'none' }} />
                    </Button>

                    {fileInputRef.current?.files?.[0] && (
                        <Box
                            sx={{
                                mt: 2,
                                p: 2,
                                bgcolor: 'success.light',
                                borderRadius: 1,
                                border: '1px solid',
                                borderColor: 'success.main',
                            }}
                        >
                            <Typography variant='body2' color='success.contrastText' sx={{ fontWeight: 'medium' }}>
                                ✓ Selected: {fileInputRef.current.files[0].name}
                            </Typography>
                        </Box>
                    )}
                </Box>

                <Typography variant='caption' color='text.secondary' sx={{ display: 'block', textAlign: 'center' }}>
                    Supported formats: JPG, PNG, GIF • Maximum size: 10MB
                </Typography>
            </DialogContent>
            <DialogActions sx={{ p: 3, pt: 1, gap: 2 }}>
                <Button variant='outlined' color='inherit' onClick={onClose} sx={{ minWidth: 100 }}>
                    Cancel
                </Button>
            </DialogActions>
        </Dialog>
    );
};

export { ImageUploadDialog };
