// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
const ReduxUtils = {
  combineDispatchToProps: function () {
    let result = {};
    for (let i = 0; i < arguments.length; i++) {
      Object.assign(result, arguments[i]);
    }
    return result;
  },
};

export default ReduxUtils;
