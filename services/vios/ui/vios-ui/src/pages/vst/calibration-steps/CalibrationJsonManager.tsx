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
import React, { useState } from 'react';
import {
    Box,
    Button,
    Typography,
    Card,
    CardContent,
    Dialog,
    DialogTitle,
    DialogContent,
    DialogActions,
    TextField,
    IconButton,
    Alert,
    Paper,
    Grid2,
    Divider,
    Accordion,
    AccordionSummary,
    AccordionDetails,
} from '@mui/material';
import {
    Download as DownloadIcon,
    Upload as UploadIcon,
    Close as CloseIcon,
    FileUpload as FileUploadIcon,
    ContentPaste as ContentPasteIcon,
    ExpandMore as ExpandMoreIcon,
} from '@mui/icons-material';
import { Project, Sensor } from './types';
import { validateCalibrationJson, processImportedCalibrationJson, generateImportPreview } from './utils/CalibrationJsonUtils';

interface CalibrationJsonManagerProps {
    project: Project | null;
    selectedSensorId: string | null;
    onJsonImported?: (jsonData: CalibrationJson) => void;
}

interface CalibrationJson {
    version: string;
    osmURL: string;
    calibrationType: 'image' | 'cartesian';
    sensors: CalibrationSensor[];
    corridors: Corridor[];
}

interface Corridor {
    directions: string[];
    length: number;
    name: string;
    sensors: string[];
}

interface CalibrationSensor {
    id: string;
    type: 'camera';
    imageCoordinates: { x: number; y: number }[];
    globalCoordinates: { x: number; y: number }[];
    coordinates: { x: number; y: number };
    origin: { lat: number; lng: number };
    geoLocation: { lat: number; lng: number };
    scaleFactor: number;
    attributes: SensorAttribute[];
    rois: ROI[];
    place: Place[];
    tripwires: Tripwire[];
}

interface Place {
    name: string;
    value: string;
}

interface SensorAttribute {
    name: string;
    value: string | number;
}

interface ROI {
    id: string;
    roiCoordinates: { x: number; y: number }[];
}

interface Tripwire {
    id: string;
    tripwireCoordinates: { x: number; y: number }[];
    direction: string;
}

const CalibrationJsonManager: React.FC<CalibrationJsonManagerProps> = ({ project, selectedSensorId, onJsonImported }) => {
    const [exportDialogOpen, setExportDialogOpen] = useState(false);
    const [importDialogOpen, setImportDialogOpen] = useState(false);
    const [importJsonText, setImportJsonText] = useState('');
    const [importMode, setImportMode] = useState<'paste' | 'file'>('paste');
    const [error, setError] = useState<string | null>(null);
    const [success, setSuccess] = useState<string | null>(null);
    const [importPreview, setImportPreview] = useState<string | null>(null);

    const selectedSensor = project?.sensor_set.find(s => s.id === selectedSensorId);

    // Convert lat/lng points to x/y coordinates
    const convertLatLngToXY = (points: { lat: number; lng: number }[]): { x: number; y: number }[] => {
        return points.map(point => ({
            x: point.lng,
            y: point.lat,
        }));
    };

    // Generate sensor attributes matching React UI's getAttributes function
    const getSensorAttributes = (sensor: Sensor): SensorAttribute[] => {
        return [
            { name: 'fps', value: sensor.fps || '' },
            { name: 'depth', value: sensor.depth || '' },
            { name: 'fieldOfView', value: sensor.fieldOfView || '' },
            { name: 'direction', value: sensor.direction || '' },
            { name: 'source', value: sensor.mmsInfo_type || '' },
            { name: 'frameWidth', value: sensor.width?.toString() || '0' },
            { name: 'frameHeight', value: sensor.height?.toString() || '0' },
        ];
    };

    // Generate image calibration JSON data
    const generateImageCalibrationData = (sensor: Sensor): Omit<CalibrationSensor, 'id' | 'type'> => {
        // For image calibration, we don't use transformed coordinates
        const imageCoordinates: { x: number; y: number }[] = [];
        const globalCoordinates: { x: number; y: number }[] = [];

        // Parse ROIs
        const rois: ROI[] = [];
        try {
            const roiPolygons = JSON.parse(sensor.roiPolygon || '[]') as Array<{ points?: { lat: number; lng: number }[] }>;
            roiPolygons.forEach((roi, index: number) => {
                if (roi.points && Array.isArray(roi.points)) {
                    rois.push({
                        id: `roi-id-${index + 1}`,
                        roiCoordinates: convertLatLngToXY(roi.points),
                    });
                }
            });
        } catch (e) {
            console.warn('Failed to parse ROI polygon:', e);
        }

        // Generate place array (simplified for now)
        const place: Place[] = [];
        // Add places based on sensor's place_set, intersection_set, etc.
        if (sensor.intersection_set) {
            place.push({ name: 'intersection', value: `intersection-${sensor.intersection_set}` });
        }

        return {
            imageCoordinates,
            globalCoordinates,
            coordinates: { x: 0, y: 0 },
            origin: { lat: project?.originLat || 0, lng: project?.originLng || 0 },
            geoLocation: { lat: sensor.originLat || 0, lng: sensor.originLng || 0 },
            scaleFactor: sensor.scaleFactor || 1,
            attributes: getSensorAttributes(sensor),
            rois,
            place,
            tripwires: [], // Simplified for now
        };
    };

    // Generate cartesian calibration JSON data
    const generateCartesianCalibrationData = (sensor: Sensor): Omit<CalibrationSensor, 'id' | 'type'> => {
        let imageCoordinates: { x: number; y: number }[] = [];
        let globalCoordinates: { x: number; y: number }[] = [];

        // Parse sensor polygon for image coordinates
        try {
            const sensorPolygons = JSON.parse(sensor.sensorPolygon || '[]');
            if (sensorPolygons.length > 0 && sensorPolygons[0].points) {
                imageCoordinates = convertLatLngToXY(sensorPolygons[0].points);
            }
        } catch (e) {
            console.warn('Failed to parse sensor polygon:', e);
        }

        // Parse edge lengths for global coordinates
        try {
            const edgeLengths = JSON.parse(sensor.edgeLengths || '[]');
            if (Array.isArray(edgeLengths)) {
                // Apply padding and scaling like React UI
                const paddedCoordinates = edgeLengths.map(point => ({
                    x: point.x + (sensor.invertImXPad || 0),
                    y: point.y + (sensor.invertImYPad || 0),
                }));

                // Scale coordinates (React UI uses factor of 100)
                globalCoordinates = paddedCoordinates.map(point => ({
                    x: point.x * 100,
                    y: point.y * 100,
                }));
            }
        } catch (e) {
            console.warn('Failed to parse edge lengths:', e);
        }

        // Parse ROIs (with homography transformation for cartesian)
        const rois: ROI[] = [];
        try {
            const roiPolygons = JSON.parse(sensor.roiPolygon || '[]') as Array<{ points?: { lat: number; lng: number }[] }>;
            // For cartesian, ROIs would need homography transformation
            // Simplified implementation for now
            roiPolygons.forEach((roi, index: number) => {
                if (roi.points && Array.isArray(roi.points)) {
                    const scaledCoordinates = convertLatLngToXY(roi.points).map(point => ({
                        x: point.x * 100,
                        y: point.y * 100,
                    }));
                    rois.push({
                        id: `roi-id-${index + 1}`,
                        roiCoordinates: scaledCoordinates,
                    });
                }
            });
        } catch (e) {
            console.warn('Failed to parse ROI polygon:', e);
        }

        // Generate place array like React UI
        const place: Place[] = [];
        // Add places based on sensor's place_set, intersection_set, etc.
        if (sensor.intersection_set) {
            place.push({ name: 'intersection', value: `intersection-${sensor.intersection_set}` });
        }
        // Add city, building, room like React UI does
        place.push(
            { name: 'city', value: project?.cityPlace || 'Unknown City' },
            { name: 'building', value: project?.name || 'Unknown Building' },
            { name: 'room', value: project?.roomPlace || 'Unknown Room' }
        );

        return {
            imageCoordinates,
            globalCoordinates,
            coordinates: { x: 0, y: 0 },
            origin: { lat: project?.originLat || 0, lng: project?.originLng || 0 },
            geoLocation: { lat: sensor.originLat || 0, lng: sensor.originLng || 0 },
            scaleFactor: 100,
            attributes: getSensorAttributes(sensor),
            rois,
            place,
            tripwires: [], // Simplified for now
        };
    };

    // Generate complete calibration JSON
    const generateCalibrationJson = (): CalibrationJson | null => {
        if (!project || !selectedSensor) {
            setError('No project or sensor selected');
            return null;
        }

        const calibrationType = project.calibrationType as 'image' | 'cartesian';

        if (calibrationType !== 'image' && calibrationType !== 'cartesian') {
            setError(`Unsupported calibration type: ${calibrationType}. Only 'image' and 'cartesian' are supported.`);
            return null;
        }

        const sensorData =
            calibrationType === 'image' ? generateImageCalibrationData(selectedSensor) : generateCartesianCalibrationData(selectedSensor);

        const calibrationJson: CalibrationJson = {
            version: '1.0',
            osmURL: project?.mapFile || '',
            calibrationType,
            sensors: [
                {
                    id: selectedSensor.sensorId, // Use sensorId like React UI
                    type: 'camera',
                    ...sensorData,
                },
            ],
            corridors: [], // React UI adds corridors from project data
        };

        return calibrationJson;
    };

    // Save JSON to backend like React UI does
    const saveJsonToBackend = async (calibrationJson: CalibrationJson) => {
        if (!project?.id) return;

        try {
            const response = await fetch(`/api/projects/${project.id}/`, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    ...(selectedSensorId ? { streamId: selectedSensorId } : {}),
                },
                body: JSON.stringify({
                    calibrationJson: JSON.stringify(calibrationJson),
                }),
            });

            if (!response.ok) {
                throw new Error(`Failed to save to backend: ${response.statusText}`);
            }
        } catch (error) {
            console.error('Error saving calibration JSON to backend:', error);
        }
    };

    // Download JSON file
    const downloadJson = (jsonObject: CalibrationJson, filename: string) => {
        const blob = new Blob([JSON.stringify(jsonObject, null, 2)], {
            type: 'application/json',
        });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;

        const clickHandler = () => {
            setTimeout(() => {
                URL.revokeObjectURL(url);
                a.removeEventListener('click', clickHandler);
            }, 150);
        };

        a.addEventListener('click', clickHandler, false);
        a.click();
    };

    // Handle export
    const handleExport = async () => {
        setError(null);
        const calibrationJson = generateCalibrationJson();

        if (calibrationJson) {
            try {
                // Save to backend like React UI does
                await saveJsonToBackend(calibrationJson);

                // Download the file
                const filename = `calibration.json`; // React UI uses fixed filename
                downloadJson(calibrationJson, filename);

                setSuccess('Calibration JSON exported and saved successfully!');
                setExportDialogOpen(false);
            } catch (error) {
                setError('Export successful, but failed to save to backend. Check console for details.');

                // Still download the file even if backend save fails
                const filename = `calibration.json`;
                downloadJson(calibrationJson, filename);
                setExportDialogOpen(false);
            }
        }
    };

    // Handle import from text with validation
    const handleImportFromText = () => {
        setError(null);
        setImportPreview(null);

        if (!importJsonText.trim()) {
            setError('No JSON data provided');
            return;
        }

        try {
            const jsonObject = JSON.parse(importJsonText);

            // Use validation from utilities
            const validation = validateCalibrationJson(jsonObject);
            if (!validation.isValid) {
                setError(`Validation errors: ${validation.errors.join(', ')}`);
                return;
            }

            // Generate preview
            const preview = generateImportPreview(jsonObject);
            setImportPreview(preview);

            // Process the import if project calibration type matches
            if (project?.calibrationType) {
                const result = processImportedCalibrationJson(
                    jsonObject,
                    project.calibrationType as 'image' | 'cartesian',
                    selectedSensorId || undefined
                );

                if (!result.success) {
                    setError(result.message);
                    return;
                }
            }

            onJsonImported?.(jsonObject);
            setSuccess('Calibration JSON imported successfully!');
            setImportDialogOpen(false);
            setImportJsonText('');
        } catch (error: unknown) {
            const errorMessage = error instanceof Error ? error.message : 'Unknown error occurred';
            setError(`JSON parsing error: ${errorMessage}`);
        }
    };

    // Handle import from file
    const handleImportFromFile = (event: React.ChangeEvent<HTMLInputElement>) => {
        const file = event.target.files?.[0];
        if (!file) return;

        if (!file.name.endsWith('.json')) {
            setError('Please select a JSON file');
            return;
        }

        const reader = new FileReader();
        reader.onload = e => {
            try {
                const content = e.target?.result as string;
                const jsonObject = JSON.parse(content);

                // Basic validation
                if (!jsonObject.calibrationType || !jsonObject.sensors) {
                    setError('Invalid calibration JSON format. Missing required fields.');
                    return;
                }

                // Check if calibration type is supported
                if (jsonObject.calibrationType !== 'image' && jsonObject.calibrationType !== 'cartesian') {
                    setError(`Unsupported calibration type: ${jsonObject.calibrationType}. Only 'image' and 'cartesian' are supported.`);
                    return;
                }

                onJsonImported?.(jsonObject);
                setSuccess('Calibration JSON imported successfully!');
                setImportDialogOpen(false);
            } catch (error: unknown) {
                const errorMessage = error instanceof Error ? error.message : 'Unknown error occurred';
                setError(`Error reading file: ${errorMessage}`);
            }
        };

        reader.onerror = () => {
            setError('Error reading the file');
        };

        reader.readAsText(file);
    };

    const clearMessages = () => {
        setError(null);
        setSuccess(null);
    };

    return (
        <Box>
            {/* Success/Error Messages */}
            {error && (
                <Alert severity='error' onClose={clearMessages} sx={{ mb: 2 }}>
                    {error}
                </Alert>
            )}
            {success && (
                <Alert severity='success' onClose={clearMessages} sx={{ mb: 2 }}>
                    {success}
                </Alert>
            )}

            {/* Main Controls */}
            <Card>
                <CardContent>
                    <Typography variant='h6' gutterBottom>
                        Calibration JSON Manager
                    </Typography>
                    <Typography variant='body2' color='text.secondary' sx={{ mb: 3 }}>
                        Export or import calibration data in JSON format. Supports image and cartesian calibration types.
                    </Typography>

                    <Grid2 container spacing={2}>
                        <Grid2 size={{ xs: 12, sm: 6 }}>
                            <Button
                                variant='contained'
                                startIcon={<DownloadIcon />}
                                fullWidth
                                onClick={() => setExportDialogOpen(true)}
                                disabled={!project || !selectedSensor}
                            >
                                Export Calibration JSON
                            </Button>
                        </Grid2>
                        <Grid2 size={{ xs: 12, sm: 6 }}>
                            <Button variant='outlined' startIcon={<UploadIcon />} fullWidth onClick={() => setImportDialogOpen(true)}>
                                Import Calibration JSON
                            </Button>
                        </Grid2>
                    </Grid2>

                    {(!project || !selectedSensor) && (
                        <Typography variant='body2' color='warning.main' sx={{ mt: 2 }}>
                            Please select a project and sensor to enable export functionality.
                        </Typography>
                    )}
                </CardContent>
            </Card>

            {/* Export Dialog */}
            <Dialog open={exportDialogOpen} onClose={() => setExportDialogOpen(false)} maxWidth='sm' fullWidth>
                <DialogTitle>
                    Export Calibration JSON
                    <IconButton onClick={() => setExportDialogOpen(false)} sx={{ position: 'absolute', right: 8, top: 8 }}>
                        <CloseIcon />
                    </IconButton>
                </DialogTitle>
                <DialogContent>
                    <Typography variant='body2' sx={{ mb: 2 }}>
                        This will export the calibration data for the selected sensor in JSON format.
                    </Typography>

                    {selectedSensor && (
                        <Paper sx={{ p: 2, bgcolor: 'background.default' }}>
                            <Typography variant='subtitle2' gutterBottom>
                                Export Details:
                            </Typography>
                            <Typography variant='body2'>
                                <strong>Project:</strong> {project?.name || 'Unknown'}
                            </Typography>
                            <Typography variant='body2'>
                                <strong>Sensor:</strong> {selectedSensor.sensorId}
                            </Typography>
                            <Typography variant='body2'>
                                <strong>Calibration Type:</strong> {project?.calibrationType || 'Unknown'}
                            </Typography>
                            <Typography variant='body2'>
                                <strong>Calibrated:</strong> {selectedSensor.isCalibrated ? 'Yes' : 'No'}
                            </Typography>
                        </Paper>
                    )}
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setExportDialogOpen(false)}>Cancel</Button>
                    <Button variant='contained' onClick={handleExport} startIcon={<DownloadIcon />}>
                        Export JSON
                    </Button>
                </DialogActions>
            </Dialog>

            {/* Import Dialog */}
            <Dialog open={importDialogOpen} onClose={() => setImportDialogOpen(false)} maxWidth='md' fullWidth>
                <DialogTitle>
                    Import Calibration JSON
                    <IconButton onClick={() => setImportDialogOpen(false)} sx={{ position: 'absolute', right: 8, top: 8 }}>
                        <CloseIcon />
                    </IconButton>
                </DialogTitle>
                <DialogContent>
                    <Typography variant='body2' sx={{ mb: 3 }}>
                        Import calibration data from a JSON file or paste JSON content directly.
                    </Typography>

                    <Grid2 container spacing={2} sx={{ mb: 3 }}>
                        <Grid2 size={{ xs: 12, sm: 6 }}>
                            <Button
                                variant={importMode === 'file' ? 'contained' : 'outlined'}
                                startIcon={<FileUploadIcon />}
                                fullWidth
                                onClick={() => setImportMode('file')}
                            >
                                Upload File
                            </Button>
                        </Grid2>
                        <Grid2 size={{ xs: 12, sm: 6 }}>
                            <Button
                                variant={importMode === 'paste' ? 'contained' : 'outlined'}
                                startIcon={<ContentPasteIcon />}
                                fullWidth
                                onClick={() => setImportMode('paste')}
                            >
                                Paste JSON
                            </Button>
                        </Grid2>
                    </Grid2>

                    <Divider sx={{ mb: 3 }} />

                    {importMode === 'file' ? (
                        <Box>
                            <input
                                type='file'
                                accept='.json'
                                onChange={handleImportFromFile}
                                style={{ width: '100%', padding: '10px', border: '1px dashed #ccc', borderRadius: '4px' }}
                            />
                            <Typography variant='caption' display='block' sx={{ mt: 1, color: 'text.secondary' }}>
                                Select a JSON file containing calibration data
                            </Typography>
                        </Box>
                    ) : (
                        <TextField
                            multiline
                            rows={10}
                            fullWidth
                            placeholder='Paste your calibration JSON here...'
                            value={importJsonText}
                            onChange={e => setImportJsonText(e.target.value)}
                            variant='outlined'
                        />
                    )}

                    {importPreview && (
                        <Accordion sx={{ mt: 3 }}>
                            <AccordionSummary
                                expandIcon={<ExpandMoreIcon />}
                                aria-controls='import-preview-content'
                                id='import-preview-header'
                            >
                                <Typography variant='subtitle2'>Import Preview</Typography>
                            </AccordionSummary>
                            <AccordionDetails>
                                <Typography variant='body2' sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                                    {importPreview}
                                </Typography>
                            </AccordionDetails>
                        </Accordion>
                    )}
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setImportDialogOpen(false)}>Cancel</Button>
                    {importMode === 'paste' && (
                        <Button
                            variant='contained'
                            onClick={handleImportFromText}
                            startIcon={<UploadIcon />}
                            disabled={!importJsonText.trim()}
                        >
                            Import JSON
                        </Button>
                    )}
                </DialogActions>
            </Dialog>
        </Box>
    );
};

export default CalibrationJsonManager;
