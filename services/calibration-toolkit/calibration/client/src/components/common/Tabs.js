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
import DOMPurify from "dompurify";
import Tab from "./Tab";

/**
 * Displays tab interface on a page.
 */
class Tabs extends Component {
  constructor(props) {
    super(props);

    this.state = {
      activeTab: this.props.defaultTab
        ? this.props.defaultTab
        : Array.isArray(this.props.children)
        ? this.props.children[0].props.label
        : null
    };
  }

  /**
   * Sets the clicked tab as active.
   */
  onClickTabItem = tab => {
    this.setState({ activeTab: tab });
  };

  render() {
    const {
      onClickTabItem,
      props: { children },
      state: { activeTab }
    } = this;
    if (!activeTab) {
      return <h2>Must provide at least two tabs</h2>;
    }

    return (
      <div className="tabs">
        <ol className="tab-list">
          {children.map(child => {
            const { label } = child.props;

            return (
              <Tab
                activeTab={activeTab}
                key={label}
                label={label}
                onClick={onClickTabItem}
              />
            );
          })}
        </ol>
        <div className="tab-content">
          {children.map(child => {
            if (child.props.label !== activeTab) return undefined;
            return (child.props.children);
          })}
        </div>
      </div>
    );
  }
}

export default Tabs;

Tabs.propTypes = {
  /** Array of tab children to use in the overal tabs list */
  children: PropTypes.instanceOf(Array).isRequired
};
