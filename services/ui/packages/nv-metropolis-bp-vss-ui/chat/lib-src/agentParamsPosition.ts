// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT AND Apache-2.0
import type React from 'react';

const GAP = 8;
const MIN_PANEL_HEIGHT = 160;

interface AgentParamsPositionInput {
  anchorRect: Pick<DOMRect, 'top' | 'bottom' | 'right'> | null;
  viewportHeight: number;
  viewportWidth: number;
}

export function getAgentParamsPosition({
  anchorRect,
  viewportHeight,
  viewportWidth,
}: AgentParamsPositionInput): React.CSSProperties {
  if (!anchorRect) {
    return { top: 80, right: 16, maxHeight: '60vh' };
  }

  const right = Math.max(GAP, viewportWidth - anchorRect.right);
  const spaceAbove = anchorRect.top - GAP * 2;
  const spaceBelow = viewportHeight - anchorRect.bottom - GAP * 2;
  const openDown = spaceAbove < MIN_PANEL_HEIGHT && spaceBelow > spaceAbove;

  return openDown
    ? { top: anchorRect.bottom + GAP, right, maxHeight: spaceBelow }
    : {
        bottom: viewportHeight - anchorRect.top + GAP,
        right,
        maxHeight: spaceAbove,
      };
}
