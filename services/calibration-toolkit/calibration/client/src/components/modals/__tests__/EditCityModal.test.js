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

import EditCityModal from "../EditCityModal";

afterEach(cleanup);
jest.mock("axios");

it("EditCityModal renders without warnings", () => {
  const testReload = jest.fn();
  const testClose = jest.fn();
  const testDelete = jest.fn();
  const div = document.createElement("div");
  ReactDOM.render(
    <AlertProvider template={AlertTemplate}>
      <EditCityModal
        modalShow={false}
        reloadCity={testReload}
        onClose={testClose}
        onDelete={testDelete}
      />
    </AlertProvider>,
    div
  );
});

it("EditCityModal test close function", () => {
  const testReload = jest.fn();
  const testClose = jest.fn();
  const testDelete = jest.fn();
  const { getByTestId } = render(
    <AlertProvider template={AlertTemplate}>
      <EditCityModal
        modalShow={true}
        reloadCity={testReload}
        onClose={testClose}
        onDelete={testDelete}
      />
    </AlertProvider>
  );

  fireEvent.click(getByTestId("CityFormClose"));
  expect(testClose).toHaveBeenCalled();
});

it("EditCityModal test form fields load in document", async () => {
  const testReload = jest.fn();
  const testClose = jest.fn();
  const testDelete = jest.fn();
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
      <EditCityModal
        cityId={cityId}
        modalShow={true}
        reloadCity={testReload}
        onClose={testClose}
        onDelete={testDelete}
      />
    </AlertProvider>
  );

  expect(getByTestId("LoaderLoading")).toBeInTheDocument();
  // Need to use hack with regex to get the value via the innerHTML string
  // the element itself does not seem to have the value attribute
  const re = /value="(.*)"/;
  const name = await waitForElement(() => getByTestId("CityNameInput"));
  expect(name.innerHTML.match(re)[1]).toBe(resp.data.name);
  const originLat = await waitForElement(() => getByTestId("CityLatInput"));
  expect(Number(originLat.innerHTML.match(re)[1])).toBe(resp.data.originLat);
  const originLng = await waitForElement(() => getByTestId("CityLngInput"));
  expect(Number(originLng.innerHTML.match(re)[1])).toBe(resp.data.originLng);
});
