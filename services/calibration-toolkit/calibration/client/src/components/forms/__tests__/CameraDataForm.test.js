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

import SensorDataForm from "../SensorDataForm";

afterEach(cleanup);
jest.mock("axios");

it("SensorDataForm renders without warnings", () => {
  const testApplyData = jest.fn();
  const testClose = jest.fn();
  const projectId = 1;
  const div = document.createElement("div");
  ReactDOM.render(
    <AlertProvider template={AlertTemplate}>
      <SensorDataForm
        projectId={projectId}
        onSaveData={testApplyData}
        onClose={testClose}
      />
    </AlertProvider>,
    div
  );
});

it("SensorDataForm test apply and close buttons", () => {
  const testApplyData = jest.fn();
  const testClose = jest.fn();
  const projectId = 1;
  const { getByTestId } = render(
    <AlertProvider template={AlertTemplate}>
      <SensorDataForm
        projectId={projectId}
        onSaveData={testApplyData}
        onClose={testClose}
      />
    </AlertProvider>
  );

  fireEvent.click(getByTestId("CamFormApply"));
  expect(testApplyData).toHaveBeenCalled();
  fireEvent.click(getByTestId("CamFormClose"));
  expect(testClose).toHaveBeenCalled();
});

it("SensorDataForm correctly loads backend data", async () => {
  const re = /value="(.*)"/;
  const testApplyData = jest.fn();
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
        { id: 2, name: "corridor2" }
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
      intersection_set: [1, 2],
      corridor_set: [1],
      fps: 10,
      fieldOfView: 45.5,
      direction: 140.563,
      deviceId: "7e1f0fa5",
      depth: 353.84,
      mmsInfo_host: "vms",
      mmsInfo_protocol: "",
      mmsInfo_type: "",
      videoURL: "",
    }
  };
  // Mock call to load dropdown menu options
  axiosMock.get.mockResolvedValueOnce(resp1);
  // Mock call to load sensor data
  axiosMock.get.mockResolvedValueOnce(resp2);

  const { getByTestId } = render(
    <AlertProvider template={AlertTemplate}>
      <SensorDataForm
        projectId={projectId}
        sensorId={sensorId}
        onSaveData={testApplyData}
        onClose={testClose}
      />
    </AlertProvider>
  );

  expect(getByTestId("LoaderLoading")).toBeInTheDocument();
  // Need to use hack with regex to get the value via the innerHTML string
  // the element itself does not seem to have the value attribute
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

  const fps = await waitForElement(() => getByTestId("CamFPSInput"));
  expect(Number(fps.innerHTML.match(re)[1])).toBe(resp2.data.fps);

  const depth = await waitForElement(() => getByTestId("CamDepthInput"));
  expect(Number(depth.innerHTML.match(re)[1])).toBe(resp2.data.depth);

  const fieldOfView = await waitForElement(() => getByTestId("CamFOVInput"));
  expect(Number(fieldOfView.innerHTML.match(re)[1])).toBe(resp2.data.fieldOfView);

  const direction = await waitForElement(() => getByTestId("CamDirectionInput"));
  expect(Number(direction.innerHTML.match(re)[1])).toBe(resp2.data.direction);

  const videoURL = await waitForElement(() => getByTestId("CamVideoURLInput"));
  expect(fps.innerHTML.match(re)[1]).toBe(resp2.data.videoURL);

  const mmsInfo_type = await waitForElement(() => getByTestId("CamMMSInfoTypeInput"));
  expect(mmsInfo_type.innerHTML.match(re)[1]).toBe(resp2.data.mmsInfo_type);

  const mmsInfo_host = await waitForElement(() => getByTestId("CamMMSInfoHostInput"));
  expect(mmsInfo_host.innerHTML.match(re)[1]).toBe(resp2.data.mmsInfo_host);

  const mmsInfo_protocol = await waitForElement(() => getByTestId("CamMMSInfoProtocolInput"));
  expect(mmsInfo_protocol.innerHTML.match(re)[1]).toBe(resp2.data.mmsInfo_protocol);

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
