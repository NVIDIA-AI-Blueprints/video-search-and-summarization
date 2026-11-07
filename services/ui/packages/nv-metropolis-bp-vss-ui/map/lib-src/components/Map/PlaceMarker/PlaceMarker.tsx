// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { Marker } from "@react-google-maps/api";
import { resolveAssetUrl } from "../../../utils/resolveAssetUrl";
//import PropTypes from 'prop-types';

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

//PlaceMarker.propTypes = {
//  /**
//   * JSON object for intersection's info
//   */
//  data: PropTypes.object.isRequired,
//  /**
//   * Type of the marker icon
//   * 0 = normal green icon
//   * 1 = red icon
//   * 2 = icon among currently selected corridor
//   */
//  type: PropTypes.oneOf([0, 1, 2]),
//  /**
//   * String name of the corridor to be highlighted
//   */
//  showHighlightedCorridor: PropTypes.string,
//  /**
//   * Current Google Maps zoom level
//   */
//  zoomLevel: PropTypes.number.isRequired,
//  /**
//   * Callback upon left click on the marker
//   *
//   * @param { String } string name of the clicked intersection
//   */
//  onLeftClick: PropTypes.func,
//  /**
//   * Callback upon right click on the marker
//   *
//   * @param { Object } event default click event from Google Maps
//   * @param { Array } array List of corridors that the intersection belongs to
//   */
//  onRightClick: PropTypes.func,
//}

export default PlaceMarker;
