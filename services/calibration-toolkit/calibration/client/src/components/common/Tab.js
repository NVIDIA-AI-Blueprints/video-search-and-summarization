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
import PropTypes from "prop-types";

/**
 * Tab only to be used with Tabs.js as parent for creating tab interface on a
 * page.
 */
class Tab extends Component {
  /**
   * Sets tab as active on click.
   */
  onClick = () => {
    const { label, onClick } = this.props;
    onClick(label);
  };

  render() {
    const {
      onClick,
      props: { activeTab, label }
    } = this;

    let className = "tab-list-item";

    if (activeTab === label) {
      className += " tab-list-active";
    }

    return (
      <li
        className={className}
        onClick={onClick}
        id={`Tab_${label}`}
        data-testid={`Tab_${label}`}
      >
        {label}
      </li>
    );
  }
}

export default Tab;

Tab.propTypes = {
  /** The currently active tab */
  activeTab: PropTypes.string.isRequired,
  /** The label to use as the tab label */
  label: PropTypes.string.isRequired,
  /** Action to perform when clicking the tab label */
  onClick: PropTypes.func.isRequired
};
