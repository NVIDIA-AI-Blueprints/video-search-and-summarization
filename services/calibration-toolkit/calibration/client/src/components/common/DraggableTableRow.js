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

import React from "react";
import { Table } from "semantic-ui-react/";

class DraggableTableRow extends React.Component {
  onDragStart = (ev, i) => {
    ev.dataTransfer.setData("index", i);
  };

  onDragOver = ev => {
    ev.preventDefault();
  };

  onDrop = (ev, a) => {
    let b = ev.dataTransfer.getData("index");
    this.props.action(parseInt(a, 10), parseInt(b, 10));
  };

  render() {
    const { i } = this.props;
    return (
      <Table.Row
        draggable
        className="draggable"
        onDragStart={e => this.onDragStart(e, i)}
        onDragOver={e => this.onDragOver(e)}
        onDrop={e => {
          this.onDrop(e, i);
        }}
      >
        {this.props.children}
      </Table.Row>
    );
  }
}

export default DraggableTableRow;