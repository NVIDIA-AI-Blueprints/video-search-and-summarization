// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { Marker } from "@react-google-maps/api";

import IntersectionAlertedIcon from "../../../../assets/map-marker-place-alerted.png";
import IntersectionSelectedIcon from "../../../../assets/map-marker-place-selected.png";
import IntersectionActiveIcon from "../../../../assets/map-marker-place-active.png";
import IntersectionInactiveIcon from "../../../../assets/map-marker-place-inactive.png";
import { BeautifyNameString } from "common/map";
import { resolveAssetUrl } from "../../../../../utils/resolveAssetUrl";

/**
 * Wraps a Google Maps Marker
 */
export default class IntersectionMarker extends React.PureComponent {
  render() {
    let iconSize;
    switch (this.props.zoomLevel) {
      case 10:
      case 11:
      case 12:
        iconSize = {
          width: 20,
          height: 20,
        };
        break;
      case 13:
      case 14:
        iconSize = {
          width: 40,
          height: 40,
        };
        break;
      case 15:
      case 16:
        iconSize = {
          width: 40,
          height: 40,
        };
        break;
      case 17:
      case 18:
        iconSize = {
          width: 80,
          height: 80,
        };
        break;
      default:
        break;
    }

    let iconUrl;
    switch (this.props.type) {
      case 0:
        iconUrl = IntersectionActiveIcon;
        break;
      case 1:
        iconUrl = IntersectionAlertedIcon;
        break;
      case 2:
        iconUrl = IntersectionSelectedIcon;
        break;
      case 3:
        iconUrl = IntersectionInactiveIcon;
        break;
      default:
    }

    let title =
      "Left Click to enter Intersection View for " + this.props.data.name + ".";
    if (this.props.data.corridorsSet.size > 0) {
      title += "\nRight Click to select associated corridor.";
    }

    if (this.props.type === 1) {
      title += "\nRECENT SEVERE ALERT!!! (Left-click to dismiss)";
    }

    let corridorNames = [];
    for (const name of this.props.data.corridorsSet.values()) {
      corridorNames.push(name);
    }

    const handleLeftClick = () => this.props.onLeftClick(this.props.data.name);

    const handleRightClick = (e) => {
      this.props.onRightClick(e, corridorNames);
    };

    const iconSrc = resolveAssetUrl(iconUrl);
    const icon = iconSrc
      ? {
          url: iconSrc,
          scaledSize: iconSize,
        }
      : undefined;

    return (
      <Marker
        position={{
          lat: this.props.data.geoLocation.lat,
          lng: this.props.data.geoLocation.lng,
        }}
        icon={icon}
        onClick={() => {
          if (!this.props.enableSpiderfier && handleLeftClick) {
            handleLeftClick();
          }
        }}
        onRightClick={(e) => {
          if (!this.props.enableSpiderfier && handleRightClick) {
            handleRightClick(e);
          }
        }}
        title={title}
        label={
          this.props.showHighlightedCorridor === null
            ? null
            : {
                color: "#9933ff",
                fontFamily: "arial black",
                fontWeight: "bold",
                text: BeautifyNameString(this.props.showHighlightedCorridor),
              }
        }
        onLoad={(marker) => {
          if (this.props.onLoad) {
            this.props.onLoad(marker, {
              leftClick: handleLeftClick,
              rightClick: handleRightClick,
            });
          }
        }}
      />
    );
  }
}
