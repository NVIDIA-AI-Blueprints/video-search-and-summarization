// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
const envStore = {};

const env = jest.fn((key) => envStore[key] ?? undefined);

const setMockEnv = (key, value) => {
  envStore[key] = value;
};

const clearMockEnv = () => {
  Object.keys(envStore).forEach((key) => {
    delete envStore[key];
  });
};

module.exports = { env, setMockEnv, clearMockEnv };
