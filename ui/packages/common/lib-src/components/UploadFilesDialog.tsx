import React, { useCallback, useState, useRef, useEffect, useImperativeHandle, forwardRef, useMemo } from 'react';
import toast from 'react-hot-toast';
import {
  IconVideoPlus,
  IconX,
  IconFileCode,
  IconChevronDown,
  IconPlus,
  IconVideo,
  IconAlertTriangle,
} from '@tabler/icons-react';
import type { UploadFileConfigTemplate, UploadFileFieldConfig } from '../types/uploadFileConfig';

const INPUT_CLASS =
  'w-full rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 focus:border-[#76b900] focus:outline-none focus:ring-1 focus:ring-[#76b900] dark:border-neutral-700 dark:bg-black dark:text-gray-300';
const POPUP_OVERLAY_CLASS =
  'fixed inset-0 z-50 flex items-center justify-center bg-black/50';
const POPUP_CONTAINER_CLASS =
  'mx-4 w-full max-w-xl rounded-lg bg-white p-6 shadow-xl dark:bg-black dark:border dark:border-neutral-800';

export interface UploadFilesDialogFileItem {
  id: string;
  file: File;
  formData: Record<string, any>;
  isExpanded: boolean;
  /** Editable filename sent to upload API (defaults to file.name) */
  uploadFilename?: string;
  metadataFile?: File | null;
  isMetadataExpanded?: boolean;
}

/** Result passed to parent when user confirms upload */
export interface UploadFilesDialogEntry {
  id: string;
  file: File;
  formData: Record<string, any>;
  /** Filename to use for upload request (defaults to file.name if not set) */
  uploadFilename?: string;
  metadataFile?: File | null;
}

function getFieldValue(
  formData: Record<string, any>,
  field: UploadFileFieldConfig
): unknown {
  return formData[field['field-name']] ?? field['field-default-value'];
}

function toDisplayString(val: unknown): string {
  if (val == null) return '';
  if (typeof val === 'string') return val;
  if (typeof val === 'number' || typeof val === 'boolean') return String(val);
  if (typeof val === 'object') return JSON.stringify(val);
  return '';
}

/** Optional: enable per-file metadata (JSON); validation is internal unless overridden */
export interface UploadFilesDialogMetadataConfig {
  enabled: true;
  /** Return true if file is valid (default: JSON parse check) */
  validateMetadataFile?: (file: File) => Promise<boolean>;
}

/** Optional: dialog UI tweaks */
export interface UploadFilesDialogOptions {
  title?: string;
  emptyStateHint?: React.ReactNode;
  addMoreWithIcon?: boolean;
}

const DEFAULT_ACCEPT = '.mp4,.mkv,video/mp4,video/x-matroska';
const DEFAULT_VALIDATE_FILE = (file: File) => {
  const allowedExtensions = /\.(mp4|mkv)$/i;
  const allowedMimeTypes = ['video/mp4', 'video/x-matroska'];
  return allowedExtensions.test(file.name) || allowedMimeTypes.includes(file.type);
};
const DEFAULT_VALIDATE_METADATA = async (file: File) => {
  if (!file.name.endsWith('.json')) return false;
  try {
    const content = await file.text();
    JSON.parse(content);
    return true;
  } catch {
    return false;
  }
};

/** Ref handle when using imperative API (omit `open` / `initialFiles` and use ref.open(files) instead) */
export interface UploadFilesDialogHandle {
  open: (files?: File[]) => void;
  close: () => void;
}

export interface UploadFilesDialogProps {
  /** When provided, dialog is controlled by parent. When omitted, use ref.open() / ref.close() (imperative). */
  open?: boolean;
  configTemplate: UploadFileConfigTemplate | null;
  /** Called when dialog closes (parent should set open=false in controlled mode) */
  onClose: () => void;
  /** Called with selected entries when user clicks Upload */
  onConfirm: (entries: UploadFilesDialogEntry[]) => void;
  /** Seed the list when dialog opens (controlled mode only; in imperative mode pass to ref.open(files)) */
  initialFiles?: File[] | null;
  /** Accepted file types for the file input */
  accept?: string;
  /** Return true if file is allowed (default: video mp4/mkv) */
  validateFile?: (file: File) => boolean;
  /** When set, show metadata (JSON) section per file */
  metadata?: UploadFilesDialogMetadataConfig;
  /** UI options */
  options?: UploadFilesDialogOptions;
}

const DEFAULT_EMPTY_HINT = (
  <span className="rounded bg-gray-100 px-2 py-0.5 dark:bg-neutral-800">
    Movie Files (mp4, mkv)
  </span>
);

function generateDefaultFormData(configTemplate: UploadFileConfigTemplate | null): Record<string, any> {
  if (!configTemplate || !Array.isArray(configTemplate.fields)) return {};
  return configTemplate.fields.reduce((acc, field) => {
    acc[field['field-name']] = field['field-default-value'];
    return acc;
  }, {} as Record<string, any>);
}

function getFieldLabel(fieldName: string): string {
  return fieldName.charAt(0).toUpperCase() + fieldName.slice(1);
}

/** Sanitize upload filename: no spaces, remove .mp4 suffix if present (for default/display). */
function sanitizeUploadFilename(name: string): string {
  const noSpaces = name.replaceAll(/\s+/g, '');
  return noSpaces.replace(/\.(mp4|mkv)$/i, '') || noSpaces;
}

/** Check if upload filename is invalid (empty or contains spaces). */
function isUploadFilenameInvalid(uploadFilename?: string): boolean {
  const v = (uploadFilename ?? '').trim();
  return v.length === 0 || /\s/.test(uploadFilename ?? '');
}

/** Renders a single config template field (boolean, select, number, text) */
function ConfigFieldEditor({
  field,
  value,
  onChange,
  disabled,
}: Readonly<{
  field: UploadFileFieldConfig;
  value: unknown;
  onChange: (value: unknown) => void;
  disabled: boolean;
}>) {
  const fieldName = field['field-name'];
  const label = getFieldLabel(fieldName);
  const inputClass = `${INPUT_CLASS} ${disabled ? 'cursor-not-allowed opacity-60' : ''}`;
  const labelClass = `w-24 flex-shrink-0 text-xs font-medium text-gray-600 dark:text-gray-400`;
  const tooltip = field['tooltip-info'] ?? '';

  switch (field['field-type']) {
    case 'boolean':
      return (
        <div className="flex items-center gap-3">
          <label className={labelClass} title={tooltip}>{label}</label>
          <label className={`flex items-center gap-2 ${disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}`}>
            <button
              type="button"
              role="switch"
              aria-checked={!!value}
              disabled={disabled}
              onClick={() => onChange(!value)}
              className={`relative inline-flex h-5 w-9 flex-shrink-0 rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-[#76b900] focus:ring-offset-2 ${value ? 'bg-[#76b900]' : 'bg-gray-300 dark:bg-neutral-700'} ${disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}`}
            >
              <span className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${value ? 'translate-x-4' : 'translate-x-0'}`} />
            </button>
            <span className="text-sm text-gray-700 dark:text-gray-300">{value ? 'Yes' : 'No'}</span>
          </label>
        </div>
      );
    case 'select':
      return (
        <div className="flex items-center gap-3">
          <label className={labelClass} title={tooltip}>{label}</label>
          <select value={String(value)} disabled={disabled} onChange={(e) => onChange(e.target.value)} className={inputClass}>
            {field['field-options']?.map((opt) => (
              <option key={String(opt)} value={String(opt)}>{String(opt)}</option>
            ))}
          </select>
        </div>
      );
    case 'number': {
      const numValue = value == null || value === '' ? '' : Number(value);
      return (
        <div className="flex items-center gap-3">
          <label className={labelClass} title={tooltip}>{label}</label>
          <input
            type="number"
            value={numValue}
            disabled={disabled}
            onChange={(e) => {
              const v = e.target.value;
              onChange(v === '' ? '' : Number(v));
            }}
            className={inputClass}
          />
        </div>
      );
    }
    default:
      return (
        <div className="flex items-center gap-3">
          <label className={labelClass} title={tooltip}>{label}</label>
          <input type="text" value={toDisplayString(value)} disabled={disabled} onChange={(e) => onChange(e.target.value)} className={inputClass} placeholder={`Enter ${fieldName}`} />
        </div>
      );
  }
}

function EmptyDropZone({
  onPickFiles,
  onDragOver,
  onDragEnter,
  onDragLeave,
  onDrop,
  isDragging,
  emptyStateHint,
}: Readonly<{
  onPickFiles: () => void;
  onDragOver: (e: React.DragEvent) => void;
  onDragEnter: (e: React.DragEvent) => void;
  onDragLeave: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent) => void;
  isDragging: boolean;
  emptyStateHint: React.ReactNode;
}>) {
  return (
    <button
      type="button"
      onClick={onPickFiles}
      onDragOver={onDragOver}
      onDragEnter={onDragEnter}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      className={`w-full cursor-pointer rounded-lg border-2 border-dashed p-4 text-center transition-colors ${
        isDragging
          ? 'border-[#76b900] bg-[#76b900]/10'
          : 'border-gray-300 hover:border-[#76b900] hover:bg-gray-50 dark:border-neutral-700 dark:hover:border-[#76b900] dark:hover:bg-neutral-900'
      }`}
    >
      <IconVideoPlus size={40} className="mx-auto text-gray-400" />
      <span className="mt-2 block text-sm font-medium text-gray-700 dark:text-gray-300">
        {isDragging ? 'Drop files here' : 'Click or drag files here'}
      </span>
      <div className="mt-2 flex flex-wrap justify-center gap-2 text-xs text-gray-500 dark:text-gray-400">
        {emptyStateHint}
      </div>
    </button>
  );
}

export const UploadFilesDialog = forwardRef<UploadFilesDialogHandle, UploadFilesDialogProps>(function UploadFilesDialog(
  {
    open: openProp,
    configTemplate,
    onClose,
    onConfirm,
    initialFiles: initialFilesProp = null,
    accept = DEFAULT_ACCEPT,
    validateFile = DEFAULT_VALIDATE_FILE,
    metadata,
    options,
  },
  ref
) {
  // --- Open/close state (controlled vs imperative ref) ---
  const isControlled = openProp !== undefined;
  const [internalOpen, setInternalOpen] = useState(false);
  const [internalInitialFiles, setInternalInitialFiles] = useState<File[] | null>(null);
  const open = isControlled ? (openProp ?? false) : internalOpen;
  const initialFiles = isControlled ? (initialFilesProp ?? null) : internalInitialFiles;

  const handleClose = useCallback(() => {
    if (!isControlled) {
      setInternalOpen(false);
      setInternalInitialFiles(null);
    }
    onClose();
  }, [isControlled, onClose]);

  useImperativeHandle(
    ref,
    () => ({
      open: (files?: File[]) => {
        setInternalInitialFiles(files ?? null);
        setInternalOpen(true);
      },
      close: () => {
        setInternalOpen(false);
        setInternalInitialFiles(null);
        onClose();
      },
    }),
    [onClose]
  );

  const title = options?.title ?? 'Upload Files';
  const emptyStateHint = options?.emptyStateHint ?? DEFAULT_EMPTY_HINT;
  const addMoreWithIcon = options?.addMoreWithIcon ?? true;
  const metadataEnabled = metadata?.enabled === true;
  const validateMetadataFile = metadata?.validateMetadataFile ?? DEFAULT_VALIDATE_METADATA;

  const defaultFormData = useMemo(() => generateDefaultFormData(configTemplate), [configTemplate]);
  const hasConfigFields = useMemo(
    () => Boolean(configTemplate && Array.isArray(configTemplate.fields) && configTemplate.fields.length > 0),
    [configTemplate]
  );
  const hasExpandableContent = metadataEnabled || hasConfigFields;

  const createFileItem = useCallback(
    (file: File): UploadFilesDialogFileItem => ({
      id: `file_${Date.now()}_${Math.random().toString(36).substring(2, 11)}`,
      file,
      formData: { ...defaultFormData },
      isExpanded: false,
      uploadFilename: sanitizeUploadFilename(file.name),
    }),
    [defaultFormData]
  );

  // --- File list and drag state ---
  const [files, setFiles] = useState<UploadFilesDialogFileItem[]>([]);
  const [isDraggingMedia, setIsDraggingMedia] = useState(false);
  const [draggingMetadataFileId, setDraggingMetadataFileId] = useState<string | null>(null);
  const [pendingMetadataFileId, setPendingMetadataFileId] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const metadataInputRef = useRef<HTMLInputElement>(null);
  const prevOpenRef = useRef(false);

  useEffect(() => {
    const justOpened = open && !prevOpenRef.current;
    prevOpenRef.current = open;
    if (justOpened) {
      setFiles(
        initialFiles?.length
          ? initialFiles.map((f) => createFileItem(f))
          : []
      );
      setPendingMetadataFileId(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only seed when open turns true; initialFiles read at that time
  }, [open]);

  // --- File list handlers ---
  const triggerFilePicker = useCallback(() => fileInputRef.current?.click(), []);

  const processNewFiles = useCallback(
    (fileList: FileList | File[]) => {
      const all = Array.from(fileList);
      const valid = all.filter(validateFile);
      if (valid.length < all.length) {
        toast.error('Please drop video files only (mp4, mkv)');
      }
      if (valid.length > 0) {
        setFiles((prev) => [...prev, ...valid.map((f) => createFileItem(f))]);
      }
    },
    [validateFile, createFileItem]
  );

  const handleFileInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const list = e.target.files;
      if (list?.length) processNewFiles(list);
      e.target.value = '';
    },
    [processNewFiles]
  );

  const handleRemoveFile = useCallback((fileId: string) => {
    setFiles((prev) => prev.filter((f) => f.id !== fileId));
  }, []);

  const handleToggleExpand = useCallback((fileId: string) => {
    setFiles((prev) =>
      prev.map((f) => (f.id === fileId ? { ...f, isExpanded: !f.isExpanded } : f))
    );
  }, []);

  const handleFieldChange = useCallback((fileId: string, fieldName: string, value: unknown) => {
    setFiles((prev) =>
      prev.map((f) =>
        f.id === fileId ? { ...f, formData: { ...f.formData, [fieldName]: value } } : f
      )
    );
  }, []);

  const handleUploadFilenameChange = useCallback((fileId: string, value: string) => {
    setFiles((prev) =>
      prev.map((f) => (f.id === fileId ? { ...f, uploadFilename: value } : f))
    );
  }, []);

  const handleToggleMetadataExpand = useCallback((fileId: string) => {
    setFiles((prev) =>
      prev.map((f) =>
        f.id === fileId ? { ...f, isMetadataExpanded: !f.isMetadataExpanded } : f
      )
    );
  }, []);

  // --- Metadata handlers ---
  const handleMetadataSelect = useCallback((fileId: string) => {
    setPendingMetadataFileId(fileId);
    metadataInputRef.current?.click();
  }, []);

  const handleMetadataRemove = useCallback((fileId: string) => {
    setFiles((prev) =>
      prev.map((f) => (f.id === fileId ? { ...f, metadataFile: null } : f))
    );
  }, []);

  const handleMetadataInputChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      const fileId = pendingMetadataFileId;
      e.target.value = '';
      setPendingMetadataFileId(null);
      if (!file || !fileId) return;
      const ok = await validateMetadataFile(file);
      if (!ok) {
        toast.error('Invalid JSON format. Please check your file.');
        return;
      }
      setFiles((prev) =>
        prev.map((f) => (f.id === fileId ? { ...f, metadataFile: file } : f))
      );
    },
    [pendingMetadataFileId, validateMetadataFile]
  );

  const handleMetadataDrop = useCallback(
    async (fileId: string, e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setDraggingMetadataFileId(null);
      const file = e.dataTransfer.files?.[0];
      if (!file) return;
      const ok = await validateMetadataFile(file);
      if (!ok) {
        toast.error('Invalid JSON format. Please check your file.');
        return;
      }
      setFiles((prev) =>
        prev.map((f) => (f.id === fileId ? { ...f, metadataFile: file } : f))
      );
    },
    [validateMetadataFile]
  );

  // --- Confirm and drag (prevent default) ---
  const hasInvalidFilenames = useMemo(
    () => files.some((f) => isUploadFilenameInvalid(f.uploadFilename)),
    [files]
  );

  const handleConfirm = useCallback(() => {
    if (files.length === 0) {
      toast.error('Please select at least one file');
      return;
    }
    if (hasInvalidFilenames) {
      toast.error('Filename is required and must not contain spaces.');
      return;
    }
    const entries: UploadFilesDialogEntry[] = files.map((f) => ({
      id: f.id,
      file: f.file,
      formData: f.formData,
      uploadFilename: sanitizeUploadFilename((f.uploadFilename ?? '').trim()) || undefined,
      metadataFile: f.metadataFile ?? undefined,
    }));
    onConfirm(entries);
    handleClose();
  }, [files, hasInvalidFilenames, onConfirm, handleClose]);

  const preventDragDefault = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleEmptyDrop = useCallback(
    (e: React.DragEvent) => {
      preventDragDefault(e);
      setIsDraggingMedia(false);
      const list = e.dataTransfer.files;
      if (list?.length) processNewFiles(list);
    },
    [processNewFiles, preventDragDefault]
  );

  if (!open) return null;

  return (
    <>
      {/* Hidden file inputs (main + optional metadata) */}
      <input
        type="file"
        ref={fileInputRef}
        className="hidden"
        accept={accept}
        onChange={handleFileInputChange}
        multiple
      />
      {metadataEnabled && (
        <input
          type="file"
          ref={metadataInputRef}
          className="hidden"
          accept=".json,application/json"
          onChange={handleMetadataInputChange}
        />
      )}
      <div className={POPUP_OVERLAY_CLASS}>
        <div className={POPUP_CONTAINER_CLASS}>
          <h3 className="mb-6 text-center text-lg font-semibold text-gray-900 dark:text-white">
            {title}
          </h3>

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
                  onClick={triggerFilePicker}
                  className="flex items-center gap-1 rounded-lg bg-[#76b900] px-2 py-1 text-xs font-medium text-white transition-colors hover:bg-[#5a8f00]"
                >
                  {addMoreWithIcon ? (
                    <>
                      <IconPlus size={14} />
                      Add More
                    </>
                  ) : (
                    '+ Add More'
                  )}
                </button>
              )}
            </div>

            {files.length > 0 ? (
              <div className="max-h-96 space-y-2 overflow-y-auto">
                {files.map((fileItem) => (
                  <div
                    key={fileItem.id}
                    className="overflow-hidden rounded-lg border border-gray-300 dark:border-neutral-700"
                  >
                    <div className="flex items-center justify-between bg-white p-3 dark:bg-neutral-900">
                      <button
                        type="button"
                        className={`flex flex-1 items-center gap-2 overflow-hidden text-left ${hasExpandableContent ? 'cursor-pointer' : ''}`}
                        onClick={() => hasExpandableContent && handleToggleExpand(fileItem.id)}
                        aria-expanded={hasExpandableContent ? fileItem.isExpanded : undefined}
                      >
                        {hasExpandableContent && (
                          <IconChevronDown
                            size={16}
                            className={`flex-shrink-0 text-gray-400 transition-transform duration-200 ${fileItem.isExpanded ? 'rotate-180' : ''}`}
                          />
                        )}
                        <IconVideo size={18} className="flex-shrink-0 text-[#76b900]" />
                        <span className="truncate text-sm text-gray-700 dark:text-gray-300">
                          {fileItem.file.name}
                        </span>
                        <span className="flex-shrink-0 text-xs text-gray-400">
                          ({(fileItem.file.size / 1024 / 1024).toFixed(2)} MB)
                        </span>
                        {((fileItem.uploadFilename ?? '').trim().length === 0 || /\s/.test(fileItem.uploadFilename ?? '')) && (
                          <IconAlertTriangle size={18} className="flex-shrink-0 text-amber-500 dark:text-amber-400" />
                        )}
                      </button>
                      <button
                        onClick={() => handleRemoveFile(fileItem.id)}
                        className="ml-2 flex-shrink-0 text-gray-500 hover:text-red-500"
                        aria-label="Remove file"
                      >
                        <IconX size={18} />
                      </button>
                    </div>

                    {hasExpandableContent && fileItem.isExpanded && (
                      <div className="border-t border-gray-200 bg-gray-50 p-3 dark:border-neutral-700 dark:bg-black">
                        <div className="mb-3 space-y-1">
                          {(() => {
                            const name = fileItem.uploadFilename ?? '';
                            const isEmpty = name.trim().length === 0;
                            const hasSpaces = /\s/.test(name);
                            const isInvalid = isUploadFilenameInvalid(name);
                            return (
                              <>
                                <div className="flex items-center gap-3">
                                  <label htmlFor={`upload-filename-${fileItem.id}`} className="w-24 flex-shrink-0 text-xs font-medium text-gray-600 dark:text-gray-400">
                                    Filename <span className="text-red-500">*</span>
                                  </label>
                                  <input
                                    id={`upload-filename-${fileItem.id}`}
                                    type="text"
                                    value={fileItem.uploadFilename ?? ''}
                                    onChange={(e) => handleUploadFilenameChange(fileItem.id, e.target.value)}
                                    className={`flex-1 ${INPUT_CLASS} ${isInvalid ? 'border-red-500 focus:border-red-500 focus:ring-red-500 dark:border-red-500' : ''}`}
                                    placeholder="e.g. my-video"
                                  />
                                </div>
                                {isEmpty && (
                                  <div className="flex items-center gap-3">
                                    <span className="w-24 flex-shrink-0" />
                                    <p className="text-xs text-red-500 dark:text-red-400">
                                      Filename is required.
                                    </p>
                                  </div>
                                )}
                                {!isEmpty && hasSpaces && (
                                  <div className="flex items-center gap-3">
                                    <span className="w-24 flex-shrink-0" />
                                    <p className="text-xs text-red-500 dark:text-red-400">
                                      Filename must not contain spaces.
                                    </p>
                                  </div>
                                )}
                              </>
                            );
                          })()}
                        </div>
                        {hasConfigFields && (
                          <div className="mb-3 space-y-3">
                            {configTemplate!.fields.map((field) => (
                              <ConfigFieldEditor
                                key={field['field-name']}
                                field={field}
                                value={getFieldValue(fileItem.formData, field)}
                                onChange={(value) => handleFieldChange(fileItem.id, field['field-name'], value)}
                                disabled={field['changeable'] === false}
                              />
                            ))}
                          </div>
                        )}
                  
                        {metadataEnabled && (
                          <div className="overflow-hidden rounded-lg border border-gray-300 dark:border-neutral-700">
                            <button
                              type="button"
                              onClick={() => handleToggleMetadataExpand(fileItem.id)}
                              className="flex w-full items-center gap-2 bg-white px-3 py-2 text-left transition-colors hover:bg-gray-50 dark:bg-neutral-900 dark:hover:bg-neutral-800"
                            >
                              <IconChevronDown
                                size={14}
                                className={`flex-shrink-0 text-gray-400 transition-transform duration-200 ${fileItem.isMetadataExpanded ? 'rotate-180' : ''}`}
                              />
                              <span className="text-xs font-medium text-gray-700 dark:text-gray-300">
                                Metadata (JSON)
                              </span>
                              {fileItem.metadataFile && (
                                <span className="rounded-full bg-[#76b900] px-1.5 py-0.5 text-xs text-white">
                                  1
                                </span>
                              )}
                              <span className="text-xs text-gray-400">(optional)</span>
                            </button>
                            {fileItem.isMetadataExpanded && (
                              <div className="border-t border-gray-200 bg-white p-2 dark:border-neutral-700 dark:bg-neutral-900">
                                {fileItem.metadataFile ? (
                                  <div className="flex items-center justify-between rounded-lg border border-[#76b900] bg-[#76b900]/10 p-2">
                                    <div className="flex items-center gap-2 overflow-hidden">
                                      <IconFileCode
                                        size={16}
                                        className="flex-shrink-0 text-[#76b900]"
                                      />
                                      <span className="truncate text-xs text-gray-700 dark:text-gray-300">
                                        {fileItem.metadataFile.name}
                                      </span>
                                    </div>
                                    <button
                                      onClick={() => handleMetadataRemove(fileItem.id)}
                                      className="ml-2 flex-shrink-0 text-gray-500 hover:text-red-500"
                                    >
                                      <IconX size={16} />
                                    </button>
                                  </div>
                                ) : (
                                  <button
                                    type="button"
                                    onClick={() => handleMetadataSelect(fileItem.id)}
                                    onDragOver={preventDragDefault}
                                    onDragEnter={(e) => {
                                      preventDragDefault(e);
                                      setDraggingMetadataFileId(fileItem.id);
                                    }}
                                    onDragLeave={(e) => {
                                      preventDragDefault(e);
                                      setDraggingMetadataFileId(null);
                                    }}
                                    onDrop={(e) => handleMetadataDrop(fileItem.id, e)}
                                    className={`w-full cursor-pointer rounded-lg border-2 border-dashed p-3 text-center transition-colors ${
                                      draggingMetadataFileId === fileItem.id
                                        ? 'border-[#76b900] bg-[#76b900]/10'
                                        : 'border-gray-300 hover:border-[#76b900] hover:bg-gray-50 dark:border-neutral-700 dark:hover:border-[#76b900] dark:hover:bg-neutral-800'
                                    }`}
                                    aria-label="Select metadata JSON file"
                                  >
                                    <IconFileCode size={24} className="mx-auto text-gray-400" />
                                    <span className="mt-1 block text-xs text-gray-500 dark:text-gray-400">
                                      {draggingMetadataFileId === fileItem.id
                                        ? 'Drop JSON here'
                                        : 'Click or drag JSON metadata'}
                                    </span>
                                  </button>
                                )}
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <EmptyDropZone
                onPickFiles={triggerFilePicker}
                onDragOver={preventDragDefault}
                onDragEnter={(e) => { preventDragDefault(e); setIsDraggingMedia(true); }}
                onDragLeave={(e) => { preventDragDefault(e); setIsDraggingMedia(false); }}
                onDrop={handleEmptyDrop}
                isDragging={isDraggingMedia}
                emptyStateHint={emptyStateHint}
              />
            )}
          </div>

          {/* Cancel / Upload */}
          <div className="flex gap-3">
            <button type="button" onClick={handleClose}
              className="flex-1 rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 dark:border-neutral-700 dark:bg-neutral-900 dark:text-gray-200 dark:hover:bg-neutral-800"
            >
              Cancel
            </button>
            <button
              data-testid="upload-confirm-button"
              type="button"
              onClick={handleConfirm}
              disabled={files.length === 0 || hasInvalidFilenames}
              className={`flex-1 rounded-lg px-4 py-2 text-sm font-medium text-white transition-colors ${
                files.length > 0 && !hasInvalidFilenames ? 'bg-[#76b900] hover:bg-[#5a8f00]' : 'bg-gray-400 cursor-not-allowed'
              }`}
            >
              Upload {files.length > 0 ? `(${files.length})` : ''}
            </button>
          </div>
        </div>
      </div>
    </>
  );
});
