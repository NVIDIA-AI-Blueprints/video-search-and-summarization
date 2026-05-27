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
import { useState } from 'react';
import nvAxios from '../../../../services/Axios';
import config from '../../../../config';
import { downloadJSON, downloadCSV, downloadZipFile } from '../utils/downloadUtils';
import { Project } from '../types';

// Type definitions for better type safety
interface SensorData {
    sensorId: string;
    rtspURL: string;
    'mmsInfo.protocol': string;
    'mmsInfo.host': string;
    'mmsInfo.type': string;
    fps: string;
    deviceId: string;
    videoURL: string;
    depth: string;
    fieldOfView: string;
    direction: string;
    [key: string]: string | number | boolean | null | undefined;
}

interface ImageMetadata {
    sensorId?: string;
    place?: string;
    fileName: string;
    view: string;
}

interface NetworkIntersection {
    name: string;
    segments: unknown[]; // Using unknown[] since segments structure depends on getIntersectionSegmentsForExport
}

interface NetworkJSON {
    city: string;
    docType: string;
    intersections: NetworkIntersection[];
}

interface ImageMetadataJSON {
    images: ImageMetadata[];
}

interface SensorResponse {
    isCalibrated?: boolean;
    isValidated?: boolean;
    sensorId?: string;
    rtspURL?: string;
    mmsInfo_protocol?: string;
    mmsInfo_host?: string;
    mmsInfo_type?: string;
    fps?: string;
    deviceId?: string;
    videoURL?: string;
    depth?: string;
    fieldOfView?: string;
    direction?: string;
    imageUrl?: string;
    invertImageUrl?: string;
}

interface IntersectionResponse {
    linksAreDrawn?: boolean;
    linksAreValid?: boolean;
    name?: string;
}

interface ProjectResponse {
    sensor_set: SensorResponse[];
    intersection_set?: IntersectionResponse[];
    name?: string;
    calibrationType?: string;
    floorPlanImageUrl?: string;
}

interface ExportHookReturn {
    loading: boolean;
    error: string | null;
    success: string | null;
    setError: (error: string | null) => void;
    setSuccess: (success: string | null) => void;
    exportSensorDetails: (project: Project) => Promise<void>;
    exportWarpedImages: (project: Project) => Promise<void>;
    exportImages: (project: Project) => Promise<void>;
    exportImageMetadata: (project: Project) => Promise<void>;
    exportNetworkJSON: (project: Project) => Promise<void>;
}

export const useExportFunctions = (): ExportHookReturn => {
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [success, setSuccess] = useState<string | null>(null);

    /**
     * Export sensor details as CSV (matching ReactJS getSensorsCSV)
     */
    const exportSensorDetails = async (project: Project) => {
        setLoading(true);
        setError(null);
        setSuccess(null);

        try {
            const response = await nvAxios.get(`${config.analyticsUIServerEndpoint}/api/projects/${project.id}/`);
            const { sensor_set }: ProjectResponse = response.data;

            const sensorData: SensorData[] = [];

            sensor_set.forEach(sensor => {
                if (sensor.isCalibrated && sensor.isValidated) {
                    sensorData.push({
                        sensorId: sensor.sensorId || '',
                        rtspURL: sensor.rtspURL || '',
                        'mmsInfo.protocol': sensor.mmsInfo_protocol || '',
                        'mmsInfo.host': sensor.mmsInfo_host || '',
                        'mmsInfo.type': sensor.mmsInfo_type || '',
                        fps: sensor.fps || '',
                        deviceId: sensor.deviceId || '',
                        videoURL: sensor.videoURL || '',
                        depth: sensor.depth || '',
                        fieldOfView: sensor.fieldOfView || '',
                        direction: sensor.direction || '',
                    });
                }
            });

            downloadCSV(sensorData, 'sensorMetadata.csv');
            setSuccess('Sensor metadata CSV downloaded successfully!');
        } catch (err) {
            setError('Failed to export sensor details: ' + (err instanceof Error ? err.message : 'Unknown error'));
        } finally {
            setLoading(false);
        }
    };

    /**
     * Export warped images as ZIP (matching ReactJS getWarpedImages)
     */
    const exportWarpedImages = async (project: Project) => {
        setLoading(true);
        setError(null);
        setSuccess(null);

        try {
            const response = await nvAxios.get(`${config.analyticsUIServerEndpoint}/api/getWarpedFiles/${project.id}/`, {
                responseType: 'arraybuffer',
            });

            // Get filename from response headers if available
            let filename = 'Warped Images.zip';
            const disposition = response.headers['content-disposition'];
            if (disposition) {
                const filenameRegex = /filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/;
                const matches = filenameRegex.exec(disposition);
                if (matches != null && matches[1]) {
                    filename = matches[1].replace(/['"]/g, '');
                }
            }

            downloadZipFile(response.data, filename);
            setSuccess('Warped images ZIP file downloaded successfully!');
        } catch (err) {
            setError('Failed to export warped images: ' + (err instanceof Error ? err.message : 'Unknown error'));
        } finally {
            setLoading(false);
        }
    };

    /**
     * Export all images as ZIP (matching ReactJS getImages)
     */
    const exportImages = async (project: Project) => {
        setLoading(true);
        setError(null);
        setSuccess(null);

        try {
            const response = await nvAxios.get(`${config.analyticsUIServerEndpoint}/api/getImageFiles/${project.id}/`, {
                responseType: 'arraybuffer',
            });

            // Get filename from response headers if available
            let filename = 'Images.zip';
            const disposition = response.headers['content-disposition'];
            if (disposition) {
                const filenameRegex = /filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/;
                const matches = filenameRegex.exec(disposition);
                if (matches != null && matches[1]) {
                    filename = matches[1].replace(/['"]/g, '');
                }
            }

            downloadZipFile(response.data, filename);
            setSuccess('Images ZIP file downloaded successfully!');
        } catch (err) {
            setError('Failed to export images: ' + (err instanceof Error ? err.message : 'Unknown error'));
        } finally {
            setLoading(false);
        }
    };

    /**
     * Export image metadata as JSON (matching ReactJS getImageJson)
     */
    const exportImageMetadata = async (project: Project) => {
        setLoading(true);
        setError(null);
        setSuccess(null);

        try {
            const response = await nvAxios.get(`${config.analyticsUIServerEndpoint}/api/projects/${project.id}/`);
            const cleanData: ProjectResponse = response.data;
            const { sensor_set } = cleanData;

            const imageMetaDataJson: ImageMetadataJSON = {
                images: [],
            };

            sensor_set.forEach(sensor => {
                if (sensor.isCalibrated && sensor.isValidated) {
                    const { sensorId, imageUrl, invertImageUrl } = sensor;

                    if (cleanData.calibrationType === 'cartesian') {
                        // Add warped image
                        if (invertImageUrl) {
                            const fileNameArray = invertImageUrl.split('/');
                            const fileName = fileNameArray[fileNameArray.length - 1];
                            imageMetaDataJson.images.push({
                                sensorId,
                                fileName,
                                view: 'warped-camera-view',
                            });
                        }

                        // Add original image
                        if (imageUrl) {
                            const imFileNameArray = imageUrl.split('/');
                            const imFileName = imFileNameArray[imFileNameArray.length - 1];
                            imageMetaDataJson.images.push({
                                sensorId,
                                fileName: imFileName,
                                view: 'camera-view',
                            });
                        }
                    } else if (
                        cleanData.calibrationType === 'mtmc' ||
                        cleanData.calibrationType === 'geo' ||
                        cleanData.calibrationType === 'image'
                    ) {
                        // Add camera view image
                        if (imageUrl) {
                            const fileNameArray = imageUrl.split('/');
                            const fileName = fileNameArray[fileNameArray.length - 1];
                            imageMetaDataJson.images.push({
                                sensorId,
                                fileName,
                                view: 'camera-view',
                            });
                        }
                    }
                }
            });

            // Add floor plan image for MTMC calibration type
            if (cleanData.calibrationType === 'mtmc' && cleanData.floorPlanImageUrl) {
                const fileNameArray = cleanData.floorPlanImageUrl.split('/');
                const fileName = fileNameArray[fileNameArray.length - 1];
                const place = 'building=' + cleanData.name;
                imageMetaDataJson.images.push({
                    place,
                    fileName,
                    view: 'plan-view',
                });
            }

            downloadJSON(imageMetaDataJson, 'imageMetadata.json');
            setSuccess('Image metadata JSON downloaded successfully!');
        } catch (err) {
            setError('Failed to export image metadata: ' + (err instanceof Error ? err.message : 'Unknown error'));
        } finally {
            setLoading(false);
        }
    };

    /**
     * Export network JSON for intersections (matching ReactJS getNetworkJSON)
     */
    const exportNetworkJSON = async (project: Project) => {
        setLoading(true);
        setError(null);
        setSuccess(null);

        try {
            const response = await nvAxios.get(`${config.analyticsUIServerEndpoint}/api/projects/${project.id}/`);
            const cleanData: ProjectResponse = response.data;
            const { intersection_set, name } = cleanData;

            const networkJSON: NetworkJSON = {
                city: name || 'not defined',
                docType: 'roadNetwork',
                intersections: [],
            };

            // Process intersections if they exist
            if (intersection_set && Array.isArray(intersection_set)) {
                intersection_set.forEach(intersection => {
                    if (intersection.linksAreDrawn && intersection.linksAreValid) {
                        const { name: intersectionName } = intersection;
                        // Note: getIntersectionSegmentsForExport function would need to be implemented
                        // For now, we'll add basic structure
                        const segments: unknown[] = []; // TODO: Implement getIntersectionSegmentsForExport

                        networkJSON.intersections.push({
                            name: intersectionName || '',
                            segments,
                        });
                    }
                });
            }

            downloadJSON(networkJSON, 'roadNetwork.json');
            setSuccess('Road network JSON downloaded successfully!');
        } catch (err) {
            setError('Failed to export road network: ' + (err instanceof Error ? err.message : 'Unknown error'));
        } finally {
            setLoading(false);
        }
    };

    return {
        loading,
        error,
        success,
        setError,
        setSuccess,
        exportSensorDetails,
        exportWarpedImages,
        exportImages,
        exportImageMetadata,
        exportNetworkJSON,
    };
};
