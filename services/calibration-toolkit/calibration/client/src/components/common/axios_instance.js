// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import axios from 'axios';

const ENDPOINT = process.env.REACT_APP_API_ENDPOINT_BASE_URL;

let url = ""
if (window.location.origin === "http://localhost:3000") {
  //dev localhost
  axios.defaults.baseURL = ENDPOINT;
  url = ENDPOINT
} else if (window.location.href.includes("/calibration")) {
  //hosted
  console.log("window", window.location.href, window.location.pathname)
  console.log("window", window.location.pathname.split("/")[1], window.location.pathname.split("/calibration")[0])
  axios.defaults.baseURL = window.location.origin + window.location.pathname.split("/calibration")[0] + "/calibration";
  console.log("baseurl", axios.defaults.baseURL)
  url = window.location.origin + window.location.pathname.split("/calibration")[0] +  "/calibration/api"
} else {
  //local deployment at 8003
  axios.defaults.baseURL = window.location.origin;
  url = window.location.origin + "/api"

}

console.log("axios_instance", url)
export const API_ENDPOINT = url
