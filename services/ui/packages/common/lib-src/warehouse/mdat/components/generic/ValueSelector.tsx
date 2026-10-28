// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import {
  Box,
  FormControl,
  FormHelperText,
  IconButton,
  InputLabel,
  MenuItem,
  Select,
} from "common/warehouse/mui";
import { Close as CloseIcon } from "common/warehouse/mui-icons";
import React, { useState } from "react";

const ValueSelector = (props) => {
  const [selectorOpen, setSelectorOpen] = useState(false);
  const [valueError, setValueError] = useState(false);

  const handleSelectorClose = () => {
    setSelectorOpen(false);
  };

  return (
    <FormControl key={"value-selector-form-control"} error={valueError}>
      {!props.noLabel && (
        <InputLabel key="value-selector-input-label" id={"ValueSelectorLabel"}>
          {props.label}
        </InputLabel>
      )}
      <Select
        key="value-selector-input-select"
        open={selectorOpen}
        onOpen={() => setSelectorOpen(true)}
        onClose={handleSelectorClose}
        style={{ minWidth: "100px" }}
        labelId={"ValueSelectorLabel"}
        id={"ValueSelector"}
        defaultValue={
          props.defaultValue
            ? props.defaultValue
            : props.multiple === true
              ? []
              : ""
        }
        title={`Select ${props.label}`}
        multiple={props.multiple}
        onChange={(e) => {
          setValueError(false);
          if (!e.target.value) return;
          if (props.validate) {
            if (!props.validate(e)) {
              setValueError(true);
              return;
            }
          }
          props.onChange && props.onChange(e);
        }}
      >
        {props.values?.map((value, key) => {
          return (
            <MenuItem key={`value-selector-value-${key}`} value={value}>
              {value}
            </MenuItem>
          );
        })}
        {props.multiple && (
          <Box sx={{ display: "flex", justifyContent: "center" }}>
            <IconButton
              title="Close selector list"
              onClick={handleSelectorClose}
            >
              <CloseIcon />
            </IconButton>
          </Box>
        )}
      </Select>
      {!props.noHelperText && (
        <FormHelperText key="value-selector-helper-text">
          {valueError
            ? `Error${props.errorText ? `: ${props.errorText}` : ""}`
            : `Select value ${props.multiple ? "(s)" : ""}`}
        </FormHelperText>
      )}
    </FormControl>
  );
};

export default ValueSelector;
