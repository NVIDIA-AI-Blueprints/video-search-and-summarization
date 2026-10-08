/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: LicenseRef-NvidiaProprietary
 *
 * NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
 * property and proprietary rights in and to this material, related
 * documentation and any modifications thereto. Any use, reproduction,
 * disclosure or distribution of this material and related documentation
 * without an express license agreement from NVIDIA CORPORATION or
 * its affiliates is strictly prohibited.
 */


import React, { Component } from "react";
import { Link } from "react-router-dom";
import MenuBar from "../common/Menubar";

/**
 * Help page to display extra information to the user.
 */
export default class Help extends Component {
  render() {
    return (
      <MenuBar active={"help"}>
        <h1>Metropolis Camera Onboarding Help</h1>
        <hr />
        <Link to={"/help/overview"}>
          <h2>USAGE OVERVIEW</h2>
        </Link>
        <Link to={"/help/schema"}>
          <h2>JSON SCHEMA</h2>
        </Link>
      </MenuBar>
    );
  }
}
