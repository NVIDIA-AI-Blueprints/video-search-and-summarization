// SPDX-License-Identifier: MIT
import React from 'react';
import { Toolbar } from './Toolbar';

type VideoManagementSidebarControlsProps = Omit<
  React.ComponentProps<typeof Toolbar>,
  'layout'
>;

export const VideoManagementSidebarControls: React.FC<VideoManagementSidebarControlsProps> = (props) => (
  <Toolbar {...props} layout="sidebar" />
);
