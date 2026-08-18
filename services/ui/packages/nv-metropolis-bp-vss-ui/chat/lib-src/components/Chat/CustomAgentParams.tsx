// SPDX-License-Identifier: MIT
/**
 * Agent parameter panel.
 *
 * A deployment declares the knobs its agent accepts as JSON
 * (NEXT_PUBLIC_CHAT_API_CUSTOM_AGENT_PARAMS_JSON); this renders them and folds
 * the values into every request. Fields are declarative so a new agent
 * parameter needs configuration, not a UI change.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';

export type ParamType = 'string' | 'number' | 'boolean' | 'select';

export interface ParamFieldConfig {
  name: string;
  label: string;
  type: ParamType;
  'default-value': string | number | boolean;
  options?: string[];
  /** Defaults to true. False pins the value, showing it without allowing edits. */
  changeable?: boolean;
  'tooltip-info'?: string;
}

export interface ParamField extends ParamFieldConfig {
  id: string;
  value: string | number | boolean;
}

export type CustomAgentParamsValues = Record<string, string | number | boolean>;

export interface CustomAgentParamsProps {
  isOpen: boolean;
  onClose: () => void;
  fields: ParamField[];
  onFieldsChange: (fields: ParamField[]) => void;
  /** Positions the panel beneath its trigger. */
  anchorRef?: React.RefObject<HTMLElement>;
  /** Locks every value, e.g. while a query is in flight. */
  valuesChangeDisabled?: boolean;
}

const INPUT_CLASS =
  'w-full px-2 py-1.5 text-sm bg-gray-50 dark:bg-black border border-gray-200 dark:border-gray-600 rounded-md text-gray-900 dark:text-white focus:outline-none focus:ring-1 focus:ring-[#76b900]';

/** Above the chat header's menu bar. */
const OVERLAY_Z = 100;

const generateId = () => Math.random().toString(36).substring(2, 11);

const STORAGE_KEY_CUSTOM_AGENT_PARAMS = 'customAgentParamsValues';

export const CustomAgentParams: React.FC<CustomAgentParamsProps> = ({
  isOpen,
  onClose,
  fields,
  onFieldsChange,
  anchorRef,
  valuesChangeDisabled = false,
}) => {
  const [anchorRect, setAnchorRect] = useState<DOMRect | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen || !anchorRef?.current) {
      setAnchorRect(null);
      return undefined;
    }

    const measure = () => setAnchorRect(anchorRef.current?.getBoundingClientRect() ?? null);
    measure();

    window.addEventListener('resize', measure);
    window.addEventListener('scroll', measure, true);
    return () => {
      window.removeEventListener('resize', measure);
      window.removeEventListener('scroll', measure, true);
    };
  }, [isOpen, anchorRef]);

  useEffect(() => {
    if (!isOpen) return undefined;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };

    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [isOpen, onClose]);

  const handleFieldChange = useCallback(
    (id: string, value: string | number | boolean) => {
      onFieldsChange(
        fields.map((field) => (field.id === id ? { ...field, value } : field)),
      );
    },
    [fields, onFieldsChange],
  );

  const renderValueInput = useCallback(
    (field: ParamField) => {
      // A field is editable unless the deployment pinned it or a query is running.
      const isChangeable = field.changeable !== false && !valuesChangeDisabled;
      const disabledClass = isChangeable ? '' : 'opacity-60 cursor-not-allowed';

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
              onChange={(event) => handleFieldChange(field.id, event.target.value)}
              className={`${INPUT_CLASS} ${disabledClass}`}
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
              onChange={(event) =>
                handleFieldChange(field.id, parseFloat(event.target.value) || 0)
              }
              className={`${INPUT_CLASS} ${disabledClass}`}
            />
          );

        default:
          return (
            <input
              type="text"
              title={field['tooltip-info']}
              disabled={!isChangeable}
              value={field.value as string}
              onChange={(event) => handleFieldChange(field.id, event.target.value)}
              className={`${INPUT_CLASS} ${disabledClass}`}
            />
          );
      }
    },
    [handleFieldChange, valuesChangeDisabled],
  );

  if (!isOpen) return null;

  return (
    <>
      {/* Click-away layer: the panel has no close control of its own. */}
      <div
        className="fixed inset-0"
        style={{ zIndex: OVERLAY_Z }}
        onClick={onClose}
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-label="Agent Parameters"
        className="absolute w-80 rounded-md border border-gray-200 bg-white p-3 shadow-lg dark:border-gray-600 dark:bg-neutral-900"
        style={{
          zIndex: OVERLAY_Z + 1,
          top: anchorRect ? anchorRect.bottom + 8 : undefined,
          left: anchorRect ? anchorRect.left : undefined,
        }}
      >
        {fields.map((field) => (
          <div key={field.id} className="mb-3 last:mb-0">
            <label className="mb-1 block text-xs text-gray-600 dark:text-gray-300">
              {field.label}
            </label>
            {renderValueInput(field)}
          </div>
        ))}
      </div>
    </>
  );
};

/** Flattens fields into the `{ name: value }` map sent with a request. */
export const fieldsToParams = (fields: ParamField[]): CustomAgentParamsValues =>
  (fields || []).reduce((values, field) => {
    if (field.name) values[field.name] = field.value;
    return values;
  }, {} as CustomAgentParamsValues);

/**
 * Reads the deployment's declared parameters.
 *
 * Malformed configuration yields no fields rather than throwing: a bad env
 * value should cost the parameter panel, not the whole chat.
 */
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
  } catch (error) {
    console.error('Failed to parse customAgentParamsJson:', error);
    return [];
  }
};

function loadParamValuesFromStorage(): CustomAgentParamsValues {
  if (typeof window === 'undefined') return {};

  try {
    const stored = sessionStorage.getItem(STORAGE_KEY_CUSTOM_AGENT_PARAMS);
    return stored ? JSON.parse(stored) : {};
  } catch (error) {
    console.warn('Failed to load custom agent params from sessionStorage:', error);
    return {};
  }
}

function saveParamValuesToStorage(fields: ParamField[]): void {
  if (typeof window === 'undefined') return;

  try {
    sessionStorage.setItem(
      STORAGE_KEY_CUSTOM_AGENT_PARAMS,
      JSON.stringify(fieldsToParams(fields)),
    );
  } catch (error) {
    console.warn('Failed to save custom agent params to sessionStorage:', error);
  }
}

/**
 * Builds the field list from configuration, restoring values the user
 * previously set so a reload does not reset their choices.
 */
export const useInitialParamFields = (
  customAgentParamsJson?: string,
): [ParamField[], React.Dispatch<React.SetStateAction<ParamField[]>>] => {
  const [fields, setFields] = useState<ParamField[]>([]);

  useEffect(() => {
    const declared = parseParamsJson(customAgentParamsJson);
    if (declared.length === 0) {
      setFields([]);
      return;
    }

    const savedValues = loadParamValuesFromStorage();
    setFields(
      declared.map((field) =>
        field.name in savedValues ? { ...field, value: savedValues[field.name] } : field,
      ),
    );
  }, [customAgentParamsJson]);

  useEffect(() => {
    if (fields.length > 0) saveParamValuesToStorage(fields);
  }, [fields]);

  return [fields, setFields];
};

export const defaultParamFields: ParamField[] = [];
