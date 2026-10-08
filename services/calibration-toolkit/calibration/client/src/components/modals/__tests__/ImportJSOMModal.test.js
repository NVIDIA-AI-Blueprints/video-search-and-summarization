// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import ReactDOM from "react-dom";
// Components using reactAlert need to be wrapped with an AlertProvider
import { Provider as AlertProvider } from "react-alert";
import AlertTemplate from "react-alert-template-basic";
import { render, cleanup, fireEvent } from "@testing-library/react";
import "@testing-library/jest-dom/extend-expect";

import ImportJSONModal from "../ImportJSONModal";

afterEach(cleanup);

it("ImportJSONModal renders without warnings", () => {
  const testUpload = jest.fn();
  const testClose = jest.fn();
  const div = document.createElement("div");
  ReactDOM.render(
    <AlertProvider template={AlertTemplate}>
      <ImportJSONModal
        modalShow={false}
        onUpload={testUpload}
        onClose={testClose}
      />
    </AlertProvider>,
    div
  );
});

it("UploadImageModal test close function", () => {
  const testUpload = jest.fn();
  const testClose = jest.fn();
  const { getByTestId } = render(
    <AlertProvider template={AlertTemplate}>
      <ImportJSONModal
        modalShow={true}
        onUpload={testUpload}
        onClose={testClose}
      />
    </AlertProvider>
  );

  fireEvent.click(getByTestId("JSONImportClose"));
  expect(testClose).toHaveBeenCalled();
});
