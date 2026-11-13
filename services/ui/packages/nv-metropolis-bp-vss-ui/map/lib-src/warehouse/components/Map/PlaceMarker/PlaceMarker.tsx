// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { Marker } from "@react-google-maps/api";
import { resolveAssetUrl } from "../../../utils/resolveAssetUrl";

const resolveMarkerIcon = (icon) => {
  if (!icon) return undefined;
  if (typeof icon === "string") {
    return resolveAssetUrl(icon) || undefined;
  }
  if (typeof icon === "object") {
    const url = resolveAssetUrl(icon.url ?? icon);
    if (!url) return undefined;
    return { ...icon, url };
  }
  return undefined;
};

/**
 * Wraps a Google Maps Marker
 * Note: (Stateless Component)
 */
const PlaceMarker = (props) => {
  const { position, icon, onLeftClick, onRightClick, title, onLoad, enableSpiderfier } = props;
  const resolvedIcon = resolveMarkerIcon(icon);
  return (
    <Marker
      position={position}
      icon={resolvedIcon}
      onClick={() => {
        if(!enableSpiderfier && onLeftClick){
          onLeftClick();
        }
      }}
      onRightClick={(e) => {
        // Prevent browser context menu via domEvent
        if (e && e.domEvent) {
          e.domEvent.preventDefault();
          e.domEvent.stopPropagation();
        }
        if(!enableSpiderfier && onRightClick){
          onRightClick(); 
        }
      }}
      title={title}
      onLoad={(marker) => {
        if(onLoad){
          onLoad(marker, {
            leftClick: onLeftClick,
            rightClick: onRightClick,
          });
        }
      }}
    />
  );
};

export default PlaceMarker;
