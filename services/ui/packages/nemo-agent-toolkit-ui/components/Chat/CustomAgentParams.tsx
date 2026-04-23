import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

// Type definitions matching .env format
export type ParamType = 'string' | 'number' | 'boolean' | 'select';

export interface ParamFieldConfig {
  name: string;
  label: string;
  type: ParamType;
  'default-value': string | number | boolean;
  options?: string[];
  changeable?: boolean; // default: true - if false, user cannot change value on UI
  'tooltip-info'?: string;
}

export interface ParamField extends ParamFieldConfig {
  id: string;
  value: string | number | boolean;
}

export type CustomAgentParamsValues = Record<string, string | number | boolean>;

interface CustomAgentParamsProps {
  isOpen: boolean;
  onClose: () => void;
  fields: ParamField[];
  onFieldsChange: (fields: ParamField[]) => void;
  anchorRef?: React.RefObject<HTMLElement>;
}

const generateId = () => Math.random().toString(36).substring(2, 11);

// Reusable input styles
const inputClass = "w-full px-2 py-1.5 text-sm bg-gray-50 dark:bg-black border border-gray-200 dark:border-gray-600 rounded-md text-gray-900 dark:text-white focus:outline-none focus:ring-1 focus:ring-[#76b900]";

const OVERLAY_Z = 100; // Above ChatHeader menu bar (z-20)

export const CustomAgentParams: React.FC<CustomAgentParamsProps> = ({
  isOpen,
  onClose,
  fields,
  onFieldsChange,
  anchorRef,
}) => {
  const [anchorRect, setAnchorRect] = useState<DOMRect | null>(null);

  // Position dialog from anchor (for portal)
  useLayoutEffect(() => {
    if (!isOpen || !anchorRef?.current) {
      setAnchorRect(null);
      return;
    }
    const anchorElement = anchorRef.current;
    const updatePosition = () => setAnchorRect(anchorElement.getBoundingClientRect());
    updatePosition();
    const resizeObserver = new ResizeObserver(updatePosition);
    resizeObserver.observe(anchorElement);
    window.addEventListener('scroll', updatePosition, true);
    return () => {
      resizeObserver.disconnect();
      window.removeEventListener('scroll', updatePosition, true);
    };
  }, [isOpen, anchorRef]);

  // Handle escape key
  useEffect(() => {
    if (!isOpen) return;

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    
    document.addEventListener('keydown', handleEscape);

    return () => {
      document.removeEventListener('keydown', handleEscape);
    };
  }, [isOpen, onClose]);

  // Re-run position when window resizes so inline style (window.innerWidth/innerHeight + anchorRect) stays correct
  useEffect(() => {
    if (!isOpen || !anchorRef?.current) return;

    let timeoutId: ReturnType<typeof setTimeout>;
    const handleResize = () => {
      clearTimeout(timeoutId);
      timeoutId = setTimeout(() => {
        if (anchorRef?.current) setAnchorRect(anchorRef.current.getBoundingClientRect());
      }, 50);
    };

    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      clearTimeout(timeoutId);
    };
  }, [isOpen, anchorRef]);

  const handleFieldChange = useCallback((id: string, value: string | number | boolean) => {
    onFieldsChange(
      fields.map(f => f.id === id ? { ...f, value } : f)
    );
  }, [fields, onFieldsChange]);

  const renderValueInput = useCallback((field: ParamField) => {
    // Check if field is changeable (default: true)
    const isChangeable = field.changeable !== false;
    const disabledClass = !isChangeable ? 'opacity-60 cursor-not-allowed' : '';

    switch (field.type) {
      case 'boolean':
        return (
          <button
            type="button"
            title={field['tooltip-info']}
            disabled={!isChangeable}
            onClick={() => isChangeable && handleFieldChange(field.id, !field.value)}
            className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
              field.value ? 'bg-[#76b900]' : 'bg-gray-300 dark:bg-gray-600'
            } ${disabledClass}`}
          >
            <span
              className={`inline-block h-4 w-4 transform rounded-full bg-white shadow-md transition-transform ${
                field.value ? 'translate-x-6' : 'translate-x-1'
              }`}
            />
          </button>
        );
      case 'select':
        return (
          <select
            title={field['tooltip-info']}
            disabled={!isChangeable}
            value={field.value as string}
            onChange={(e) => handleFieldChange(field.id, e.target.value)}
            className={`${inputClass} ${disabledClass}`}
          >
            {field.options?.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        );
      case 'number':
        return (
          <input
            type="number"
            title={field['tooltip-info']}
            disabled={!isChangeable}
            step="any"
            value={field.value as number}
            onChange={(e) => handleFieldChange(field.id, parseFloat(e.target.value) || 0)}
            className={`${inputClass} ${disabledClass}`}
          />
        );
      default: // string
        return (
          <input
            type="text"
            title={field['tooltip-info']}
            disabled={!isChangeable}
            value={field.value as string}
            onChange={(e) => handleFieldChange(field.id, e.target.value)}
            className={`${inputClass} ${disabledClass}`}
          />
        );
    }
  }, [handleFieldChange]);

  if (!isOpen) return null;

  const dialogContent = (
    <>
      {/* Invisible backdrop to capture outside clicks */}
      <div
        className="fixed inset-0"
        style={{ zIndex: OVERLAY_Z }}
        onClick={onClose}
        aria-hidden
      />
      {/* Dialog - fixed position when portaled so it stacks above menu bar */}
      <div
        className="min-w-60 max-w-80 bg-white dark:bg-black rounded-xl shadow-2xl border border-gray-200 dark:border-gray-700 overflow-hidden"
        style={
          anchorRect
            ? {
                position: 'fixed',
                bottom: window.innerHeight - anchorRect.top + 8,
                right: window.innerWidth - anchorRect.right,
                zIndex: OVERLAY_Z + 1,
              }
            : undefined
        }
        role="dialog"
        aria-label="Agent parameters"
      >
        <div className="p-4 space-y-4 max-h-[400px] overflow-y-auto">
            {fields.length === 0 ? (
              <p className="text-sm text-gray-500 dark:text-gray-400 text-center py-4">
                No parameters configured.
              </p>
            ) : (
              fields.map((field) => (
                <div key={field.id} className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <label
                      className="text-sm font-medium text-gray-700 dark:text-gray-300"
                      title={field['tooltip-info']}
                    >
                      {field.label}
                    </label>
                    {field.type === 'boolean' && renderValueInput(field)}
                  </div>
                  {field.type !== 'boolean' && (
                    <div>{renderValueInput(field)}</div>
                  )}
                </div>
              ))
            )}
          </div>
      </div>
    </>
  );

  // Portal to body so z-index stacks above ChatHeader menu bar
  if (typeof document !== 'undefined') {
    return createPortal(dialogContent, document.body);
  }
  return dialogContent;
};

// Helper function to convert fields to payload object
export const fieldsToParams = (fields: ParamField[]): CustomAgentParamsValues => 
  (fields || []).reduce((acc, field) => {
    if (field.name) {
      acc[field.name] = field.value;
    }
    return acc;
  }, {} as CustomAgentParamsValues);

// Parse JSON string to ParamField array
// Format: { "params": [{ "name": "...", "label": "...", "type": "...", "default-value": ... }] }
export const parseParamsJson = (jsonString?: string): ParamField[] => {
  try {
    if (!jsonString) return [];
    
    const parsed = JSON.parse(jsonString) as { params: ParamFieldConfig[] };
    if (!parsed.params || !Array.isArray(parsed.params)) return [];
    
    return parsed.params.map((item) => ({
      ...item,
      id: generateId(),
      value: item['default-value'],
    }));
  } catch (e) {
    console.error('Failed to parse customAgentParamsJson:', e);
    return [];
  }
};

// Storage key for persisting custom agent params values
const STORAGE_KEY_CUSTOM_AGENT_PARAMS = 'customAgentParamsValues';

/**
 * Load saved param values from sessionStorage
 */
const loadParamValuesFromStorage = (): CustomAgentParamsValues => {
  if (typeof window === 'undefined') return {};
  
  try {
    const stored = sessionStorage.getItem(STORAGE_KEY_CUSTOM_AGENT_PARAMS);
    return stored ? JSON.parse(stored) : {};
  } catch (error) {
    console.warn('Failed to load custom agent params from sessionStorage:', error);
    return {};
  }
};

/**
 * Save param values to sessionStorage
 */
const saveParamValuesToStorage = (fields: ParamField[]): void => {
  if (typeof window === 'undefined') return;
  
  try {
    sessionStorage.setItem(STORAGE_KEY_CUSTOM_AGENT_PARAMS, JSON.stringify(fieldsToParams(fields)));
  } catch (error) {
    console.warn('Failed to save custom agent params to sessionStorage:', error);
  }
};

// Hook to initialize param fields from JSON string (from context/state)
// Values are persisted to sessionStorage and restored on page refresh
export const useInitialParamFields = (customAgentParamsJson?: string): [ParamField[], React.Dispatch<React.SetStateAction<ParamField[]>>] => {
  const [fields, setFields] = useState<ParamField[]>([]);
  const initialized = useRef(false);
  
  // Initialize fields from JSON and restore saved values from sessionStorage
  useEffect(() => {
    if (!initialized.current && customAgentParamsJson) {
      initialized.current = true;
      const parsedFields = parseParamsJson(customAgentParamsJson);
      
      // Load saved values from sessionStorage and merge with parsed fields
      // Validate type before applying to prevent injection of malicious values
      const savedValues = loadParamValuesFromStorage();
      const fieldsWithSavedValues = parsedFields.map(field => {
        if (!field.name || !(field.name in savedValues)) return field;
        const savedValue = savedValues[field.name];
        const isValidType = 
          (field.type === 'boolean' && typeof savedValue === 'boolean') ||
          (field.type === 'number' && typeof savedValue === 'number' && !isNaN(savedValue)) ||
          ((field.type === 'string' || field.type === 'select') && typeof savedValue === 'string');
        return isValidType ? { ...field, value: savedValue } : field;
      });
      
      setFields(fieldsWithSavedValues);
    }
  }, [customAgentParamsJson]);
  
  // Save to sessionStorage whenever fields change (after initialization)
  useEffect(() => {
    if (initialized.current && fields.length > 0) {
      saveParamValuesToStorage(fields);
    }
  }, [fields]);
  
  return [fields, setFields];
};

// For backwards compatibility
export const defaultParamFields: ParamField[] = [];
