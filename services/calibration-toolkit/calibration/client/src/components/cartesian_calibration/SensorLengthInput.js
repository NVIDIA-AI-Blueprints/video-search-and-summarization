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
  const { edgeLengths, edgeValidation, updateEdgeLengths, hasHomography } = props;
  return (
    <Table celled>
      <Table.Header>
        <Table.Row>
          <Table.HeaderCell>Vertex</Table.HeaderCell>
          <Table.HeaderCell>X Coordinate</Table.HeaderCell>
          <Table.HeaderCell>Y Coordinate</Table.HeaderCell>
        </Table.Row>
      </Table.Header>
      <Table.Body>
        {edgeLengths.map((vertex, index) => {

          const xcoord  = vertex.lng
          const ycoord = vertex.lat
          return (
            <Table.Row key={index}>
              <Table.Cell>{`${index}`}</Table.Cell>
              <Table.Cell>
                <Input
                  value={xcoord}
                  error={!edgeValidation[index]}
                  content={`${index},lng`}
                  onChange={(e, data) => {
                    updateEdgeLengths(e, data);
                  }}
                  disabled={hasHomography}
                />
              </Table.Cell>
              <Table.Cell>
                <Input
                  value={ycoord}
                  error={!edgeValidation[index]}
                  content={`${index},lat`}
                  onChange={(e, data) => {
                    updateEdgeLengths(e, data);
                  }}
                  disabled={hasHomography}

                />
              </Table.Cell>

            </Table.Row>
          );
        })}
      </Table.Body>
    </Table>
  );
}

SensorLengthInput.propTypes = {
  /** Sensor ID */
  id: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  /** Number of edges of the currently drawn polygon */
  numEdges: PropTypes.number,
  /** is Homography created */
  hasHomography: PropTypes.bool,
  // edgeLengths: PropTypes.
};
