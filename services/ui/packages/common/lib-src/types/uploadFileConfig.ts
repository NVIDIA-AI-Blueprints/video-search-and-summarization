// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/** Types for upload file config template */

export type UploadFileFieldType = 'boolean' | 'string' | 'number' | 'array' | 'select';

export interface UploadFileFieldConfig {
  'field-name': string;
  'field-type': UploadFileFieldType;
  'field-default-value': boolean | string | number | string[] | number[];
  'field-options'?: string[] | number[];
  'changeable'?: boolean;
  'tooltip-info'?: string;
}

export interface UploadFileConfigTemplate {
  fields: UploadFileFieldConfig[];
}
