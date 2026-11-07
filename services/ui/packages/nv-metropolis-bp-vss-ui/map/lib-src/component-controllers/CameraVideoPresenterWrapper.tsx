// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";

import { CameraVideoPresenter, Widget, Window } from "common/warehouse/mdat";
import { DefaultHTMLVideoTagOptions } from "common/warehouse";
import { Warning as WarningIcon } from "common/warehouse/mui-icons";

import Styles from "./CameraVideoPresenterWrapper.module.css";

const CameraVideoPresenterWrapper = (props) => {
  let propsTobePassed = Object.assign(
    {
      videoHtmlTagOptions: {
        ...DefaultHTMLVideoTagOptions.htmlTagOptions,
        width: '100%',
        height: '100%',
      },
    },
    props
  );
  
  // Set title based on mode
  const title = props.mode === "recorded" 
    ? "Camera View (Recorded)" 
    : "Camera View (Live)";
  
  // Set subtitle with Camera ID
  const subtitle = props.cameraSourceInfo?.id ? `Camera ID: ${props.cameraSourceInfo.id}` : "Camera ID: Test";
  
  return (
    <React.Fragment>
      {props.mode === "recorded" && (
        <Widget
          right="0.5%"
          top="6%"
          height="1.5rem"
          width="29%"
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
        right="0.5%"
        top="calc(6% + 1.7rem)"
        height="33%"
        width="29%"
        zIndex="99"
        showShadow={true}
      >
        <Window titleText={title} subTitleText={subtitle} overflowHidden>
          <div className={Styles.videoBody}>
            <CameraVideoPresenter {...propsTobePassed} />
          </div>
        </Window>
      </Widget>
    </React.Fragment>
  );
};

export default CameraVideoPresenterWrapper;
