// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";

// Context to expose TopView's stageScale to children for tooltip positioning
export const TopViewContext = React.createContext({
  stageScale: 1,
});

