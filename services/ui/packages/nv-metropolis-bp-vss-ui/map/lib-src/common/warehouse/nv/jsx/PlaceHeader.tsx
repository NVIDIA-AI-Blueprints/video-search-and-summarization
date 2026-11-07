// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";

import { Widget } from "common/warehouse/mdat";

/**
 * Header Bar transparent over Map within the context of the current place
 */
const PlaceHeader = (props) => {
  return (
    <Widget top="0" left="0" height="2.25rem" width="100%" zIndex="20" showShadow={true}>
      {props.children}
    </Widget>
  );
};

export default PlaceHeader;
