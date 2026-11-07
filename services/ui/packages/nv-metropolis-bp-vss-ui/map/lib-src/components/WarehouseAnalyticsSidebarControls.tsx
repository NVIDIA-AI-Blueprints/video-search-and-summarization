// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React, { useState } from 'react';
import {
  CONFIG_PANEL_ALLOWED_CONFIG_TYPES_DEFAULT,
  ConfigFileActionsPanel,
  UploadImagesPanel,
} from 'common/warehouse';

interface WarehouseAnalyticsSidebarControlsProps {
  configTypes?: string[];
  theme?: 'light' | 'dark';
}

export const WarehouseAnalyticsSidebarControls: React.FC<WarehouseAnalyticsSidebarControlsProps> = ({
  configTypes = CONFIG_PANEL_ALLOWED_CONFIG_TYPES_DEFAULT,
  theme: _theme,
}) => {
  const [showConfig, setShowConfig] = useState(false);
  const [showUpload, setShowUpload] = useState(false);

  return (
    <div data-testid="map-controls" className="flex flex-col gap-3 px-3 pt-2 pb-3">
      <ConfigFileActionsPanel
        configTypes={configTypes}
        expanded={showConfig}
        onToggle={() => setShowConfig((open) => !open)}
      />
      <UploadImagesPanel
        expanded={showUpload}
        onToggle={() => setShowUpload((open) => !open)}
      />
    </div>
  );
};
