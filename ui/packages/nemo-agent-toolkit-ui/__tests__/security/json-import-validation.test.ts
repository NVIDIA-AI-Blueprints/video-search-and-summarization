// Mock react-hot-toast module
jest.mock('react-hot-toast', () => ({
  __esModule: true,
  default: {
    error: jest.fn(),
    success: jest.fn(),
  },
}));

import toast from 'react-hot-toast';

import { validateImportData } from '@/utils/security/import-validation';
import { MAX_FILE_SIZE_BYTES } from '@/constants';

// Get mocked toast functions for assertions
const mockToast = toast as jest.Mocked<typeof toast>;

describe('JSON Import Validation Security', () => {
  beforeEach(() => {
    // Clear all mocks before each test
    jest.clearAllMocks();
  });

  describe('Positive Tests - Valid JSON should pass', () => {
    test('accepts valid conversation export format V1 (array)', () => {
      const validJson = JSON.stringify([
        {
          id: 1,
          name: "Test Conversation",
          messages: [
            { role: "user", content: "Hello" },
            { role: "assistant", content: "Hi there!" }
          ]
        }
      ]);

      const result = validateImportData(validJson);
      expect(result).not.toBeNull();
      expect(Array.isArray(result)).toBe(true);
      if (Array.isArray(result)) {
        expect(result[0].name).toBe("Test Conversation");
      }
      // Valid data should not trigger error toasts
      expect(mockToast.error).not.toHaveBeenCalled();
    });

    test('accepts valid conversation export format V2+ (object)', () => {
      const validJson = JSON.stringify({
        version: 4,
        history: [
          {
            id: "conv-1",
            name: "Chat",
            messages: [],
            folderId: null
          }
        ],
        folders: [],
        prompts: []
      });

      const result = validateImportData(validJson);
      expect(result).not.toBeNull();
      if (result && !Array.isArray(result) && 'version' in result) {
        expect(result.version).toBe(4);
        expect(Array.isArray(result.history)).toBe(true);
      }
      // Valid data should not trigger error toasts
      expect(mockToast.error).not.toHaveBeenCalled();
    });

    test('accepts empty valid structures', () => {
      const emptyArray = JSON.stringify([]);
      const emptyObject = JSON.stringify({ history: [], folders: [] });

      expect(validateImportData(emptyArray)).not.toBeNull();
      expect(validateImportData(emptyObject)).not.toBeNull();
      // Valid data should not trigger error toasts
      expect(mockToast.error).not.toHaveBeenCalled();
    });
  });

  describe('Negative Tests - Invalid/malicious JSON should be blocked', () => {
    test('blocks prototype pollution attempts', () => {
      const maliciousJson = JSON.stringify({
        "__proto__": { "isAdmin": true },
        "constructor": { "prototype": { "isEvil": true } },
        "history": [
          {
            id: "conv-1",
            name: "Test Conversation", 
            messages: []
          }
        ],
        "folders": []
      });

      const result = validateImportData(maliciousJson);
      expect(result).not.toBeNull();
      // Dangerous payloads should be sanitized - the malicious content should not be there  
      const proto = Object.getPrototypeOf(result!);
      expect(proto).not.toHaveProperty('isAdmin');
      
      // The constructor property exists naturally, but shouldn't contain our malicious payload
      expect(result!.constructor).not.toEqual({ "prototype": { "isEvil": true } });
      // Safe data should remain
      if (result && !Array.isArray(result) && 'history' in result) {
        expect(result.history).toBeDefined();
      }
    });

    test('blocks malformed JSON', () => {
      const malformedJsons = [
        '{"invalid": json}',
        '{"incomplete": ',
        'not json at all',
        '{"trailing": "comma",}'
      ];

      malformedJsons.forEach(json => {
        expect(validateImportData(json)).toBeNull();
      });
      
      // Should call toast.error for each malformed JSON (excluding empty string which fails basic validation)
      expect(mockToast.error).toHaveBeenCalledWith('Invalid JSON format');
      expect(mockToast.error).toHaveBeenCalledTimes(malformedJsons.length);
      
      // Test empty string separately - it fails basic validation, no toast call
      expect(validateImportData('')).toBeNull();
    });

    test('blocks non-object/non-array data', () => {
      const invalidData = [
        JSON.stringify("just a string"),
        JSON.stringify(123),
        JSON.stringify(true),
        JSON.stringify(null)
      ];

      invalidData.forEach(json => {
        expect(validateImportData(json)).toBeNull();
      });
      
      // Should call toast.error for each invalid data type
      expect(mockToast.error).toHaveBeenCalledWith('Import data must be a valid object');
      expect(mockToast.error).toHaveBeenCalledTimes(invalidData.length);
    });

  test('blocks oversized JSON (DoS protection)', () => {
    // Create a JSON string larger than the max file size
    const largeObject = {
      data: 'x'.repeat(MAX_FILE_SIZE_BYTES + 1000) // Larger than limit
    };
    const largeJson = JSON.stringify(largeObject);

    expect(validateImportData(largeJson)).toBeNull();
    expect(mockToast.error).toHaveBeenCalledWith('Import file too large (max 5MB)');
  });

    test('blocks invalid input types', () => {
      const invalidInputs = [
        null,
        undefined,
        123,
        true,
        {},
        []
      ];

      invalidInputs.forEach(input => {
        expect(validateImportData(input as any)).toBeNull();
      });
      
      // These fail basic input validation, so no toast calls should be made
      expect(mockToast.error).not.toHaveBeenCalled();
    });

    test('blocks valid JSON with invalid export format', () => {
      const invalidFormatJson = JSON.stringify({
        someField: 'value',
        anotherField: 123,
        notAnExportFormat: true
      });

      expect(validateImportData(invalidFormatJson)).toBeNull();
      expect(mockToast.error).toHaveBeenCalledWith('Invalid import format. Please use a valid export file.');
    });

    test('sanitizes nested prototype pollution attempts', () => {
      const nestedMaliciousJson = JSON.stringify({
        history: [
          {
            id: "conv-1",
            name: "Normal Conversation",
            messages: [],
            "__proto__": { "evil": true }
          }
        ],
        folders: [
          {
            name: "Normal Folder",
            "constructor": { "prototype": { "malicious": true } }
          }
        ]
      });

      const result = validateImportData(nestedMaliciousJson);
      expect(result).not.toBeNull();
      
      // Check that dangerous payloads were sanitized from nested objects
      if (result && !Array.isArray(result) && 'history' in result && 'folders' in result) {
        // Additional null checks for arrays
        if (result.history && result.history.length > 0 && result.folders && result.folders.length > 0) {
          // The malicious content should not be present
          const historyProto = Object.getPrototypeOf(result.history[0]);
          expect(historyProto).not.toHaveProperty('evil');
          
          // The constructor property exists naturally, but shouldn't contain our malicious payload
          expect(result.folders[0].constructor).not.toEqual({ "prototype": { "malicious": true } });
          
          // Check that safe data remains
          expect(result.history[0].name).toBe("Normal Conversation");
          expect(result.folders[0].name).toBe("Normal Folder");
        }
      }
    });
  });
});
