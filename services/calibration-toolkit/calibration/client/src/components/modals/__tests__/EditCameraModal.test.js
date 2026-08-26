// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import ReactDOM from "react-dom";
import axiosMock from "axios";
// Components using reactAlert need to be wrapped with an AlertProvider
import { Provider as AlertProvider } from "react-alert";
import AlertTemplate from "react-alert-template-basic";
import {
  render,
  cleanup,
  fireEvent,
  waitForElement
} from "@testing-library/react";
import "@testing-library/jest-dom/extend-expect";

import EditSensorModal from "../EditSensorModal";

afterEach(cleanup);
jest.mock("axios");

it("EditSensorModal renders without warnings", () => {
  const testReload = jest.fn();
  const testClose = jest.fn();
  const projectId = 1;
  const div = document.createElement("div");
  ReactDOM.render(
    <AlertProvider template={AlertTemplate}>
      <EditSensorModal
        modalShow={false}
        projectId={projectId}
        reloadSensors={testReload}
        onClose={testClose}
      />
    </AlertProvider>,
    div
  );
});

it("EditSensorModal test close function and tab selection", () => {
  const testReload = jest.fn();
  const testClose = jest.fn();
  const projectId = 1;
  const { getByTestId } = render(
    <AlertProvider template={AlertTemplate}>
      <EditSensorModal
        modalShow={true}
        projectId={projectId}
        reloadSensors={testReload}
        onClose={testClose}
      />
    </AlertProvider>
  );

  // The modal should render the edit sensor form
  fireEvent.click(getByTestId("CamFormClose"));
  expect(testClose).toHaveBeenCalled();

  const re = /tab-list-active/;
  expect(
    re.test(getByTestId("Tab_Change Metadata").getAttribute("class"))
  ).toBe(true);
  expect(re.test(getByTestId("Tab_Upload Image").getAttribute("class"))).toBe(
    false
  );
  fireEvent.click(getByTestId("Tab_Upload Image"));
  expect(
    re.test(getByTestId("Tab_Change Metadata").getAttribute("class"))
  ).toBe(false);
  expect(re.test(getByTestId("Tab_Upload Image").getAttribute("class"))).toBe(
    true
  );
});

it("EditSensorModal test form fields load in document", async () => {
  const testReload = jest.fn();
  const testClose = jest.fn();
  const projectId = 1;
  const sensorId = 1;
  const resp1 = {
    data: {
      intersection_set: [
        { id: 1, name: "intersection1" },
        { id: 2, name: "intersection2" }
      ],
      corridor_set: [
        { id: 1, name: "corridor1" },
        { id: 5, name: "corridor2" },
        { id: 3, name: "corridor2" }
      ],
      placeTypes_set: []
    }
  };
  const resp2 = {
    data: {
      sensorId: "CAMERA1",
      sensorName: "CAMERA1",
      majorRoad: "ROAD1",
      minorRoad: "ROAD2",
      originLat: 42.4897,
      originLng: -90.677,
      cardinalDirection: "ENE",
      intersection_set: [2],
      corridor_set: [1, 5, 3]
    }
  };
  // Mock call to load dropdown menu options
  axiosMock.get.mockResolvedValueOnce(resp1);
  // Mock call to load sensor data
  axiosMock.get.mockResolvedValueOnce(resp2);

  const { getByTestId } = render(
    <AlertProvider template={AlertTemplate}>
      <EditSensorModal
        modalShow={true}
        projectId={projectId}
        sensorId={sensorId}
        reloadSensors={testReload}
        onClose={testClose}
      />
    </AlertProvider>
  );

  expect(getByTestId("LoaderLoading")).toBeInTheDocument();
  // Need to use hack with regex to get the value via the innerHTML string
  // the element itself does not seem to have the value attribute
  const re = /value="(.*)"/;
  const sensorId = await waitForElement(() => getByTestId("CamSensorIdInput"));
  expect(sensorId.innerHTML.match(re)[1]).toBe(resp2.data.sensorId);
  const sensorName = await waitForElement(() => getByTestId("CamNameInput"));
  expect(sensorName.innerHTML.match(re)[1]).toBe(resp2.data.sensorName);
  const majorRoad = await waitForElement(() => getByTestId("CamMajRdInput"));
  expect(majorRoad.innerHTML.match(re)[1]).toBe(resp2.data.majorRoad);
  const minorRoad = await waitForElement(() => getByTestId("CamMinRdInput"));
  expect(minorRoad.innerHTML.match(re)[1]).toBe(resp2.data.minorRoad);
  const originLat = await waitForElement(() => getByTestId("CamLatInput"));
  expect(Number(originLat.innerHTML.match(re)[1])).toBe(resp2.data.originLat);
  const originLng = await waitForElement(() => getByTestId("CamLngInput"));
  expect(Number(originLng.innerHTML.match(re)[1])).toBe(resp2.data.originLng);
  const cardinalDirection = await waitForElement(() =>
    getByTestId("CamCardDirInput")
  );
  const cardDirRe = new RegExp(`${resp2.data.cardinalDirection}`, "g");

  expect(cardinalDirection.innerHTML.match(cardDirRe).length).toBe(2);
  const intersection = await waitForElement(() =>
    getByTestId("CamIntersecInput")
  );
  resp2.data.intersection_set.forEach(intId => {
    const intersectionRe = new RegExp(`value="${intId}"`, "g");
    expect(intersection.innerHTML.match(intersectionRe).length).toBe(1);
  });
  const corridor = await waitForElement(() => getByTestId("CamCorInput"));
  resp2.data.corridor_set.forEach(corId => {
    const corridorRe = new RegExp(`value="${corId}"`, "g");
    expect(corridor.innerHTML.match(corridorRe).length).toBe(1);
  });
});
