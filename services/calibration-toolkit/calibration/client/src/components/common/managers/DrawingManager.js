/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: LicenseRef-NvidiaProprietary
 *
 * NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
 * property and proprietary rights in and to this material, related
 * documentation and any modifications thereto. Any use, reproduction,
 * disclosure or distribution of this material and related documentation
 * without an express license agreement from NVIDIA CORPORATION or
 * its affiliates is strictly prohibited.
 */

import React, { PureComponent } from "react";
import PropTypes from "prop-types";
import { Header, List, Icon, Button, Popup } from "semantic-ui-react";
import Hotkeys from "react-hot-keys";

import {
  shortcuts,
  colors,
  calibId,
  calValId,
  roiId,
  cartCalibId,
  cartRoiId,
  cartTripDirId,
  cartTripwireId,
  tripDirId,
  tripwireId,
  floorPlanCalibId,
  floorPlanValId,
  floorPlanRoiId,
  floorPlanTripDirId,
  floorPlanTripwireId,
  camPlacementId,
  camPlacementMapId
} from "../utils";

/**
 * Headerbar drawing manager that allows user to click a specific category to
 * enter the drawing mode for that category. Also controls showing/hiding of
 * drawings and deletion of all drawings in a specific category.
 */
export default class DrawingManager extends PureComponent {
  render() {
    const {
      title,
      onSelect,
      labels,
      selected,
      toggles,
      onToggle,
      filter,
      style,
      clearMapData,
      clearImageData,
      toggleMapMarkers,
      toggleImageMarkers,
      showMapMarkers
    } = this.props;
    console.log("dm labels", labels)
    const getSelectHandler = ({ type, id }) =>
      type === "polyline" || type === "polygon" ? () => onSelect(id) : null;
    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          padding: "1em 0.5em",
          borderRight: "1px solid #ccc",
          height: "100%",
          ...style
        }}
      >
        <Header
          size="large"
          style={{ flex: "0 0 auto" }}
          data-testid="ManagerTitle"
        >
          {title}
        </Header>
        <List
          horizontal
          divided
          selection
          // relaxed
          style={{ flex: 1, overflowY: "auto", overflowX: "auto" }}
        >
          {labels.map((label, i) =>
            ListItem({
              shortcut: shortcuts[i],
              label,
              color: colors[i],
              onSelect: getSelectHandler(label),
              updateSelected: onSelect,
              refreshSelect: getSelectHandler,
              selected: selected === label.id,
              disabled: filter ? !filter(label) : false,
              onToggle: onToggle,
              isToggled: toggles && toggles[label.id],
              clearMapData,
              clearImageData,
              toggleMapMarkers,
              toggleImageMarkers,
              showMapMarkers
            })
          )}
          <Hotkeys keyName="esc" onKeyDown={() => onSelect(null)} />
        </List>
      </div>
    );
  }
}

const iconMapping = {
  polyline: "pencil alternate",
  polygon: "pencil alternate",

};

const typeHidable = {
  polyline: true,
  polygon: true,
  text: false,
  select: false,
  "select-one": false
};
function ListItem({
  shortcut,
  label,
  onSelect,
  updateSelected,
  onToggle,
  color,
  selected = false,
  disabled = false,
  isToggled = false,
  clearMapData,
  clearImageData,
  toggleMapMarkers,
  toggleImageMarkers,
  showMapMarkers
}) {
  var icons = [];
  var clearIcons = [];

//GEO case
  if ( label.id === calibId || label.id === floorPlanValId || label.id === camPlacementId || label.id === camPlacementMapId
      // label.id !== tripwireId ||
      // label.id !== tripDirId
    ) {
    clearIcons.push([
      <Popup
        trigger={
          <Button
            size="small"
            key="marker-vis"
            floated="right"
            icon={showMapMarkers ? "map marker" : "map marker alternate"}
            // label="Markers"
            color={color}
            onClick={e => {
              if (toggleImageMarkers) {
                toggleImageMarkers();
              }
              if (toggleMapMarkers) {
                toggleMapMarkers();
              }
              e.stopPropagation();
            }}
            data-testid={`MarkerVis_${label.id}`}
          />
        }
        size="tiny"
        position="top center"
        content="Show Map Markers"
      />
    ]);
  }
  if (
      label.id !== calValId &&
      label.id !== cartCalibId &&
      label.id !== cartRoiId &&
      label.id !== cartTripDirId &&
      label.id !== cartTripwireId &&
      label.id !== floorPlanCalibId
      // label.id !== floorPlanRoiId &&
      // label.id !== floorPlanValId &&
      // label.id !== floorPlanTripwireId &&
      // label.id !== floorPlanTripDirId
      ) {
        clearIcons.push([
          <Popup
            trigger={
              <Button
                size="small"
                key="clear-map"
                floated="right"
                icon="delete"
                // label="Clear Map"
                color={color}
                onClick={async e => {
                  e.stopPropagation();
                  await clearMapData(label.id);
                  updateSelected(null);
                  updateSelected(label.id);
                }}
                data-testid={`ClearMap_${label.id}`}
              />
            }
            size="tiny"
            position="top center"
            content={`Clear ${label.name}`}
          />
        ]);
  }
  if (
      label.id === calibId ||
      label.id === calValId ||
      label.id === cartCalibId ||
      label.id === cartRoiId ||
      label.id === cartTripDirId ||
      label.id === cartTripwireId ||
      label.id === floorPlanCalibId ||
      label.id === camPlacementMapId
      // label.id === floorPlanRoiId ||
      // label.id === floorPlanValId ||
      // label.id === floorPlanTripwireId ||
      // label.id === floorPlanTripDirId

      ) {
        clearIcons.push([
          <Popup
            trigger={
              <Button
                size="small"
                key="clear-sensor"
                color={color}
                floated="right"
                icon="delete"
                // label="Clear Image"
                onClick={async e => {
                  e.stopPropagation();
                  await clearImageData(label.id);
                  updateSelected(null);
                  updateSelected(label.id);
                }}
                data-testid={`ClearImage_${label.id}`}
              />
            }
            size="tiny"
            position="top center"
            content={`Clear ${label.name}`}
            />
          ]);
  }

  if (onToggle && typeHidable[label.type]) {
    icons.push(
      <Popup
        trigger={
          <Button
            size="small"
            key="visibility-icon"
            color={color}
            floated="left"
            icon={isToggled ? "eye" : "eye slash"}
            onClick={e => {
              onToggle(label);
              e.stopPropagation();
            }}
          />
        }
        key={label.id}
        size="tiny"
        position="top center"
        content={"Hide"}
      />
    );
  }

  const iconType = iconMapping[label.type];
  const figureIcon = label.draw ? iconType ? (
    <Icon
      key="type-icon"
      name={iconType}
      style={{  opacity: 0.5, display: "inline-block", marginLeft: 5 }}
    />
  ) : null : null ;
  if (!label.draw){
    clearIcons = []
  }
  return (
    <List.Item
      onClick={onSelect}
      disabled={disabled}
      active={selected}
      key={label.id}
      style={{   fontSize: "26px", verticalAlign: "middle" }}
      data-testid={`ManagerItem_${label.id}`}
    >
      <Hotkeys
        keyName={shortcut}
        onKeyDown={() => !disabled && onSelect && onSelect()}
        key={label.id}
      >
        <List.Content key={label.id}>
          {label.name}
          {figureIcon}
          {clearIcons}
          {icons}
        </List.Content>
      </Hotkeys>
    </List.Item>
  );
}

DrawingManager.propTypes = {
  /** Title to display at top of the drawing manager */
  title: PropTypes.string,
  /** Each object contains information about the label to display */
  labels: PropTypes.arrayOf(PropTypes.object).isRequired,
  /** ID of the selected item in the drawing manager */
  selected: PropTypes.string,
  /** Handle the selection of an item in the drawing manager */
  onSelect: PropTypes.func,
  /** Toggles that visibility of categories in the drawing manager */
  toggles: PropTypes.object,
  /** Handle the action of updating the toggle visibility*/
  onToggle: PropTypes.func,
  /** Handle clearning the map data for a category */
  clearMapData: PropTypes.func,
  /** Handle clearning the image data for a category */
  clearImageData: PropTypes.func,
  /** Toggle the map markers (vertex number labels)*/
  toggleMapMarkers: PropTypes.func,
  /** Toggle the image markers (vertex number labels) */
  toggleImageMarkers: PropTypes.func,
  /** Indicator whether or not the markers should be shown */
  showMapMarkers: PropTypes.bool
};
