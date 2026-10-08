// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import ReactDOM from "react-dom";
import { render, cleanup, fireEvent } from "@testing-library/react";
import "@testing-library/jest-dom/extend-expect";

import DeleteButton from "../DeleteButton";

afterEach(cleanup);

it("DeleteButton renders without warnings", () => {
  const div = document.createElement("div");
  ReactDOM.render(
    <DeleteButton
      label={"button"}
      onConfirmDelete={() => console.log("Delete")}
    />,
    div
  );
});

it("DeleteButton renders with correct label", () => {
  const buttonlabel = "buttonLabel";
  const { getByTestId } = render(
    <DeleteButton
      label={buttonlabel}
      onConfirmDelete={() => console.log("Delete")}
    />
  );

  expect(getByTestId("DeleteButton")).toHaveTextContent(buttonlabel);
});

it("DeleteButton opens and closes modal properly", () => {
  const buttonlabel = "buttonLabel";
  const { queryByTestId, getByTestId } = render(
    <DeleteButton
      label={buttonlabel}
      onConfirmDelete={() => console.log("Delete")}
    />
  );

  // Open the modal
  fireEvent.click(getByTestId("DeleteButton"), { button: 0 });
  expect(getByTestId("ConfirmDeleteButton")).toBeInTheDocument();
  // Close the modal
  fireEvent.click(getByTestId("CancelDelete"), { button: 0 });
  const cancelDelete = queryByTestId("CancelDelete");
  expect(cancelDelete).toBeNull();
  expect(getByTestId("DeleteButton")).toHaveTextContent(buttonlabel);
});

it("DeleteButton opens and runs passed function onConfirmDelete", () => {
  const testDelete = jest.fn();
  const buttonlabel = "buttonLabel";
  const { queryByTestId, getByTestId } = render(
    <DeleteButton label={buttonlabel} onConfirmDelete={testDelete} />
  );

  // Open the modal
  fireEvent.click(getByTestId("DeleteButton"), { button: 0 });
  expect(getByTestId("ConfirmDeleteButton")).toBeInTheDocument();
  // Click the delete button
  fireEvent.click(getByTestId("ConfirmDeleteButton"), { button: 0 });
  expect(testDelete).toHaveBeenCalled();
  // Close the modal
  fireEvent.click(getByTestId("CancelDelete"), { button: 0 });
  const cancelDelete = queryByTestId("CancelDelete");
  expect(cancelDelete).toBeNull();
  expect(getByTestId("DeleteButton")).toHaveTextContent(buttonlabel);
});
