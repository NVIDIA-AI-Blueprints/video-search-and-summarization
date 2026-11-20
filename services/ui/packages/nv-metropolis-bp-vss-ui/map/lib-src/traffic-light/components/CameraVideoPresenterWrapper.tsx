// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";

import {
  CameraVideoPresenter,
  DefaultHTMLVideoTagOptions,
  Widget,
} from "common/map";
import { Warning as WarningIcon } from "common/map/mui-icons";

import Styles from "./CameraVideoPresenterWrapper.module.css";

const CameraVideoPresenterWrapper = (props) => {
  let propsTobePassed = Object.assign(
    {
      videoHtmlTagOptions: DefaultHTMLVideoTagOptions.htmlTagOptions,
    },
    props
  );
  return (
    <React.Fragment>
      {props.mode === "recorded" && (
        <Widget
          right=".5vw"
          top="2.25vw"
          height="1.2vw"
          width={DefaultHTMLVideoTagOptions.htmlTagOptions.width}
          zIndex="99"
          showShadow={false}
        >
          <span className={Styles.videoNoticeBanner}>
            <WarningIcon />
            Seek disrupts sync with trajectory animation
          </span>
        </Widget>
      )}
      <Widget
        right=".5vw"
        top="4.5vw"
        height={DefaultHTMLVideoTagOptions.htmlTagOptions.height}
        width={DefaultHTMLVideoTagOptions.htmlTagOptions.width}
        zIndex="99"
        showShadow={true}
      >
        <CameraVideoPresenter {...propsTobePassed} />
      </Widget>
    </React.Fragment>
  );
};

export default CameraVideoPresenterWrapper;
