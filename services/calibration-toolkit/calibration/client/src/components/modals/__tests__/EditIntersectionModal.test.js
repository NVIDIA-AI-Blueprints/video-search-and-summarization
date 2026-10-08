// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import ReactDOM from "react-dom";
import axiosMock from "axios";
// Components using reactAlert need to be wrapped with an AlertProvider
import { Provider as AlertProvider } from "react-alert";
// Componetns using link or history need to be wrapped in a router
import { BrowserRouter as Router } from "react-router-dom";
import AlertTemplate from "react-alert-template-basic";
import {
  render,
  cleanup,
  fireEvent,
  waitForElement
} from "@testing-library/react";
import "@testing-library/jest-dom/extend-expect";

import EditIntersectionModal from "../EditIntersectionModal";

afterEach(cleanup);
jest.mock("axios");

it("EditIntersectionModal renders without warnings", () => {
  const testReload = jest.fn();
  const testClose = jest.fn();
  const div = document.createElement("div");
  ReactDOM.render(
    <AlertProvider template={AlertTemplate}>
      <EditIntersectionModal
        modalShow={false}
        reloadIntersections={testReload}
        onClose={testClose}
      />
    </AlertProvider>,
    div
  );
});

it("EditIntersectionModal test close function", () => {
  const testReload = jest.fn();
  const testClose = jest.fn();
  const { getByTestId } = render(
    <AlertProvider template={AlertTemplate}>
      <EditIntersectionModal
        modalShow={true}
        reloadIntersections={testReload}
        onClose={testClose}
      />
    </AlertProvider>
  );

  fireEvent.click(getByTestId("IntersecFormClose"));
  expect(testClose).toHaveBeenCalled();
});

it("EditIntersectionModal test form fields load in document", async () => {
  const testReload = jest.fn();
  const testClose = jest.fn();
  const intersectionId = 1;
  const resp = {
    data: {
      description: "test description",
      majorRoad: "ROAD1",
      minorRoad: "ROAD2",
      name: "ROAD1_AND_ROAD2",
      originLat: 42.4897,
      originLng: -90.677
    }
  };
  axiosMock.get.mockResolvedValueOnce(resp);
  const { getByTestId } = render(
    <AlertProvider template={AlertTemplate}>
      <Router>
        <EditIntersectionModal
          intersectionId={intersectionId}
          modalShow={true}
          reloadIntersections={testReload}
          onClose={testClose}
        />
      </Router>
    </AlertProvider>
  );

  expect(getByTestId("LoaderLoading")).toBeInTheDocument();
  // Need to use hack with regex to get the value via the innerHTML string
  // the element itself does not seem to have the value attribute
  const re = /value="(.*)"/;
  const description = await waitForElement(() =>
    getByTestId("IntersecDescInput")
  );
  expect(description.innerHTML.match(re)[1]).toBe(resp.data.description);
  const majorRoad = await waitForElement(() =>
    getByTestId("IntersecMajRdInput")
  );
  expect(majorRoad.innerHTML.match(re)[1]).toBe(resp.data.majorRoad);
  const minorRoad = await waitForElement(() =>
    getByTestId("IntersecMinRdInput")
  );
  expect(minorRoad.innerHTML.match(re)[1]).toBe(resp.data.minorRoad);
  const name = await waitForElement(() => getByTestId("IntersecNameInput"));
  expect(name.innerHTML.match(re)[1]).toBe(resp.data.name);
  const originLat = await waitForElement(() => getByTestId("IntersecLatInput"));
  expect(Number(originLat.innerHTML.match(re)[1])).toBe(resp.data.originLat);
  const originLng = await waitForElement(() => getByTestId("IntersecLngInput"));
  expect(Number(originLng.innerHTML.match(re)[1])).toBe(resp.data.originLng);
});
