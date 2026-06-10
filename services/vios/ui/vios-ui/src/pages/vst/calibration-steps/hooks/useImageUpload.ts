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
import config from '../../../../config';

interface SensorData {
    sensorPolygon: string;
    homography: string;
    imHomography: string;
    edgeLengths: string;
    isCalibrated: boolean;
    isValidated: boolean;
}

interface UseImageUploadOptions {
    onSuccess?: () => void;
    onError?: (error: string) => void;
}

export const useImageUpload = (options: UseImageUploadOptions = {}) => {
    const [uploadingIds, setUploadingIds] = useState<Set<string>>(new Set());
    const [uploadProgress, setUploadProgress] = useState(0);

    const getSensorData = async (sensorId: string): Promise<SensorData> => {
        const response = await fetch(`${config.analyticsUIServerEndpoint}/api/sensors/${sensorId}/`, {
            headers: { streamId: sensorId },
        });

        if (!response.ok) {
            throw new Error(`Failed to fetch sensor data: ${response.status} ${response.statusText}`);
        }

        return await response.json();
    };

    const uploadCalibrationImage = async (sensorId: string, file: File, sensorData: SensorData) => {
        const formData = new FormData();
        formData.append('imageUrl', file);
        formData.append('sensorPolygon', sensorData.sensorPolygon || '[]');
        formData.append('homography', sensorData.homography || '[]');
        formData.append('imHomography', sensorData.imHomography || '[]');
        formData.append('edgeLengths', sensorData.edgeLengths || '[]');
        formData.append('isCalibrated', sensorData.isCalibrated.toString());
        formData.append('isValidated', sensorData.isValidated.toString());

        const uploadUrl = `${config.analyticsUIServerEndpoint}/api/sensors/${sensorId}/`;

        console.log('🔍 Image upload debug info:');
        console.log('- Upload URL:', uploadUrl);
        console.log('- File name:', file.name);
        console.log('- File size:', file.size, 'bytes');
        console.log('- File type:', file.type);
        console.log('- FormData entries:');
        for (const [key, value] of formData.entries()) {
            console.log(`  - ${key}:`, value instanceof File ? `File(${value.name}, ${value.size}b)` : value);
        }

        try {
            const response = await fetch(uploadUrl, {
                method: 'PATCH',
                headers: { streamId: sensorId },
                body: formData,
            });

            if (!response.ok) {
                const errorText = await response.text();
                console.error('❌ Upload failed with response:', {
                    status: response.status,
                    statusText: response.statusText,
                    headers: Object.fromEntries(response.headers.entries()),
                    body: errorText,
                });
                throw new Error(`Failed to upload image: ${response.status} ${response.statusText}. Server response: ${errorText}`);
            }

            console.log('✅ Upload successful');
            return await response.json();
        } catch (error) {
            if (error instanceof TypeError && error.message.includes('Failed to fetch')) {
                console.error('❌ Network error - server may be unreachable:', {
                    url: uploadUrl,
                    error: error.message,
                });
                throw new Error(
                    `Network error: Cannot reach server at ${uploadUrl}. Please check if the analytics server is running and accessible.`
                );
            }
            throw error;
        }
    };

    const updateImageResolution = async (sensorId: string, width: number, height: number) => {
        const response = await fetch(`${config.analyticsUIServerEndpoint}/api/sensors/${sensorId}/`, {
            method: 'PATCH',
            headers: {
                'Content-Type': 'application/json',
                streamId: sensorId,
            },
            body: JSON.stringify({
                height: height,
                width: width,
                // Also set inverted image dimensions to match original image
                // This prevents the server fallback bug that swaps dimensions
                invertImHeight: height,
                invertImWidth: width,
                invertImXPad: 0,
                invertImYPad: 0,
            }),
        });

        if (!response.ok) {
            throw new Error(`Failed to update image resolution: ${response.status} ${response.statusText}`);
        }

        return await response.json();
    };

    const getImageDimensions = (file: File): Promise<{ width: number; height: number }> => {
        return new Promise((resolve, reject) => {
            const img = new Image();
            img.onload = () => {
                resolve({ width: img.naturalWidth, height: img.naturalHeight });
            };
            img.onerror = () => {
                reject(new Error('Failed to load image'));
            };
            img.src = URL.createObjectURL(file);
        });
    };

    const uploadImage = async (sensorId: string, file: File) => {
        setUploadingIds(prev => new Set(prev).add(sensorId));
        setUploadProgress(0);

        try {
            console.log('🚀 Starting image upload for sensor:', sensorId);

            // Get image dimensions first
            setUploadProgress(10);
            console.log('📏 Getting image dimensions...');
            const dimensions = await getImageDimensions(file);
            console.log('✅ Image dimensions:', dimensions);

            // Get current sensor data
            setUploadProgress(25);
            console.log('📄 Fetching current sensor data...');
            const sensorData = await getSensorData(sensorId);
            console.log('✅ Sensor data retrieved');

            // Upload the image with the sensor data
            setUploadProgress(50);
            console.log('📤 Uploading image...');
            await uploadCalibrationImage(sensorId, file, sensorData);
            console.log('✅ Image uploaded successfully');

            // Update image resolution
            setUploadProgress(75);
            console.log('🔄 Updating image resolution...');
            await updateImageResolution(sensorId, dimensions.width, dimensions.height);
            console.log('✅ Image resolution updated');

            setUploadProgress(100);
            console.log('🎉 Upload completed successfully');
            options.onSuccess?.();
        } catch (err) {
            console.error('❌ Upload failed:', err);
            const errorMessage = err instanceof Error ? err.message : 'Failed to upload calibration image';

            // Provide more specific error messages based on the error type
            if (errorMessage.includes('Failed to fetch') || errorMessage.includes('Network error')) {
                const detailedMessage =
                    'Connection failed: Unable to reach the analytics server at ' +
                    config.analyticsUIServerEndpoint +
                    '. Please check:\n\n' +
                    '1. Is the analytics server running?\n' +
                    '2. Is the server accessible from your network?\n' +
                    '3. Check your network connection\n\n' +
                    'Original error: ' +
                    errorMessage;
                options.onError?.(detailedMessage);
            } else if (errorMessage.includes('CORS')) {
                const corsMessage =
                    'CORS error: The server is not configured to allow requests from this domain. ' +
                    'Please contact your system administrator.\n\nOriginal error: ' +
                    errorMessage;
                options.onError?.(corsMessage);
            } else {
                options.onError?.(errorMessage);
            }
        } finally {
            setUploadingIds(prev => {
                const newSet = new Set(prev);
                newSet.delete(sensorId);
                return newSet;
            });
            setUploadProgress(0);
        }
    };

    return {
        uploadingIds,
        uploadProgress,
        uploadImage,
        isUploading: (sensorId: string) => uploadingIds.has(sensorId),
    };
};
