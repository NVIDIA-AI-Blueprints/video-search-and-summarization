// SPDX-License-Identifier: MIT
import React from 'react';

interface VideoManagementSidebarControlsProps {
  onFilesSelected: (files: File[]) => void;
  enableVideoUpload?: boolean;
}

export const VideoManagementSidebarControls: React.FC<VideoManagementSidebarControlsProps> = ({ enableVideoUpload = true }) => {
  // Add video management sidebar controls here if needed in future
  // enableVideoUpload prop is available for future implementation
  return null;
};
