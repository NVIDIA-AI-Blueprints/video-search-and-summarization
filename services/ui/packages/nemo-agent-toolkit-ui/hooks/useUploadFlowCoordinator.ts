import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Tracks whether any ChatFileUpload instance has an open upload dialog
 * (file picker, progress, or success). Used to block chat interaction until all close.
 */
export function useUploadFlowCoordinator() {
  const sourcesRef = useRef(new Set<string>());
  const [uploadFlowActive, setUploadFlowActive] = useState(false);
  const uploadFlowActiveRef = useRef(false);

  useEffect(() => {
    uploadFlowActiveRef.current = uploadFlowActive;
  }, [uploadFlowActive]);

  const reportUploadFlowActive = useCallback((sourceId: string, active: boolean) => {
    const sources = sourcesRef.current;
    if (active) {
      sources.add(sourceId);
    } else {
      sources.delete(sourceId);
    }
    const next = sources.size > 0;
    uploadFlowActiveRef.current = next;
    setUploadFlowActive(next);
  }, []);

  return { uploadFlowActive, uploadFlowActiveRef, reportUploadFlowActive };
}
