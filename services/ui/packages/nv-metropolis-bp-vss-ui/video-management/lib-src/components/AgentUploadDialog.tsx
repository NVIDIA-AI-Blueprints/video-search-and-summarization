// SPDX-License-Identifier: MIT
import React, { useState, useCallback } from 'react';
import { Button, TextInput, Select } from '@nvidia/foundations-react-core';
import { IconChevronDown, IconVideo, IconX } from '@tabler/icons-react';

const ACCEPTED_EXTENSIONS = ['.mp4', '.mkv'];

const POPUP_OVERLAY_VIEWPORT = 'fixed inset-0 z-50 flex items-center justify-center bg-black/50';
/** Covers only the parent `relative` region (e.g. Video Management main pane), not the whole browser window */
const POPUP_OVERLAY_CONTAINED = 'absolute inset-0 z-40 flex items-center justify-center bg-black/50';
const POPUP_CONTAINER_CLASS = 'mx-4 w-full max-w-xl rounded-lg bg-white p-6 shadow-xl dark:bg-neutral-900';

interface AgentUploadFileItem {
  id: string;
  file: File;
  isExpanded: boolean;
  formData: Record<string, any>;
}

interface AgentUploadDialogProps {
  open: boolean;
  files: AgentUploadFileItem[];
  configTemplate: any;
  onAddMore: () => void;
  onFilesDropped: (files: File[]) => void;
  onClose: () => void;
  onConfirmUpload: () => void;
  onToggleExpand: (fileId: string) => void;
  onRemoveFile: (fileId: string) => void;
  onFieldChange: (fileId: string, fieldName: string, value: any) => void;
  /** `contained` = overlay only the nearest positioned ancestor (Video Management pane). Default `viewport` = full window. */
  overlay?: 'viewport' | 'contained';
}

export const AgentUploadDialog: React.FC<AgentUploadDialogProps> = ({
  open,
  files,
  configTemplate,
  onAddMore,
  onFilesDropped,
  onClose,
  onConfirmUpload,
  onToggleExpand,
  onRemoveFile,
  onFieldChange,
  overlay = 'viewport',
}) => {
  const [isDragOver, setIsDragOver] = useState(false);

  const handleDrop = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragOver(false);
    const dropped = Array.from(e.dataTransfer.files).filter((f) =>
      ACCEPTED_EXTENSIONS.some((ext) => f.name.toLowerCase().endsWith(ext)),
    );
    if (dropped.length > 0) onFilesDropped(dropped);
  }, [onFilesDropped]);

  const handleDragOver = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
  }, []);

  const handleDragEnter = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragOver(false);
  }, []);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onAddMore();
    }
  }, [onAddMore]);

  if (!open) return null;

  const renderField = (fileItem: AgentUploadFileItem, field: any) => {
    const fieldName = field['field-name'];
    const value = fileItem.formData[fieldName] ?? field['field-default-value'];
    const isChangeable = field['changeable'] !== false;

    if (field['field-type'] === 'boolean') {
      return (
        <label
          className={`flex items-center gap-3 ${
            isChangeable ? 'cursor-pointer' : 'cursor-not-allowed opacity-60'
          }`}
        >
          <button
            type="button"
            role="switch"
            aria-checked={value}
            disabled={!isChangeable}
            onClick={() => isChangeable && onFieldChange(fileItem.id, fieldName, !value)}
            className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
              value ? 'bg-[#76b900]' : 'bg-neutral-600'
            } ${!isChangeable ? 'opacity-60 cursor-not-allowed' : ''}`}
          >
            <span
              className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
                value ? 'translate-x-6' : 'translate-x-1'
              }`}
            />
          </button>
          <span className="text-sm text-gray-400 dark:text-gray-400">{value ? 'Yes' : 'No'}</span>
        </label>
      );
    }

    if (field['field-type'] === 'select') {
      return (
        <Select
          value={String(value)}
          disabled={!isChangeable}
          onValueChange={(val: string) => onFieldChange(fileItem.id, fieldName, val)}
          items={field['field-options']?.map((opt: any) => String(opt)) ?? []}
        />
      );
    }

    if (field['field-type'] === 'number') {
      return (
        <TextInput
          type="number"
          value={String(value)}
          disabled={!isChangeable}
          onValueChange={(val: string) => onFieldChange(fileItem.id, fieldName, Number(val))}
        />
      );
    }

    return (
      <TextInput
        value={String(value ?? '')}
        disabled={!isChangeable}
        onValueChange={(val: string) => onFieldChange(fileItem.id, fieldName, val)}
        placeholder={`Enter ${fieldName}`}
      />
    );
  };

  const overlayClass =
    overlay === 'contained' ? POPUP_OVERLAY_CONTAINED : POPUP_OVERLAY_VIEWPORT;

  return (
    <div className={overlayClass}>
      <div className={POPUP_CONTAINER_CLASS}>
        <h3 className="mb-6 text-center text-lg font-semibold text-gray-900 dark:text-white">
          Upload Files
        </h3>

        {/* Files list */}
        <div className="mb-4">
          <div className="mb-2 flex items-center justify-between">
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
              Files <span className="text-red-500">*</span>
              {files.length > 0 && (
                <span className="ml-2 rounded-full bg-[#76b900] px-2 py-0.5 text-xs text-white">
                  {files.length}
                </span>
              )}
            </label>
            {files.length > 0 && (
              <Button
                kind="primary"
                onClick={onAddMore}
              >
                + Add More
              </Button>
            )}
          </div> 

          {files.length > 0 ? (
            <div className="max-h-96 space-y-2 overflow-y-auto">
              {files.map((item) => {
                const hasExpandableContent = configTemplate && Array.isArray(configTemplate.fields) && configTemplate.fields.length > 0;
                return (
                  <div
                    key={item.id}
                    className="overflow-hidden rounded-lg border border-gray-300 dark:border-gray-600"
                  >
                    <div className="flex items-center justify-between bg-white p-3 dark:bg-neutral-900">
                      <div
                        className={`flex flex-1 items-center gap-2 overflow-hidden ${hasExpandableContent ? 'cursor-pointer' : ''}`}
                        onClick={() => hasExpandableContent && onToggleExpand(item.id)}
                      >
                        {hasExpandableContent && (
                          <IconChevronDown
                            size={16}
                            className={`flex-shrink-0 text-gray-400 transition-transform duration-200 ${
                              item.isExpanded ? 'rotate-180' : ''
                            }`}
                          />
                        )}
                        <IconVideo size={18} className="flex-shrink-0 text-[#76b900]" />
                        <span className="truncate text-sm text-gray-700 dark:text-gray-300">
                          {item.file.name}
                        </span>
                        <span className="flex-shrink-0 text-xs text-gray-400">
                          ({(item.file.size / 1024 / 1024).toFixed(2)} MB)
                        </span>
                      </div>
                      <button
                        onClick={() => onRemoveFile(item.id)}
                        aria-label="Remove file"
                        className="p-1.5 rounded transition-colors text-gray-400 hover:text-gray-200 hover:bg-neutral-700 dark:text-gray-400 dark:hover:text-white dark:hover:bg-neutral-700"
                      >
                        <IconX size={18} />
                      </button>
                    </div>

                    {hasExpandableContent && item.isExpanded && (
                      <div className="border-t border-gray-200 bg-gray-50 p-3 dark:border-gray-600 dark:bg-neutral-800">
                        <div className="mb-3 space-y-3">
                          {configTemplate.fields.map((field: any) => (
                            <div key={field['field-name']} className="flex items-center gap-3">
                              <label className="w-24 flex-shrink-0 text-xs font-medium text-gray-600 dark:text-gray-400">
                                {field['field-name']}
                              </label>
                              <div className="flex-1">{renderField(item, field)}</div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            <div
              role="button"
              tabIndex={0}
              aria-label="Click or drag movie files here to upload (mp4, mkv)"
              onClick={onAddMore}
              onKeyDown={handleKeyDown}
              onDragOver={handleDragOver}
              onDragEnter={handleDragEnter}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              className={`w-full cursor-pointer rounded-lg border-2 border-dashed p-4 text-center transition-colors ${
                isDragOver
                  ? 'border-[#76b900] bg-[#76b900]/10 dark:border-[#76b900] dark:bg-[#76b900]/10'
                  : 'border-gray-300 hover:border-[#76b900] hover:bg-gray-50 dark:border-gray-600 dark:hover:border-[#76b900] dark:hover:bg-black'
              }`}
            >
              <span className="text-sm font-medium text-gray-700 dark:text-gray-300">
                Click or drag files here
              </span>
              <div className="mt-2 text-xs text-gray-500 dark:text-gray-400">Movie Files (mp4, mkv)</div>
            </div>
          )}
        </div>

        <div className="flex gap-3">
          <Button
            kind="secondary"
            onClick={onClose}
          >
            Cancel
          </Button>
          <Button
            kind="primary"
            onClick={onConfirmUpload}
            disabled={files.length === 0}
          >
            Upload {files.length > 0 ? `(${files.length})` : ''}
          </Button>
        </div>
      </div>
    </div>
  );
};
