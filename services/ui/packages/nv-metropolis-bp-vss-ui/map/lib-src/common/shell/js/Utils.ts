// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { getEnv, vstApiV1Base } from "./EnvUtils";

const Utils = {
  getMediaServiceBaseUrl: () =>
    vstApiV1Base(getEnv("REACT_APP_MEDIA_STREAMING_URL_BASE")),
};

export default Utils;
