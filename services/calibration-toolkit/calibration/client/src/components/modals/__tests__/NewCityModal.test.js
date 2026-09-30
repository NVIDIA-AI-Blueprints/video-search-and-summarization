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

import NewCityModal from "../NewCityModal";

afterEach(cleanup);
jest.mock("axios");

it("NewCityModal renders without warnings", () => {
  const testReload = jest.fn();
  const testClose = jest.fn();
  const projectId = 1;
  const projectName = "test_project";
  const div = document.createElement("div");
  ReactDOM.render(
    <AlertProvider template={AlertTemplate}>
      <NewCityModal
        modalShow={false}
        projectId={projectId}
        projectName={projectName}
        reloadCities={testReload}
        onClose={testClose}
      />
    </AlertProvider>,
    div
  );
});

it("NewCityModal test close function", () => {
  const testReload = jest.fn();
  const testClose = jest.fn();
  const projectId = 1;
  const projectName = "test_project";
  const { getByTestId } = render(
    <AlertProvider template={AlertTemplate}>
      <NewCityModal
        modalShow={true}
        projectId={projectId}
        projectName={projectName}
        reloadCities={testReload}
        onClose={testClose}
      />
    </AlertProvider>
  );

  fireEvent.click(getByTestId("CityFormClose"));
  expect(testClose).toHaveBeenCalled();
});

it("NewCityModal test form fields load in document", async () => {
  const testReload = jest.fn();
  const testClose = jest.fn();
  const projectId = 1;
  const projectName = "test_project";
  const { getByTestId } = render(
    <AlertProvider template={AlertTemplate}>
      <NewCityModal
        modalShow={true}
        projectId={projectId}
        projectName={projectName}
        reloadCities={testReload}
        onClose={testClose}
      />
    </AlertProvider>
  );

  expect(getByTestId("NewCityTitle")).toHaveTextContent(projectName);
  expect(getByTestId("CityNameInput")).toBeInTheDocument();
  expect(getByTestId("CityLatInput")).toBeInTheDocument();
  expect(getByTestId("CityLngInput")).toBeInTheDocument();
});
