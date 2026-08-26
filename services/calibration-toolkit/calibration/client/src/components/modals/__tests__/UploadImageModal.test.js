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

import UploadImageModal from "../UploadImageModal";

afterEach(cleanup);
jest.mock("axios");

it("UploadImageModal renders without warnings", () => {
  const testReload = jest.fn();
  const testClose = jest.fn();
  const div = document.createElement("div");
  ReactDOM.render(
    <AlertProvider template={AlertTemplate}>
      <UploadImageModal
        modalShow={false}
        reloadSensors={testReload}
        onClose={testClose}
      />
    </AlertProvider>,
    div
  );
});

it("UploadImageModal test close function", async () => {
  const testReload = jest.fn();
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
      <UploadImageModal
        modalShow={true}
        sensorId={sensorId}
        reloadSensors={testReload}
        onClose={testClose}
      />
    </AlertProvider>
  );

  const closeButton = await waitForElement(() =>
    getByTestId("UploadFormClose")
  );
  fireEvent.click(closeButton);
  expect(testClose).toHaveBeenCalled();
});

it("UploadImageModal test form fields load in document", async () => {
  const testReload = jest.fn();
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
      <UploadImageModal
        modalShow={true}
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
  const rtspURL = await waitForElement(() => getByTestId("RTSPInput"));
  expect(rtspURL.innerHTML.match(re)[1]).toBe(resp.data.rtspURL);
});
