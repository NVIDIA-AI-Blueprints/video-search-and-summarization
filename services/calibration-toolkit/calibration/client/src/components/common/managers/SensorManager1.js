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
export default class SensorManager extends PureComponent {
  render() {
    const {
      title,
      onSelect,
      sensors,
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
    console.log("dm sensors", sensors)
    const getSelectHandler = ({ type, id }) => 
      type === "polygon" || type === "point" ? () => onSelect(id) : null;
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
          {sensors.map((sensor,i) =>
            {
              console.log("sm", sensor)
              ListItem({
                shortcut: shortcuts,
                sensor,
                color: colors['red'],
                onSelect: getSelectHandler("polygon", sensor.id),
                updateSelected: onSelect,
                refreshSelect: getSelectHandler,
                selected: selected === sensor.id,
                disabled: filter ? !filter(sensor) : false,
                onToggle: onToggle,
                isToggled: toggles && toggles[sensor.id],
                clearMapData,
                clearImageData,
                toggleMapMarkers,
                toggleImageMarkers,
                showMapMarkers
              })
          }
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
  point: "pencil alternate",

};

const typeHidable = {
  polyline: true,
  polygon: true,
  point: true,
  text: false,
  select: false,
  "select-one": false
};
function ListItem({
  shortcut,
  sensor,
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

  console.log("sm", sensor.id
  )

//GEO case
  if (sensor
      // sensor.id !== tripwireId ||
      // sensor.id !== tripDirId
    ) {
      clearIcons.push([
        <Popup
          trigger={
            <Button
              size="small"
              key="marker-vis"
              floated="right"
              icon={showMapMarkers ? "map marker" : "map marker alternate"}
              // sensor="Markers"
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
              data-testid={`MarkerVis_${sensor.id}`}
            />
          }
          size="tiny"
          position="top center"
          content="Show Map Markers"
        />
      ]);
      clearIcons.push([
        <Popup
          trigger={
            <Button
              size="small"
              key="clear-map"
              floated="right"
              icon="delete"
              // sensor="Clear Map"
              color={color}
              onClick={async e => {
                e.stopPropagation();
                await clearMapData(sensor.id);
                updateSelected(null);
                updateSelected(sensor.id);
              }}
              data-testid={`ClearMap_${sensor.id}`}
            />
          }
          size="tiny"
          position="top center"
          content={`Clear ${sensor.name}`}
        />
      ]);
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
                await clearImageData(sensor.id);
                updateSelected(null);
                updateSelected(sensor.id);
              }}
              data-testid={`ClearImage_${sensor.id}`}
            />
          }
          size="tiny"
          position="top center"
          content={`Clear ${sensor.name}`}
          />
        ]);
  }



  if (onToggle && typeHidable["point"]) {
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
              onToggle(sensor);
              e.stopPropagation();
            }}
          />
        }
        key={sensor.id}
        size="tiny"
        position="top center"
        content={"Hide"}
      />
    );
  }

  const iconType = iconMapping["point"];
  const figureIcon = iconType ? (
    <Icon
      key="type-icon"
      name={iconType}
      style={{  opacity: 0.5, display: "inline-block", marginLeft: 5 }}
    />
  ) : null ;
  // if (!sensor.draw){
    // clearIcons = []
  // }
  console.log()
  return (
    <List.Item
      onClick={onSelect}
      disabled={disabled}
      active={selected}
      key={sensor.id}
      style={{   fontSize: "26px", verticalAlign: "middle" }}
      data-testid={`ManagerItem_${sensor.id}`}
    >
      <Hotkeys
        keyName={shortcut}
        onKeyDown={() => !disabled && onSelect && onSelect()}
        key={sensor.id}
      >
        <List.Content key={sensor.id}>
          {sensor.name}
          {figureIcon}
          {clearIcons}
          {icons}
        </List.Content>
      </Hotkeys>
    </List.Item>
  );
}

SensorManager.propTypes = {
  /** Title to display at top of the drawing manager */
  title: PropTypes.string,
  /** Each object contains information about the sensor to display */
  sensors: PropTypes.arrayOf(PropTypes.object).isRequired,
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
  /** Toggle the map markers (vertex number sensors)*/
  toggleMapMarkers: PropTypes.func,
  /** Toggle the image markers (vertex number sensors) */
  toggleImageMarkers: PropTypes.func,
  /** Indicator whether or not the markers should be shown */
  showMapMarkers: PropTypes.bool
};
