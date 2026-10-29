// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";

import { CameraVideoPresenter, Widget } from "common/warehouse/mdat";

import DefaultHTMLVideoTagOptions from "./../js/DefaultHTMLVideoTagOptions";

const MyCameraVideoPresenter = (props) => {
  let propsTobePassed = Object.assign(
    {
      videoHtmlTagOptions: DefaultHTMLVideoTagOptions.htmlTagOptions,
    },
    props
  );
  return (
    <Widget
      right=".5vw"
      top="2.5vw"
      height={DefaultHTMLVideoTagOptions.htmlTagOptions.height}
      width={DefaultHTMLVideoTagOptions.htmlTagOptions.width}
      zIndex="99"
      showShadow={true}
    >
      <CameraVideoPresenter {...propsTobePassed} />
    </Widget>
  );
};

export default MyCameraVideoPresenter;
