// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

var prodBaseUrlPath = window.location.pathname;
// if path was empty, don't prepend anything to the new paths
if (prodBaseUrlPath === '/') {
  prodBaseUrlPath = '';
} else if (prodBaseUrlPath.includes("/calibration")) {
  prodBaseUrlPath = window.location.pathname.split("/calibration")[0] + "/calibration"
}

export const prodUrl = prodBaseUrlPath