import toast from 'react-hot-toast';

import { SupportedExportFormats } from '@/types/export';
import { MAX_FILE_SIZE_BYTES } from '@/constants';

/**
 * Validates and sanitizes imported JSON data to prevent XSS and prototype pollution
 * @param rawJson - Raw JSON string from file
 * @returns Validated export format or null if invalid
 */
export function validateImportData(rawJson: string): SupportedExportFormats | null {
  // Basic input validation
  if (!rawJson || typeof rawJson !== 'string') {
    return null;
  }

  // Simple DoS protection - limit JSON string length
  if (rawJson.length > MAX_FILE_SIZE_BYTES) {
    const maxSizeMB = Math.round(MAX_FILE_SIZE_BYTES / (1024 * 1024));
    toast.error(`Import file too large (max ${maxSizeMB}MB)`);
    return null;
  }

  let parsed: any;
  try {
    // Parse JSON safely
    parsed = JSON.parse(rawJson);
  } catch (error) {
    toast.error('Invalid JSON format');
    return null;
  }

  // Block null or non-object data
  if (parsed === null || typeof parsed !== 'object') {
    toast.error('Import data must be a valid object');
    return null;
  }

  // Prevent prototype pollution by blocking dangerous properties
  const dangerousKeys = ['__proto__', 'constructor', 'prototype'];
  function sanitizeObject(obj: any): any {
    if (obj === null || typeof obj !== 'object') return obj;
    
    if (Array.isArray(obj)) {
      return obj.map(item => sanitizeObject(item));
    }

    const sanitized: any = {};
    for (const [key, value] of Object.entries(obj)) {
      // Block dangerous prototype pollution keys
      if (dangerousKeys.includes(key)) {
        console.warn(`Blocked dangerous key during import: ${key}`);
        continue;
      }
      
      // Recursively sanitize nested objects
      sanitized[key] = sanitizeObject(value);
    }
    return sanitized;
  }

  // Sanitize the parsed data
  const sanitized = sanitizeObject(parsed);

  // Validate export format structure
  if (Array.isArray(sanitized)) {
    // ExportFormatV1 - array of conversations
    if (sanitized.every(item => 
      typeof item === 'object' && 
      item !== null &&
      typeof item.id === 'number' &&
      typeof item.name === 'string' &&
      Array.isArray(item.messages)
    )) {
      return sanitized as SupportedExportFormats;
    }
  } else if (typeof sanitized === 'object' && sanitized !== null) {
    // Check for V2, V3, V4 formats
    if (sanitized.version === 4 && 
        Array.isArray(sanitized.history) && 
        Array.isArray(sanitized.folders) && 
        Array.isArray(sanitized.prompts)) {
      return sanitized as SupportedExportFormats;
    }
    
    if (sanitized.version === 3 && 
        Array.isArray(sanitized.history) && 
        Array.isArray(sanitized.folders)) {
      return sanitized as SupportedExportFormats;
    }
    
    // V2 format (history and folders properties)
    if ((sanitized.history === null || Array.isArray(sanitized.history)) &&
        (sanitized.folders === null || Array.isArray(sanitized.folders))) {
      return sanitized as SupportedExportFormats;
    }
  }

  toast.error('Invalid import format. Please use a valid export file.');
  return null;
}
