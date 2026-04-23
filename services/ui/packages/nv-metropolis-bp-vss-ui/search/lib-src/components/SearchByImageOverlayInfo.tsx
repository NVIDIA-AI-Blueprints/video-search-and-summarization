// SPDX-License-Identifier: MIT
import React from 'react';
import { Button as KaizenButton } from '@nvidia/foundations-react-core';
import { VideoModalTooltip } from '@aiqtoolkit-ui/common';
import { SearchByImageFrameData } from '../types';

interface SearchByImageOverlayInfoProps {
  frameData: SearchByImageFrameData;
  selectedObjectId: string | null;
  onConfirm: (objectId: string) => void;
  onCancel: () => void;
  isDark?: boolean;
}

export const SearchByImageOverlayInfo: React.FC<SearchByImageOverlayInfoProps> = ({
  frameData,
  selectedObjectId,
  onConfirm,
  onCancel,
  isDark = false,
}) => {
  const selectedObj = frameData.objects.find((o) => o.id === selectedObjectId);
  const selectedTypeLabel = selectedObj?.type?.trim() || 'Unknown';
  const hasBoxes = frameData.objects.length > 0;
  const containerClassName = isDark
    ? 'flex items-center justify-between gap-3 border-y border-gray-700 bg-slate-900 px-4 py-2 text-sm text-gray-100'
    : 'flex items-center justify-between gap-3 border-y border-gray-200 bg-white px-4 py-2 text-sm text-gray-900';
  const hintClassName = isDark ? 'text-gray-300' : 'text-gray-600';

  return (
    <div className={containerClassName}>
      {selectedObjectId ? (
        <div className="min-w-0 truncate">
          <span className="font-medium">Selected Object Id:</span>
          {' '}
          <span className="font-mono font-semibold">{selectedObjectId}</span>
          {' '}
          <span className={hintClassName}>({selectedTypeLabel})</span>
        </div>
      ) : (
        <span className={`flex items-center ${hintClassName}`}>
          {hasBoxes
            ? 'Select one to search for similar object embeddings across views/cameras'
            : 'No bounding boxes detected in this frame'}
        </span>
      )}

      <div className="flex shrink-0 items-center gap-2">
        {selectedObjectId && (
          <VideoModalTooltip content="Search related embeddings for the selected object">
            <div>
              <KaizenButton
                onClick={() => onConfirm(selectedObjectId)}
                kind="primary"
                size="small"
              >
                Search
              </KaizenButton>
            </div>
          </VideoModalTooltip>
        )}
        <VideoModalTooltip
          content="Press to exit Search By Image Mode"
          placement="topEnd"
        >
          <div>
            <KaizenButton
              onClick={onCancel}
              kind="secondary"
              size="small"
            >
              Cancel
            </KaizenButton>
          </div>
        </VideoModalTooltip>
      </div>
    </div>
  );
};
