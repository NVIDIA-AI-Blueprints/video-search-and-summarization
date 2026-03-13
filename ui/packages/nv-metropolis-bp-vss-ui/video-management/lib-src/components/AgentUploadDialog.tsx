// SPDX-License-Identifier: MIT
import React from 'react';
import { IconChevronDown, IconVideo, IconX } from '@tabler/icons-react';

const INPUT_CLASS =
  'w-full rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 focus:border-[#76b900] focus:outline-none focus:ring-1 focus:ring-[#76b900] dark:border-gray-600 dark:bg-[#343541] dark:text-gray-300';
const POPUP_OVERLAY_CLASS = 'fixed inset-0 z-50 flex items-center justify-center bg-black/50';
const POPUP_CONTAINER_CLASS = 'mx-4 w-full max-w-xl rounded-lg bg-white p-6 shadow-xl dark:bg-[#343541]';

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
  onClose: () => void;
  onConfirmUpload: () => void;
  onToggleExpand: (fileId: string) => void;
  onRemoveFile: (fileId: string) => void;
  onFieldChange: (fileId: string, fieldName: string, value: any) => void;
}

export const AgentUploadDialog: React.FC<AgentUploadDialogProps> = ({
  open,
  files,
  configTemplate,
  onAddMore,
  onClose,
  onConfirmUpload,
  onToggleExpand,
  onRemoveFile,
  onFieldChange,
}) => {
  if (!open) return null;

  const renderField = (fileItem: AgentUploadFileItem, field: any) => {
    const fieldName = field['field-name'];
    const value = fileItem.formData[fieldName] ?? field['field-default-value'];
    const isChangeable = field['changeable'] !== false;

    if (field['field-type'] === 'boolean') {
      return (
        <label
          className={`flex items-center gap-2 ${
            isChangeable ? 'cursor-pointer' : 'cursor-not-allowed opacity-60'
          }`}
        >
          <button
            type="button"
            role="switch"
            aria-checked={value}
            disabled={!isChangeable}
            onClick={() => isChangeable && onFieldChange(fileItem.id, fieldName, !value)}
            className={`relative inline-flex h-5 w-9 flex-shrink-0 rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-[#76b900] focus:ring-offset-2 ${
              value ? 'bg-[#76b900]' : 'bg-gray-300 dark:bg-gray-600'
            } ${isChangeable ? 'cursor-pointer' : 'cursor-not-allowed opacity-60'}`}
          >
            <span
              className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
                value ? 'translate-x-4' : 'translate-x-0'
              }`}
            />
          </button>
          <span className="text-sm text-gray-700 dark:text-gray-300">{value ? 'Yes' : 'No'}</span>
        </label>
      );
    }

    if (field['field-type'] === 'select') {
      return (
        <select
          value={value}
          disabled={!isChangeable}
          onChange={(e) => onFieldChange(fileItem.id, fieldName, e.target.value)}
          className={`${INPUT_CLASS} ${!isChangeable ? 'cursor-not-allowed opacity-60' : ''}`}
        >
          {field['field-options']?.map((opt: any) => (
            <option key={String(opt)} value={String(opt)}>
              {String(opt)}
            </option>
          ))}
        </select>
      );
    }

    if (field['field-type'] === 'number') {
      return (
        <input
          type="number"
          value={value}
          disabled={!isChangeable}
          onChange={(e) => onFieldChange(fileItem.id, fieldName, Number(e.target.value))}
          className={`${INPUT_CLASS} ${!isChangeable ? 'cursor-not-allowed opacity-60' : ''}`}
        />
      );
    }

    return (
      <input
        type="text"
        value={value}
        disabled={!isChangeable}
        onChange={(e) => onFieldChange(fileItem.id, fieldName, e.target.value)}
        className={`${INPUT_CLASS} ${!isChangeable ? 'cursor-not-allowed opacity-60' : ''}`}
        placeholder={`Enter ${fieldName}`}
      />
    );
  };

  return (
    <div className={POPUP_OVERLAY_CLASS}>
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
              <button
                onClick={onAddMore}
                className="flex items-center gap-1 rounded-lg bg-[#76b900] px-2 py-1 text-xs font-medium text-white transition-colors hover:bg-[#5a8f00]"
              >
                + Add More
              </button>
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
                    <div className="flex items-center justify-between bg-white p-3 dark:bg-[#343541]">
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
                        className="ml-2 flex-shrink-0 text-gray-500 hover:text-red-500"
                        aria-label="Remove file"
                      >
                        <IconX size={18} />
                      </button>
                    </div>

                    {hasExpandableContent && item.isExpanded && (
                      <div className="border-t border-gray-200 bg-gray-50 p-3 dark:border-gray-600 dark:bg-[#2a2a36]">
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
              onClick={onAddMore}
              className="w-full cursor-pointer rounded-lg border-2 border-dashed p-4 text-center transition-colors border-gray-300 hover:border-[#76b900] hover:bg-gray-50 dark:border-gray-600 dark:hover:border-[#76b900] dark:hover:bg-gray-800"
            >
              <span className="text-sm font-medium text-gray-700 dark:text-gray-300">
                Click or drag files here
              </span>
              <div className="mt-2 text-xs text-gray-500 dark:text-gray-400">Movie Files (mp4, mkv)</div>
            </div>
          )}
        </div>

        <div className="flex gap-3">
          <button
            onClick={onClose}
            className="flex-1 rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-200 dark:hover:bg-gray-600"
          >
            Cancel
          </button>
          <button
            onClick={onConfirmUpload}
            className={`flex-1 rounded-lg px-4 py-2 text-sm font-medium text-white transition-colors ${
              files.length > 0 ? 'bg-[#76b900] hover:bg-[#5a8f00]' : 'bg-gray-400 cursor-not-allowed'
            }`}
            disabled={files.length === 0}
          >
            Upload {files.length > 0 ? `(${files.length})` : ''}
          </button>
        </div>
      </div>
    </div>
  );
};
