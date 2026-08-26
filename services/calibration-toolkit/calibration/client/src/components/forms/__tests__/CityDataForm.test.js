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

import CityDataForm from "../CityDataForm";

afterEach(cleanup);
jest.mock("axios");

it("CityDataForm renders without warnings", () => {
  const testApplyData = jest.fn();
  const testClose = jest.fn();
  const div = document.createElement("div");
  ReactDOM.render(
    <AlertProvider template={AlertTemplate}>
      <CityDataForm onSaveData={testApplyData} onClose={testClose} />
    </AlertProvider>,
    div
  );
});

it("CityDataForm test apply and close buttons", () => {
  const testApplyData = jest.fn();
  const testClose = jest.fn();
  const { getByTestId } = render(
    <AlertProvider template={AlertTemplate}>
      <CityDataForm onSaveData={testApplyData} onClose={testClose} />
    </AlertProvider>
  );

  fireEvent.click(getByTestId("CityFormApply"));
  expect(testApplyData).toHaveBeenCalled();
  fireEvent.click(getByTestId("CityFormClose"));
  expect(testClose).toHaveBeenCalled();
});

it("CityDataForm correctly loads backend data", async () => {
  const re = /value="(.*)"/;
  const testApplyData = jest.fn();
  const testClose = jest.fn();
  const cityId = 1;
  const resp = {
    data: {
      name: "test_name1",
      originLat: 42.4897,
      originLng: -90.677,
      mapFile: "test_file"
    }
  };
  axiosMock.get.mockResolvedValueOnce(resp);

  const { getByTestId } = render(
    <AlertProvider template={AlertTemplate}>
      <CityDataForm
        onSaveData={testApplyData}
        onClose={testClose}
        cityId={cityId}
      />
    </AlertProvider>
  );

  expect(getByTestId("LoaderLoading")).toBeInTheDocument();
  // Need to use hack with regex to get the value via the innerHTML string
  // the element itself does not seem to have the value attribute
  const name = await waitForElement(() => getByTestId("CityNameInput"));
  expect(name.innerHTML.match(re)[1]).toBe(resp.data.name);
  const originLat = await waitForElement(() => getByTestId("CityLatInput"));
  expect(Number(originLat.innerHTML.match(re)[1])).toBe(resp.data.originLat);
  const originLng = await waitForElement(() => getByTestId("CityLngInput"));
  expect(Number(originLng.innerHTML.match(re)[1])).toBe(resp.data.originLng);
});
