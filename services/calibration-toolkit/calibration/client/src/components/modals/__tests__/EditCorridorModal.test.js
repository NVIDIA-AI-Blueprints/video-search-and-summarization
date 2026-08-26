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

import EditCorridorModal from "../EditCorridorModal";

afterEach(cleanup);
jest.mock("axios");

it("EditCorridorModal renders without warnings", () => {
  const testReload = jest.fn();
  const testClose = jest.fn();
  const div = document.createElement("div");
  ReactDOM.render(
    <AlertProvider template={AlertTemplate}>
      <EditCorridorModal
        modalShow={false}
        reloadCorridors={testReload}
        onClose={testClose}
      />
    </AlertProvider>,
    div
  );
});

it("EditCorridorModal test close function", () => {
  const testReload = jest.fn();
  const testClose = jest.fn();
  const { getByTestId } = render(
    <AlertProvider template={AlertTemplate}>
      <EditCorridorModal
        modalShow={true}
        reloadCorridors={testReload}
        onClose={testClose}
      />
    </AlertProvider>
  );

  fireEvent.click(getByTestId("CorFormClose"));
  expect(testClose).toHaveBeenCalled();
});

it("EditCorridorModal test form fields load in document", async () => {
  const testReload = jest.fn();
  const testClose = jest.fn();
  const corridorId = 1;
  const resp = {
    data: {
      name: "test_name1",
      originLat: 42.4897,
      originLng: -90.677
    }
  };
  axiosMock.get.mockResolvedValueOnce(resp);
  const { getByTestId } = render(
    <AlertProvider template={AlertTemplate}>
      <EditCorridorModal
        corridorId={corridorId}
        modalShow={true}
        reloadCorridors={testReload}
        onClose={testClose}
      />
    </AlertProvider>
  );

  expect(getByTestId("LoaderLoading")).toBeInTheDocument();
  // Need to use hack with regex to get the value via the innerHTML string
  // the element itself does not seem to have the value attribute
  const re = /value="(.*)"/;
  const name = await waitForElement(() => getByTestId("CorNameInput"));
  expect(name.innerHTML.match(re)[1]).toBe(resp.data.name);
  const originLat = await waitForElement(() => getByTestId("CorLatInput"));
  expect(Number(originLat.innerHTML.match(re)[1])).toBe(resp.data.originLat);
  const originLng = await waitForElement(() => getByTestId("CorLngInput"));
  expect(Number(originLng.innerHTML.match(re)[1])).toBe(resp.data.originLng);
});
