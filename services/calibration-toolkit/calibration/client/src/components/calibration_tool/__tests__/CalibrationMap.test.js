// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import ReactDOM from "react-dom";
import { render, cleanup } from "@testing-library/react";
import "@testing-library/jest-dom/extend-expect";

import CalibrationMap from "../CalibrationMap";

const API_KEY = process.env.REACT_APP_GOOGLE_MAPS_API_KEY;

afterEach(cleanup);

it("CalibrationMap renders without crashing", () => {
  const div = document.createElement("div");
  ReactDOM.render(
    <CalibrationMap
      id={1}
      type={"sensors"}
      API_KEY={API_KEY}
      mapCenter={{ lat: 42.4897, lng: -90.677 }}
      mapZoom={18}
      shapes={{}}
    />,
    div
  );
});
