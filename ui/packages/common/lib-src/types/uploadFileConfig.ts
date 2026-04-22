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
