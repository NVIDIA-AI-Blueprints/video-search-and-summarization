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
/**
 * Utility functions for downloading different file types
 */

// Type definitions for better type safety
interface CSVRow {
    [key: string]: string | number | boolean | null | undefined;
}

/**
 * Downloads a JSON file to the user's downloads folder
 */
export const downloadJSON = (jsonObject: object | object[], filename: string) => {
    const blob = new Blob([JSON.stringify(jsonObject, null, 2)], {
        type: 'application/json',
    });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename || 'download.json';

    const clickHandler = () => {
        setTimeout(() => {
            URL.revokeObjectURL(url);
            a.removeEventListener('click', clickHandler);
        }, 150);
    };

    a.addEventListener('click', clickHandler, false);
    a.click();
};

/**
 * Converts JSON array to CSV format
 */
const convertToCSV = (jsonArray: CSVRow[]): string => {
    if (!jsonArray || jsonArray.length === 0) return '';

    const headers = Object.keys(jsonArray[0]);
    const csvContent = [
        headers.join(','),
        ...jsonArray.map(row =>
            headers
                .map(header => {
                    const value = row[header] || '';
                    // Escape quotes and wrap in quotes if necessary
                    return `"${String(value).replace(/"/g, '""')}"`;
                })
                .join(',')
        ),
    ].join('\n');

    return csvContent;
};

/**
 * Downloads a CSV file to the user's downloads folder
 */
export const downloadCSV = (csvData: CSVRow[], filename: string) => {
    const csv = convertToCSV(csvData);
    const blob = new Blob([csv], {
        type: 'text/csv',
    });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename || 'download.csv';

    const clickHandler = () => {
        setTimeout(() => {
            URL.revokeObjectURL(url);
            a.removeEventListener('click', clickHandler);
        }, 150);
    };

    a.addEventListener('click', clickHandler, false);
    a.click();
};

/**
 * Downloads a ZIP file to the user's downloads folder
 */
export const downloadZipFile = (zipData: ArrayBuffer, filename: string) => {
    const blob = new Blob([zipData], {
        type: 'application/zip',
    });
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', filename || 'download.zip');

    const clickHandler = () => {
        setTimeout(() => {
            URL.revokeObjectURL(url);
            link.removeEventListener('click', clickHandler);
        }, 180);
    };

    link.addEventListener('click', clickHandler, false);
    link.click();
};
