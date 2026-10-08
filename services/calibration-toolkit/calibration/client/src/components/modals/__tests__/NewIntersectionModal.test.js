// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import ReactDOM from "react-dom";
// Components using reactAlert need to be wrapped with an AlertProvider
import { Provider as AlertProvider } from "react-alert";
import AlertTemplate from "react-alert-template-basic";
import { render, cleanup, fireEvent } from "@testing-library/react";
import "@testing-library/jest-dom/extend-expect";

import NewIntersectionModal from "../NewIntersectionModal";

afterEach(cleanup);

it("NewIntersectionModal renders without warnings", () => {
  const testReload = jest.fn();
  const testClose = jest.fn();
  const projectId = 1;
  const cityId = 1;
  const cityName = "test_city";
  const div = document.createElement("div");
  ReactDOM.render(
    <AlertProvider template={AlertTemplate}>
      <NewIntersectionModal
        modalShow={false}
        projectId={projectId}
        cityId={cityId}
        cityName={cityName}
        reloadIntersections={testReload}
        onClose={testClose}
      />
    </AlertProvider>,
    div
  );
});

it("NewIntersectionModal test close function", () => {
  const testReload = jest.fn();
  const testClose = jest.fn();
  const projectId = 1;
  const cityId = 1;
  const cityName = "test_city";
  const { getByTestId } = render(
    <AlertProvider template={AlertTemplate}>
      <NewIntersectionModal
        modalShow={true}
        projectId={projectId}
        cityId={cityId}
        cityName={cityName}
        reloadIntersections={testReload}
        onClose={testClose}
      />
    </AlertProvider>
  );

  fireEvent.click(getByTestId("IntersecFormClose"));
  expect(testClose).toHaveBeenCalled();
});

it("NewIntersectionModal test form fields load in document", () => {
  const testReload = jest.fn();
  const testClose = jest.fn();
  const projectId = 1;
  const cityId = 1;
  const cityName = "test_city";
  const { getByTestId } = render(
    <AlertProvider template={AlertTemplate}>
      <NewIntersectionModal
        modalShow={true}
        projectId={projectId}
        cityId={cityId}
        cityName={cityName}
        reloadIntersections={testReload}
        onClose={testClose}
      />
    </AlertProvider>
  );

  expect(getByTestId("NewIntersecTitle")).toHaveTextContent(cityName);
  expect(getByTestId("IntersecNameInput")).toBeInTheDocument();
  expect(getByTestId("IntersecLatInput")).toBeInTheDocument();
  expect(getByTestId("IntersecLngInput")).toBeInTheDocument();
});
