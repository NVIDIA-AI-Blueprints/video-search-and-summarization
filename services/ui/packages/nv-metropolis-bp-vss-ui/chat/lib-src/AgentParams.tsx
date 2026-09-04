// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT AND Apache-2.0
/**
 * Per-turn agent parameters.
 *
 * The deployment describes the fields as JSON in
 * NEXT_PUBLIC_CHAT_API_CUSTOM_AGENT_PARAMS_JSON:
 *
 *   {"params":[{"name":"top_k","label":"Top K","type":"number","default-value":5}]}
 *
 * Values are merged into the request body, so a field named after a fixed field
 * (`messages`) must not be able to shadow it — `useChatStream` spreads params
 * first for exactly that reason.
 */
import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { createRandomId } from './id';
import type { CustomAgentParamsValues, ParamField, ParamFieldConfig } from './types';

const STORAGE_KEY = 'vss-chat-custom-agent-params';

export const fieldsToParams = (fields: ParamField[]): CustomAgentParamsValues =>
  (fields || []).reduce((acc, field) => {
    if (field.name) acc[field.name] = field.value;
    return acc;
  }, {} as CustomAgentParamsValues);

export const parseParamsJson = (jsonString?: string): ParamField[] => {
  try {
    if (!jsonString) return [];
    const parsed = JSON.parse(jsonString) as { params?: ParamFieldConfig[] };
    if (!Array.isArray(parsed?.params)) return [];
    return parsed.params.map((item) => ({
      ...item,
      id: createRandomId(),
      value: item['default-value'],
    }));
  } catch (error) {
    console.warn('vss-chat: could not parse customAgentParamsJson', error);
    return [];
  }
};

function loadSavedValues(): CustomAgentParamsValues {
  if (typeof window === 'undefined') return {};
  try {
    const stored = sessionStorage.getItem(STORAGE_KEY);
    return stored ? JSON.parse(stored) : {};
  } catch {
    return {};
  }
}

/**
 * Fields from config, with any value the user previously chose restored.
 *
 * A saved value is only applied when its type still matches the field's — the
 * store is sessionStorage, which page script can write, and a string where a
 * number is expected would reach the agent as-is.
 */
export function useParamFields(
  customAgentParamsJson?: string,
): [ParamField[], React.Dispatch<React.SetStateAction<ParamField[]>>] {
  const [fields, setFields] = useState<ParamField[]>([]);
  const initialized = useRef(false);

  useEffect(() => {
    if (initialized.current || !customAgentParamsJson) return;
    initialized.current = true;

    const saved = loadSavedValues();
    setFields(
      parseParamsJson(customAgentParamsJson).map((field) => {
        if (!field.name || !(field.name in saved)) return field;
        const value = saved[field.name];
        const validType =
          (field.type === 'boolean' && typeof value === 'boolean') ||
          (field.type === 'number' && typeof value === 'number' && !Number.isNaN(value)) ||
          ((field.type === 'string' || field.type === 'select') && typeof value === 'string');
        return validType ? { ...field, value } : field;
      }),
    );
  }, [customAgentParamsJson]);

  useEffect(() => {
    if (!initialized.current || !fields.length || typeof window === 'undefined') return;
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(fieldsToParams(fields)));
    } catch {
      // Quota or a locked-down storage policy; the values just will not persist.
    }
  }, [fields]);

  return [fields, setFields];
}

const inputClass =
  'w-full rounded-md border border-gray-200 bg-gray-50 px-2 py-1.5 text-sm text-gray-900 focus:outline-none focus:ring-1 focus:ring-[#76b900] dark:border-gray-600 dark:bg-black dark:text-white';

export interface AgentParamsProps {
  isOpen: boolean;
  onClose: () => void;
  fields: ParamField[];
  onFieldsChange: (fields: ParamField[]) => void;
  anchorRef?: React.RefObject<HTMLElement | null>;
  /** True while a turn is in flight: values are frozen mid-request. */
  valuesChangeDisabled?: boolean;
}

export const AgentParams: React.FC<AgentParamsProps> = ({
  isOpen,
  onClose,
  fields,
  onFieldsChange,
  anchorRef,
  valuesChangeDisabled = false,
}) => {
  const [anchorRect, setAnchorRect] = useState<DOMRect | null>(null);

  // Portalled to <body> so the chat input's overflow cannot clip it, which
  // means the position has to be tracked by hand.
  useLayoutEffect(() => {
    if (!isOpen || !anchorRef?.current) {
      setAnchorRect(null);
      return;
    }
    const element = anchorRef.current;
    const update = () => setAnchorRect(element.getBoundingClientRect());
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    window.addEventListener('scroll', update, true);
    window.addEventListener('resize', update);
    return () => {
      observer.disconnect();
      window.removeEventListener('scroll', update, true);
      window.removeEventListener('resize', update);
    };
  }, [isOpen, anchorRef]);

  useEffect(() => {
    if (!isOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [isOpen, onClose]);

  const handleFieldChange = useCallback(
    (id: string, value: string | number | boolean) => {
      onFieldsChange(fields.map((f) => (f.id === id ? { ...f, value } : f)));
    },
    [fields, onFieldsChange],
  );

  if (!isOpen || typeof document === 'undefined') return null;

  const renderInput = (field: ParamField) => {
    const editable = field.changeable !== false && !valuesChangeDisabled;
    const disabledClass = editable ? '' : 'opacity-60 cursor-not-allowed';

    switch (field.type) {
      case 'boolean':
        return (
          <button
            type="button"
            role="switch"
            aria-checked={field.value === true}
            title={field['tooltip-info']}
            disabled={!editable}
            onClick={() => handleFieldChange(field.id, !field.value)}
            className={`relative inline-flex h-5 w-10 items-center rounded-full transition-colors ${
              field.value ? 'bg-[#76b900]' : 'bg-gray-300 dark:bg-gray-600'
            } ${disabledClass}`}
          >
            <span
              className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                field.value ? 'translate-x-5' : 'translate-x-0.5'
              }`}
            />
          </button>
        );
      case 'number':
        return (
          <input
            type="number"
            className={`${inputClass} ${disabledClass}`}
            title={field['tooltip-info']}
            disabled={!editable}
            value={String(field.value ?? '')}
            onChange={(e) => {
              const next = e.target.value === '' ? '' : Number(e.target.value);
              // Reject NaN rather than sending it: JSON.stringify turns it into
              // null and the agent sees a missing parameter.
              if (next === '' || !Number.isNaN(next)) {
                handleFieldChange(field.id, next === '' ? '' : (next as number));
              }
            }}
          />
        );
      case 'select':
        return (
          <select
            className={`${inputClass} ${disabledClass}`}
            title={field['tooltip-info']}
            disabled={!editable}
            value={String(field.value ?? '')}
            onChange={(e) => handleFieldChange(field.id, e.target.value)}
          >
            {(field.options ?? []).map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        );
      default:
        return (
          <input
            type="text"
            className={`${inputClass} ${disabledClass}`}
            title={field['tooltip-info']}
            disabled={!editable}
            value={String(field.value ?? '')}
            onChange={(e) => handleFieldChange(field.id, e.target.value)}
          />
        );
    }
  };

  const top = anchorRect ? anchorRect.bottom + 8 : 80;
  const right = anchorRect ? Math.max(8, window.innerWidth - anchorRect.right) : 16;

  return createPortal(
    <>
      <div className="fixed inset-0 z-[99]" onClick={onClose} aria-hidden />
      <div
        role="dialog"
        aria-label="Agent parameters"
        className="fixed z-[100] w-72 rounded-md border border-gray-200 bg-white p-3 shadow-lg dark:border-gray-700 dark:bg-black"
        style={{ top, right, maxHeight: '60vh', overflowY: 'auto' }}
      >
        <p className="mb-2 text-sm font-medium text-gray-700 dark:text-gray-200">
          Agent parameters
        </p>
        <div className="flex flex-col gap-3">
          {fields.map((field) => (
            <label key={field.id} className="flex flex-col gap-1">
              <span className="text-xs text-gray-600 dark:text-gray-400">{field.label}</span>
              {renderInput(field)}
            </label>
          ))}
        </div>
      </div>
    </>,
    document.body,
  );
};
