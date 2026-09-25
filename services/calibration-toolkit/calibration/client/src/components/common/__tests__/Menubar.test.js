// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import ReactDOM from "react-dom";
import { BrowserRouter as Router } from "react-router-dom";
import { render, cleanup, waitForElement } from "@testing-library/react";
import "@testing-library/jest-dom/extend-expect";
import axiosMock from "axios";

import Menubar from "../Menubar";

afterEach(cleanup);
jest.mock("axios");

it("Menubar renders without warnings", () => {
  const div = document.createElement("div");
  ReactDOM.render(
    <Router>
      <Menubar />
    </Router>,
    div
  );
});

it("Menubar loads projects for dropdown", async () => {
  const resp = {
    data: [
      { id: 1, name: "project1" },
      { id: 2, name: "project2" },
      { id: 3, name: "project3" }
    ]
  };

  await axiosMock.get.mockResolvedValueOnce(resp);

  const { getByTestId } = render(
    <Router>
      <Menubar active={2} />
    </Router>
  );

  resp.data.forEach(async project => {
    const dropdown = await waitForElement(() => getByTestId(project.name));
    expect(dropdown).toBeInTheDocument();
  });
});
