// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import ReactDOM from "react-dom";
import { BrowserRouter as Router } from "react-router-dom";
import { render, cleanup, fireEvent } from "@testing-library/react";
import "@testing-library/jest-dom/extend-expect";

import CalibrationButton from "../CalibrationButton";

afterEach(cleanup);

it("CalibrationButton renders without warnings", () => {
  const div = document.createElement("div");
  ReactDOM.render(
    <Router>
      <CalibrationButton />
    </Router>,
    div
  );
});

it("CalibrationButton renders calibrate if hasHomography = false", () => {
  const testEdit = jest.fn();
  const testCalib = jest.fn();
  const { queryByTestId, getByTestId } = render(
    <CalibrationButton
      hasHomography={false}
      onEditClick={testEdit}
      onCalibClick={testCalib}
    />
  );

  // Ensure edit button not in the document
  expect(queryByTestId("EditCalibrationBtn")).toBeNull();
  // Ensure calibrate button in the document and can be clicked
  expect(getByTestId("CalibrateBtn")).toBeInTheDocument();
  fireEvent.click(getByTestId("CalibrateBtn"));
  expect(testCalib).toHaveBeenCalled();
  expect(testEdit).toBeCalledTimes(0);
});

it("CalibrationButton renders edit if hasHomography = true", () => {
  const testEdit = jest.fn();
  const testCalib = jest.fn();
  const { queryByTestId, getByTestId } = render(
    <CalibrationButton
      hasHomography={true}
      onEditClick={testEdit}
      onCalibClick={testCalib}
    />
  );

  // Ensure edit button not in the document
  expect(queryByTestId("CalibrateBtn")).toBeNull();
  // Ensure calibrate button in the document and can be clicked
  expect(getByTestId("EditCalibrationBtn")).toBeInTheDocument();
  fireEvent.click(getByTestId("EditCalibrationBtn"));
  expect(testEdit).toHaveBeenCalled();
  expect(testCalib).toBeCalledTimes(0);
});
