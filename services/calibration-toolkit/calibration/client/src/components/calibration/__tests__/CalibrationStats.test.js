// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import ReactDOM from "react-dom";
// Components using reactAlert need to be wrapped with an AlertProvider
import { Provider as AlertProvider } from "react-alert";
import AlertTemplate from "react-alert-template-basic";
import { render, cleanup, fireEvent } from "@testing-library/react";
import "@testing-library/jest-dom/extend-expect";

import CalibrationStats from "../CalibrationStats";

afterEach(cleanup);

it("CalibrationStats renders without warnings", () => {
  const div = document.createElement("div");
  ReactDOM.render(
    <AlertProvider template={AlertTemplate}>
      <CalibrationStats />
    </AlertProvider>,
    div
  );
});

it("CalibrationStats renders text warning if no data given", () => {
  const { queryByTestId, getByTestId } = render(
    <AlertProvider template={AlertTemplate}>
      <CalibrationStats />
    </AlertProvider>
  );

  // Check that warning exists and accept calibration not in document
  expect(getByTestId("HomographyWarning")).toBeInTheDocument();
  expect(queryByTestId("AcceptCalibration")).toBeNull();
});

it("CalibrationStats render correctly given example data", () => {
  const testAccept = jest.fn();
  const height = 960;
  // Lat needs to be flipped because values were taken from backend storage
  // Origin is top left in storage
  // Origin is bottom left in image canvas, can't be changed without a hack
  const imagePoints = [
    { lat: height - 491.9477641095558, lng: 181.21291483364985 },
    { lat: height - 490.876245932481, lng: 295.95449729834394 },
    { lat: height - 490.876245932481, lng: 549.0293894634636 }
  ];
  const mapPoints = [
    { lat: 42.4896791491784, lng: -90.6771634398005 },
    { lat: 42.489649481296844, lng: -90.67714198212838 },
    { lat: 42.48958223404656, lng: -90.6771111367247 }
  ];
  const trueErrors = {
    "0": 1.5489555549970082,
    "1": 0.171310650959724,
    "2": 1.5771525483152318
  };

  const { getByTestId } = render(
    <AlertProvider template={AlertTemplate}>
      <CalibrationStats
        height={height}
        homography={
          "[[-0.029301839066477483, 0.07790302096188301, -90.6766389364472], [0.013730106903864819, -0.03650456767103275, 42.48978593857981], [0.00032314573282267954, -0.0008591371208096651, 1.0]]"
        }
        imagePoints={imagePoints}
        mapPoints={mapPoints}
        onAcceptCalib={testAccept}
      />
    </AlertProvider>
  );

  // Check points are adequate
  imagePoints.forEach((point, index) => {
    expect(getByTestId(`RepErr_${index}`)).toBeInTheDocument();
    expect(getByTestId(`RepErr_${index}`)).toHaveTextContent(trueErrors[index]);
  });

  // Check accept function
  expect(getByTestId("AcceptCalibration")).toBeInTheDocument();
  fireEvent.click(getByTestId("AcceptCalibration"));
  expect(testAccept).toHaveBeenCalled();
});

it("CalibrationStats accept calibration doesn't work if errors too large", () => {
  const testAccept = jest.fn();
  const height = 960;
  // Random imagePoint to produce large error (>3m)
  const imagePoints = [{ lat: 491, lng: 181 }];
  const mapPoints = [{ lat: 42.4896791491784, lng: -90.6771634398005 }];

  const { getByTestId } = render(
    <AlertProvider template={AlertTemplate}>
      <CalibrationStats
        height={height}
        homography={
          "[[-0.029301839066477483, 0.07790302096188301, -90.6766389364472], [0.013730106903864819, -0.03650456767103275, 42.48978593857981], [0.00032314573282267954, -0.0008591371208096651, 1.0]]"
        }
        imagePoints={imagePoints}
        mapPoints={mapPoints}
        onAcceptCalib={testAccept}
      />
    </AlertProvider>
  );

  // Check value is sufficiently large
  imagePoints.forEach((point, index) => {
    expect(getByTestId(`RepErr_${index}`)).toBeInTheDocument();
    expect(Number(getByTestId(`RepErr_${index}`).innerHTML)).toBeGreaterThan(3);
  });

  // Check accept function has not been called because error too large
  expect(getByTestId("AcceptCalibration")).toBeInTheDocument();
  fireEvent.click(getByTestId("AcceptCalibration"));
  expect(testAccept).toBeCalledTimes(0);
});
