// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import React from 'react';
import { Toolbar } from './Toolbar';

type VideoManagementSidebarControlsProps = Omit<
  React.ComponentProps<typeof Toolbar>,
  'layout'
>;

export const VideoManagementSidebarControls: React.FC<VideoManagementSidebarControlsProps> = (props) => (
  <Toolbar {...props} layout="sidebar" />
);
