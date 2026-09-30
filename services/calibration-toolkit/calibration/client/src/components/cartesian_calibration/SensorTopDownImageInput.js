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
import PropTypes from "prop-types";
import { Table, Input } from "semantic-ui-react";

export default function SensorLengthInput(props) {
  const { edgeLengths, edgeValidation, updateEdgeLengths } = props;

  return (
    <Table celled>
      <Table.Header>
        <Table.Row>
          <Table.HeaderCell>Edge</Table.HeaderCell>
          <Table.HeaderCell>X Coordinate</Table.HeaderCell>
          <Table.HeaderCell>Y Coordinate</Table.HeaderCell>
        </Table.Row>
      </Table.Header>
      <Table.Body>
        <Table.Row key={0}>
          <Table.Cell>
            Width Padding
          </Table.Cell>
          <Table.Cell>
            <Input 
              value={widthPadding}
              error={!widthPaddingValidation}
              content={"widthPadding"}
              onChange={(e, data) => {
                updatePadding(e,data);
              }}
          />
          </Table.Cell>
          <Table.Cell>
            Height Padding
          </Table.Cell>
          <Table.Cell>
            <Input 
              value={heightPadding}
              error={!heightPaddingValidation}
              content={"heightPadding"}
              onChange={(e, data) => {
                updatePadding(e,data);
              }}
            />
          </Table.Cell>
        </Table.Row>
      </Table.Body>
    </Table>
  );
}

SensorTopDownImageInput.propTypes = {
  /** Sensor ID */
  id: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  /** Number of edges of the currently drawn polygon */
  numEdges: PropTypes.number
};
