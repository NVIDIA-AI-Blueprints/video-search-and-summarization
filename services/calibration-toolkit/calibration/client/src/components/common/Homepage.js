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
import SimpleMenuBar from "./SimpleMenubar";
import ProjectsGrid from "../projects/ProjectsGrid";
import TopMenu from "./TopMenu/TopMenu";
import MainContainer from "./MainContainer/MainContainer";

/**
 * App homepage that displays all projects and allows user to add new projects.
 * The homepage takes no props.
 */
export default class Homepage extends Component {
  render() {
    return (
      <div>
      <TopMenu>      </TopMenu>
      <MainContainer>
        <h1>Metropolis Camera Onboarding Tool</h1>
        <hr />
        <ProjectsGrid title="PROJECTS:" newButton={true} />
      </MainContainer>
    </div>
    );
  }
}
