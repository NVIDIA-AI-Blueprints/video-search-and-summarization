import type { FileUploadResult } from '@aiqtoolkit-ui/common';

/** Emitted when a chat upload batch finishes with at least one successful file. */
export type ChatVideoUploadCompletePayload = {
  results: { filename: string; result: FileUploadResult }[];
};
