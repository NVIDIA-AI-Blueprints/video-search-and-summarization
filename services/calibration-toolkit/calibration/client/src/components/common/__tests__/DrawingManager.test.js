// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import ReactDOM from "react-dom";
import { render, cleanup, fireEvent } from "@testing-library/react";
import "@testing-library/jest-dom/extend-expect";

import DrawingManager from "../DrawingManager";
import { calibId, roiId, calValId, linksId, corId } from "../../common/utils";

afterEach(cleanup);

it("DrawingManager renders without warnings when given labels prop", () => {
  const div = document.createElement("div");
  ReactDOM.render(
    <DrawingManager
      labels={[
        {
          type: "polygon",
          name: "Calibration",
          id: calibId
        },
        {
          type: "polygon",
          name: "ROI",
          id: roiId
        }
      ]}
    />,
    div
  );
});

it("DrawingManager renders without warnings when given labels prop", () => {
  const title = "testing_DrawingManager";
  const testSelect = jest.fn();
  const labels = [
    {
      type: "polygon",
      name: "Calibration",
      id: calibId
    },
    {
      type: "polygon",
      name: "ROI",
      id: roiId
    }
  ];
  const { getByTestId } = render(
    <DrawingManager title={title} labels={labels} onSelect={testSelect} />
  );
  // Check for title
  const titleId = "ManagerTitle";
  expect(getByTestId(titleId)).toBeInTheDocument();
  expect(getByTestId(titleId)).toHaveTextContent(title);

  labels.forEach(label => {
    const id = `ManagerItem_${label.id}`;
    expect(getByTestId(id)).toBeInTheDocument();
    expect(getByTestId(id)).toHaveTextContent(label.name);
    fireEvent.click(getByTestId(id));
  });
  expect(testSelect).toHaveBeenCalledTimes(labels.length);
});

it("DrawingManager renders clear buttons only for correct id values", () => {
  // problem with event.stopPropagation when testing DrawingManager
  // Gives error in test mode, can't test some passed functions without fix
  const testClearImage = jest.fn();
  const testClearMap = jest.fn();
  const testSelect = jest.fn();
  const labels = [
    {
      type: "polygon",
      name: "Calibration",
      id: calibId
    },
    {
      type: "polygon",
      name: "ROI",
      id: roiId
    },
    {
      type: "polyline",
      name: "Validation",
      id: calValId
    },
    {
      type: "polyline",
      name: "Road Links",
      id: linksId
    },
    {
      type: "polyline",
      name: "Corridor",
      id: corId
    }
  ];
  const { queryByTestId, getByTestId } = render(
    <DrawingManager
      labels={labels}
      onSelect={testSelect}
      clearImageData={testClearImage}
      clearMapData={testClearMap}
      showMapMarkers={true}
    />
  );

  // Check for clearing button and test click
  labels.forEach(label => {
    const imageId = `ClearImage_${label.id}`;
    const mapId = `ClearMap_${label.id}`;
    const markerId = `MarkerVis_${label.id}`;
    if (label.id === calibId || label.id === calValId) {
      // Clear Image Data
      expect(getByTestId(imageId)).toBeInTheDocument();
      expect(getByTestId(imageId)).toHaveTextContent("Clear Image");
    } else {
      expect(queryByTestId(imageId)).toBeNull();
    }
    if (label.id !== calValId) {
      // Clear Map Data
      expect(getByTestId(mapId)).toBeInTheDocument();
      expect(getByTestId(mapId)).toHaveTextContent("Clear Map");
    } else {
      expect(queryByTestId(mapId)).toBeNull();
    }
    if (label.id !== roiId) {
      // Marker Visibility
      expect(getByTestId(markerId)).toBeInTheDocument();
      expect(getByTestId(markerId)).toHaveTextContent("Markers");
    } else {
      expect(queryByTestId(markerId)).toBeNull();
    }
  });
});
