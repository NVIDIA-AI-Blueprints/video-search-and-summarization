// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import ReactDOM from "react-dom";
// Components using reactAlert need to be wrapped with an AlertProvider
import { Provider as AlertProvider } from "react-alert";
import AlertTemplate from "react-alert-template-basic";
import { render, cleanup, fireEvent } from "@testing-library/react";
import "@testing-library/jest-dom/extend-expect";

import NewSensorModal from "../NewSensorModal";

afterEach(cleanup);

it("EditSensorModal renders without warnings", () => {
  const testReload = jest.fn();
  const testClose = jest.fn();
  const projectId = 1;
  const div = document.createElement("div");
  ReactDOM.render(
    <AlertProvider template={AlertTemplate}>
      <NewSensorModal
        modalShow={false}
        projectId={projectId}
        reloadSensors={testReload}
        onClose={testClose}
      />
    </AlertProvider>,
    div
  );
});

it("NewSensorModal test close function and tab selection", () => {
  const testReload = jest.fn();
  const testClose = jest.fn();
  const projectId = 1;
  const { getByTestId } = render(
    <AlertProvider template={AlertTemplate}>
      <NewSensorModal
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
});

it("NewSensorModal test form fields load in document", () => {
  const testReload = jest.fn();
  const testClose = jest.fn();
  const projectId = 1;

  const { getByTestId } = render(
    <AlertProvider template={AlertTemplate}>
      <NewSensorModal
        modalShow={true}
        projectId={projectId}
        reloadSensors={testReload}
        onClose={testClose}
      />
    </AlertProvider>
  );

  expect(getByTestId("CamSensorIdInput")).toBeInTheDocument();
  expect(getByTestId("CamNameInput")).toBeInTheDocument();
  expect(getByTestId("CamMajRdInput")).toBeInTheDocument();
  expect(getByTestId("CamMinRdInput")).toBeInTheDocument();
  expect(getByTestId("CamLatInput")).toBeInTheDocument();
  expect(getByTestId("CamLngInput")).toBeInTheDocument();
  expect(getByTestId("CamCardDirInput")).toBeInTheDocument();
  expect(getByTestId("CamIntersecInput")).toBeInTheDocument();
  expect(getByTestId("CamCorInput")).toBeInTheDocument();
});
