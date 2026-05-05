/**
 * @aiqtoolkit-ui/common
 *
 * Shared components and utilities for the AIQ Toolkit UI system.
 * Use this package across all apps (nemo-agent-toolkit-ui, nv-metropolis-bp-vss-ui, etc.)
 */

// Components
export { VideoModal } from './components/VideoModal';
export type { VideoModalProps } from './components/VideoModal';
export { VideoModalTooltip } from './components/VideoModalTooltip';
export type { VideoModalTooltipProps } from './components/VideoModalTooltip';

export { UploadFilesDialog } from './components/UploadFilesDialog';
export type {
  UploadFilesDialogFileItem,
  UploadFilesDialogEntry,
  UploadFilesDialogProps,
  UploadFilesDialogHandle,
  UploadFilesDialogMetadataConfig,
  UploadFilesDialogOptions,
} from './components/UploadFilesDialog';
export type {
  UploadFileConfigTemplate,
  UploadFileFieldConfig,
} from './types/uploadFileConfig';

// Hooks
export { useVideoModal } from './hooks/useVideoModal';
export type {
  VideoModalState,
  VideoModalData,
  AlertLike,
  UseVideoModalOptions,
} from './hooks/useVideoModal';

// Utils
export { copyToClipboard } from './utils/clipboard';
export { formatTimestamp } from './utils/formatters';
export { getUploadUrl, uploadFile, uploadFileChunkedToVst, notifyGenericUploadComplete } from './utils/videoUpload';
export type { FileUploadResult } from './utils/videoUpload';
export { uploadFileChunked, CHUNK_SIZE_BYTES, MAX_CHUNK_RETRIES } from './utils/chunkedUpload';
export type { ChunkedUploadOptions, ChunkedUploadResponse } from './utils/chunkedUpload';
export { checkVideoUrl, fetchVideoUrlFromVst, replaceVideoUrlBase } from './utils/videoModal';
export type { FetchVideoUrlParams } from './utils/videoModal';
