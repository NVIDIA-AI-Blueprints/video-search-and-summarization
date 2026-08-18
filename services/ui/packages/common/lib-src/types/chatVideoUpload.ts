// SPDX-License-Identifier: MIT
import type { FileUploadResult } from '../utils/videoUpload';

/** Emitted when a chat upload batch finishes with at least one successful file. */
export type ChatVideoUploadCompletePayload = {
  results: { filename: string; result: FileUploadResult }[];
};
