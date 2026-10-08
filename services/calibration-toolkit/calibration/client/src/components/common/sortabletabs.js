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

import React, { Component, useState } from "react";
import PropTypes from "prop-types";

import Tab from "./Tab";
import {sortableContainer, sortableElement, arrayMove} from 'react-sortable-hoc';



const SortableItem = sortableElement((data) => {
  console.log(data)
  return(
      <Tab
          activeTab={data.activeTab}
          key={data.label}
          label={data.label}
          onClick={Tabs.onClickTabItem}
      />
  )});

const SortableContainer = sortableContainer(({children}) => {
  return <ul>{children}</ul>;
});

/**
 * Displays tab interface on a page.
 */
class Tabs extends Component {
  constructor(props) {
    super(props);

    this.state = {
      tabList : this.props.children,
      activeTab: this.props.defaultTab
        ? this.props.defaultTab
        : Array.isArray(this.props.children)
        ? this.props.children[0].props.label
        : null,
      
      

    };
    this.onClickTabItem = this.onClickTabItem.bind(this)
    
  }

 
  

  onSortEnd = ({oldIndex, newIndex}) => {
    this.setState(({tabList}) => ({
      tabList: arrayMove(tabList, oldIndex, newIndex),
    }));
  };

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
    console.log(activeTab)
    return (
      <div className="tabs">
          <SortableContainer onSortEnd={this.onSortEnd}>
          {/* <ol className="tab-list"> */}

            {children.map((child,index) => {
              const { label } = child.props;
              const  data = {
                activeTab: activeTab,
                label: label,
                key: label,
                onClick: onClickTabItem
              }
              console.log("container data", data)
              return (
                <SortableItem key={`children-${child}`}  index={index} value={data} />
              );
            })}
          </SortableContainer>
          {/* </ol> */}
        <div className="tab-content">
          {children.map(child => {
            if (child.props.label !== activeTab) return undefined;
            return child.props.children;
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
