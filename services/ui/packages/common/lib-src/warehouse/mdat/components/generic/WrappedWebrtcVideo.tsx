// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";

import WebrtcVideo from "./WebrtcVideo/WebrtcVideo";

export default class WrappedWebrtcVideo extends React.PureComponent {
  render() {
    return <WebrtcVideo {...this.props} />;
  }
}
