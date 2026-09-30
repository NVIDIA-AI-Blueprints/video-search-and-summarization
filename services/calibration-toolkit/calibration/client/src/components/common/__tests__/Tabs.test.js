// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import ReactDOM from "react-dom";
import { render, cleanup, fireEvent } from "@testing-library/react";
import "@testing-library/jest-dom/extend-expect";

import Tabs from "../Tabs";
// This tests both the Tabs and the Tab file

afterEach(cleanup);

it("Tabs renders without warnings when given labels prop", () => {
  const div = document.createElement("div");
  ReactDOM.render(
    <Tabs>
      <div label="TestTab1">
        <p>Testing Tabs</p>
      </div>
      <div label="TestTab2">
        <p>Testing Tabs</p>
      </div>
    </Tabs>,
    div
  );
});

it("Tabs renders all tabs when >2 tabs are given", () => {
  const labels = ["TestTab1", "TestTab2"];
  const { getByTestId } = render(
    <Tabs>
      {labels.map((label, index) => {
        return (
          <div label={label} key={index}>
            <p>Testing Tabs</p>
          </div>
        );
      })}
    </Tabs>
  );

  // Check that all tabs are in the document
  labels.forEach(label => {
    const labelId = `Tab_${label}`;
    expect(getByTestId(labelId)).toBeInTheDocument();
  });

  // Test the active tabs
  const activeTab = "tab-list-item tab-list-active";
  const inactiveTab = "tab-list-item";
  expect(getByTestId(`Tab_${labels[0]}`).getAttribute("class")).toBe(activeTab);
  expect(getByTestId(`Tab_${labels[1]}`).getAttribute("class")).toBe(
    inactiveTab
  );
  // Click Tab 2
  fireEvent.click(getByTestId(`Tab_${labels[1]}`));
  expect(getByTestId(`Tab_${labels[0]}`).getAttribute("class")).toBe(
    inactiveTab
  );
  expect(getByTestId(`Tab_${labels[1]}`).getAttribute("class")).toBe(activeTab);
  // Click Tab 1
  fireEvent.click(getByTestId(`Tab_${labels[0]}`));
  expect(getByTestId(`Tab_${labels[0]}`).getAttribute("class")).toBe(activeTab);
  expect(getByTestId(`Tab_${labels[1]}`).getAttribute("class")).toBe(
    inactiveTab
  );
});
