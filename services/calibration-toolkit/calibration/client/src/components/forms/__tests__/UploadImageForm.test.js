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

import UploadImageForm from "../UploadImageForm";

afterEach(cleanup);
jest.mock("axios");

it("UploadImageForm renders without warnings", () => {
  const testApplyData = jest.fn();
  const testClose = jest.fn();
  const div = document.createElement("div");
  ReactDOM.render(
    <AlertProvider template={AlertTemplate}>
      <UploadImageForm onSaveData={testApplyData} onClose={testClose} />
    </AlertProvider>,
    div
  );
});

it("UploadImageForm test apply and close buttons", async () => {
  const testSave = jest.fn();
  const testClose = jest.fn();
  const sensorId = 1;
  const resp = {
    data: {
      rtspURL: "rtsp://test"
    }
  };
  axiosMock.get.mockResolvedValueOnce(resp);
  const { getByTestId } = render(
    <AlertProvider template={AlertTemplate}>
      <UploadImageForm
        onSaveData={testSave}
        onClose={testClose}
        sensorId={sensorId}
      />
    </AlertProvider>
  );

  const saveButton = await waitForElement(() => getByTestId("RTSPSave"));
  fireEvent.click(saveButton);
  expect(testSave).toHaveBeenCalled();
  const closeButton = await waitForElement(() =>
    getByTestId("UploadFormClose")
  );
  fireEvent.click(closeButton);
  expect(testClose).toHaveBeenCalled();
});

it("UploadImageForm correctly loads backend data", async () => {
  const re = /value="(.*)"/;
  const testSave = jest.fn();
  const testClose = jest.fn();
  const sensorId = 1;
  const resp = {
    data: {
      rtspURL: "rtsp://test"
    }
  };
  axiosMock.get.mockResolvedValueOnce(resp);

  const { getByTestId } = render(
    <AlertProvider template={AlertTemplate}>
      <UploadImageForm
        onSaveData={testSave}
        onClose={testClose}
        sensorId={sensorId}
      />
    </AlertProvider>
  );

  expect(getByTestId("LoaderLoading")).toBeInTheDocument();
  // Need to use hack with regex to get the value via the innerHTML string
  // the element itself does not seem to have the value attribute
  const rtspURL = await waitForElement(() => getByTestId("RTSPInput"));
  expect(rtspURL.innerHTML.match(re)[1]).toBe(resp.data.rtspURL);
});
