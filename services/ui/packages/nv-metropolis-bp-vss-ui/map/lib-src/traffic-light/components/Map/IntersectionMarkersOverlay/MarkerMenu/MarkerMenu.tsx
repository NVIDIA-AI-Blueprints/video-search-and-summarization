// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { OverlayView } from "@react-google-maps/api";
import { Close as CloseIcon } from "common/map/mui-icons";

import { BeautifyNameString } from "common/map";

import CssClasses from "./MarkerMenu.module.css";

/**
 * On Right Click on IntersectionMarker Menu
 */
const MarkerMenu = (props) => {
  return (
    <OverlayView
      key="intersection-marker-right-click-menu"
      mapPaneName={OverlayView.FLOAT_PANE}
      position={props.position}
    >
      <div className={CssClasses.mainBox}>
        <div className={CssClasses.header}>
          <div className={CssClasses.heading}>Select a corridor</div>
          <div
            title="Click to close the Corridor selection menu"
            onClick={props.onClose}
            className={CssClasses.closeButtonDiv}
          >
            <CloseIcon className={CssClasses.closeButtonIcon} />
          </div>
        </div>
        {props.itemsStringList.map((str, index) => {
          return (
            <div
              key={"intersection-marker-menu" + index}
              className={CssClasses.item}
              onClick={() => props.onClickItem(str)}
              onMouseOver={() => props.onMouseOverItem(str)}
              onMouseOut={() => props.onMouseOutOfItem()}
            >
              {BeautifyNameString(str)}
            </div>
          );
        })}
      </div>
    </OverlayView>
  );
};

export default MarkerMenu;
