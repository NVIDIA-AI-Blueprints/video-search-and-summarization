// SPDX-License-Identifier: MIT
/**
 * Tracks whether any upload flow currently owns the composer.
 *
 * Uploads can start from more than one place — the composer's attach button,
 * the chat header, a drop onto the transcript — and each opens and closes
 * independently. A single boolean would let whichever finished first re-enable
 * sending while another batch was still running, so sources are tracked by name
 * and the flow is active while any of them is.
 */
import { useCallback, useRef, useState } from 'react';

/** Identifies an upload source, e.g. 'chat-input' or 'chat-header'. */
export type UploadFlowSourceId = string;

export interface UploadFlowCoordinator {
  /** True while at least one source has an upload flow open. */
  uploadFlowActive: boolean;
  /**
   * Mirrors `uploadFlowActive` for reads inside callbacks and event handlers,
   * which would otherwise close over a stale value.
   */
  uploadFlowActiveRef: React.MutableRefObject<boolean>;
  /** Opens or closes the flow for one source. */
  reportUploadFlowActive: (sourceId: UploadFlowSourceId, active: boolean) => void;
}

export function useUploadFlowCoordinator(): UploadFlowCoordinator {
  const activeSourcesRef = useRef<Set<UploadFlowSourceId>>(new Set());
  const uploadFlowActiveRef = useRef(false);
  const [uploadFlowActive, setUploadFlowActive] = useState(false);

  const reportUploadFlowActive = useCallback(
    (sourceId: UploadFlowSourceId, active: boolean) => {
      const sources = activeSourcesRef.current;

      if (active) sources.add(sourceId);
      else sources.delete(sourceId);

      const nextActive = sources.size > 0;
      uploadFlowActiveRef.current = nextActive;
      setUploadFlowActive(nextActive);
    },
    [],
  );

  return { uploadFlowActive, uploadFlowActiveRef, reportUploadFlowActive };
}
