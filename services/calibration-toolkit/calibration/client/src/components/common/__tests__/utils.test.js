// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import "@testing-library/jest-dom/extend-expect";

import {
  formDataValidation,
  setEdited,
  updateDataValue,
  updateDataValidation
} from "../utils";

it("formValidation test correct input values", () => {
  let returnVal;
  const stringValue = "test_value";
  const numberValueAsStr = "10";
  const numberValue = Number(numberValueAsStr);

  // Test sensorId
  returnVal = formDataValidation("sensorId", stringValue);
  expect(returnVal.re).toEqual(/^[a-zA-z0-9_]+$/);
  expect(returnVal.value).toEqual(stringValue);
  // Test majorRoad
  returnVal = formDataValidation("majorRoad", stringValue);
  expect(returnVal.re).toEqual(/^[a-zA-z0-9_]+$/);
  expect(returnVal.value).toEqual(stringValue);
  // Test minorRoad
  returnVal = formDataValidation("minorRoad", stringValue);
  expect(returnVal.re).toEqual(/^[a-zA-z0-9_]+$/);
  expect(returnVal.value).toEqual(stringValue);
  // Test cardinalDirection
  returnVal = formDataValidation("cardinalDirection", stringValue);
  expect(returnVal.re).toEqual(/^[a-zA-z0-9_]+$/);
  expect(returnVal.value).toEqual(stringValue);
  // Test name
  returnVal = formDataValidation("name", stringValue);
  expect(returnVal.re).toEqual(/^[a-zA-z0-9_ ]+$/);
  expect(returnVal.value).toEqual(stringValue);
  // Test sensorName
  returnVal = formDataValidation("sensorName", stringValue);
  expect(returnVal.re).toEqual(/^[a-zA-z0-9_ ]+$/);
  expect(returnVal.value).toEqual(stringValue);
  // Test rtspURL
  returnVal = formDataValidation("rtspURL", stringValue);
  expect(returnVal.re).toEqual(/rtsp:(.*)/);
  expect(returnVal.value).toEqual(stringValue);
  // Test mapFile
  returnVal = formDataValidation("mapFile", stringValue);
  expect(returnVal.re).toEqual(/(.*)bz2/);
  expect(returnVal.value).toEqual(stringValue);
  // Test originLat
  returnVal = formDataValidation("originLat", numberValueAsStr);
  expect(returnVal.re).toEqual(/^-?[0-9]\d*(\.\d+)?$/);
  expect(returnVal.value).toEqual(numberValue);
  // Test latitude
  returnVal = formDataValidation("latitude", numberValueAsStr);
  expect(returnVal.re).toEqual(/^-?[0-9]\d*(\.\d+)?$/);
  expect(returnVal.value).toEqual(numberValue);
  // Test originLng
  returnVal = formDataValidation("originLng", numberValueAsStr);
  expect(returnVal.re).toEqual(/^-?[0-9]\d*(\.\d+)?$/);
  expect(returnVal.value).toEqual(numberValue);
  // Test longitude
  returnVal = formDataValidation("longitude", numberValueAsStr);
  expect(returnVal.re).toEqual(/^-?[0-9]\d*(\.\d+)?$/);
  expect(returnVal.value).toEqual(numberValue);
  // Test description
  returnVal = formDataValidation("description", stringValue);
  expect(returnVal.re).toEqual(/.*/);
  expect(returnVal.value).toEqual(stringValue);
});

it("form validation test incorrect input values", () => {
  let returnVal;

  // Test strings of length > 200
  let stringValue = "a".repeat(201);
  // sensorId
  returnVal = formDataValidation("sensorId", stringValue);
  expect(returnVal.re).toEqual(/a^/);
  expect(returnVal.value).toEqual(stringValue);
  // majorRoad
  returnVal = formDataValidation("majorRoad", stringValue);
  expect(returnVal.re).toEqual(/a^/);
  expect(returnVal.value).toEqual(stringValue);
  // minorRoad
  returnVal = formDataValidation("minorRoad", stringValue);
  expect(returnVal.re).toEqual(/a^/);
  expect(returnVal.value).toEqual(stringValue);
  // name
  returnVal = formDataValidation("name", stringValue);
  expect(returnVal.re).toEqual(/a^/);
  expect(returnVal.value).toEqual(stringValue);
  // sensorName
  returnVal = formDataValidation("sensorName", stringValue);
  expect(returnVal.re).toEqual(/a^/);
  expect(returnVal.value).toEqual(stringValue);

  // Test strings of length > 400
  stringValue = "a".repeat(401);
  // description
  returnVal = formDataValidation("description", stringValue);
  expect(returnVal.re).toEqual(/a^/);
  expect(returnVal.value).toEqual(stringValue);

  // Test bounds of originLat and latitude
  returnVal = formDataValidation("originLat", "90");
  expect(returnVal.re).toEqual(/^-?[0-9]\d*(\.\d+)?$/);
  expect(returnVal.value).toEqual("false");
  returnVal = formDataValidation("latitude", "-90");
  expect(returnVal.re).toEqual(/^-?[0-9]\d*(\.\d+)?$/);
  expect(returnVal.value).toEqual("false");
  returnVal = formDataValidation("originLat", "0");
  expect(returnVal.re).toEqual(/^-?[0-9]\d*(\.\d+)?$/);
  expect(returnVal.value).toEqual("false");
  returnVal = formDataValidation("latitude", "not a number");
  expect(returnVal.re).toEqual(/^-?[0-9]\d*(\.\d+)?$/);
  expect(returnVal.value).toEqual(NaN);
  // Test bounds of originLng and longitude
  returnVal = formDataValidation("originLng", "181");
  expect(returnVal.re).toEqual(/^-?[0-9]\d*(\.\d+)?$/);
  expect(returnVal.value).toEqual("false");
  returnVal = formDataValidation("longitude", "-181");
  expect(returnVal.re).toEqual(/^-?[0-9]\d*(\.\d+)?$/);
  expect(returnVal.value).toEqual("false");
  returnVal = formDataValidation("originLng", "0");
  expect(returnVal.re).toEqual(/^-?[0-9]\d*(\.\d+)?$/);
  expect(returnVal.value).toEqual("false");
  returnVal = formDataValidation("longitude", "not a number");
  expect(returnVal.re).toEqual(/^-?[0-9]\d*(\.\d+)?$/);
  expect(returnVal.value).toEqual(NaN);
});

it("Test setEdited updates content to true", () => {
  const content = "sensorId";
  const isEdited = { [content]: false };
  const newEdited = setEdited(isEdited, content);
  expect(isEdited[content]).toEqual(false);
  expect(newEdited[content]).toEqual(true);
});

it("Test updateDataValue updates content", () => {
  const content = "sensorId";
  const oldValue = "oldValue";
  const newValue = "newValue";
  const dataValues = { [content]: oldValue };
  const newDataValues = updateDataValue(dataValues, content, newValue);
  expect(dataValues[content]).toEqual(oldValue);
  expect(newDataValues[content]).toEqual(newValue);
});

it("Test updateDataValidation updates content", () => {
  const content = "sensorId";
  // Test a valid value
  const newValue = "newValue";
  const dataValidation = { [content]: false };
  const newDataValidation = updateDataValidation(
    dataValidation,
    content,
    newValue
  );
  expect(dataValidation[content]).toEqual(false);
  expect(newDataValidation[content]).toEqual(true);
  // Test an invalid value
  const falseValue = "this&value&should&not&work";
  const falseDataValidation = updateDataValidation(
    newDataValidation,
    content,
    falseValue
  );
  expect(dataValidation[content]).toEqual(false);
  expect(newDataValidation[content]).toEqual(true);
  expect(falseDataValidation[content]).toEqual(false);
});
