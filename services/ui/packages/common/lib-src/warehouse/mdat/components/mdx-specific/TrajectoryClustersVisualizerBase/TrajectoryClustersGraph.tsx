// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import PropTypes from "prop-types";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  CartesianGrid,
  Cell,
  Tooltip,
} from "recharts";
import { Checkbox, FormControlLabel, Grid, Divider } from "common/warehouse/mui";

import Styles from "./TrajectoryClustersGraph.module.css";

/**
 * Graph view for Trajectory Clusters
 */
class TrajectoryClustersGraph extends React.PureComponent {
  render() {
    if (!this.props.data || this.props.data.length === 0) {
      return "No data to show as chart!";
    }

    let chartData = [];

    this.props.data &&
      this.props.data.forEach((cluster) => {
        chartData.push(cluster);
      });

    return (
      <React.Fragment>
        <ResponsiveContainer>
          <BarChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" />
            <Tooltip
              contentStyle={{
                backgroundColor: "#fff",
                color: "#1a1a1a",
                border: "1px solid #d4d4d8",
                fontSize: 12,
              }}
              labelStyle={{ color: "#1a1a1a" }}
              itemStyle={{ color: "#1a1a1a" }}
              labelFormatter={(v, n, p) => {
                if (n && n.length && n[0].payload) {
                  return (
                    <React.Fragment>
                      {"(left click bar to select/deselect)"}
                      <br />
                      <br />
                      {"Index : " + n[0].payload.clusterIndex}
                      <br />
                      {"ModelVersion : " + n[0].payload.modelVersion}
                      <br />
                      {"Label : " + (n[0].payload.label || "")}
                      <br />
                    </React.Fragment>
                  );
                }
                return v;
              }}
              cursor={{ fill: "none" }}
            />
            <Bar
              dataKey="count"
              maxBarSize={20}
              onClick={this.props.onClusterBarClick}
              onMouseOver={this.props.onClusterBarMouseOver}
              onMouseOut={this.props.onClusterBarMouseOut}
            >
              {chartData.map((entry) => (
                <Cell
                  key={entry.clusterIndex}
                  fill={entry.color}
                  stroke={
                    this.props.keyClusterFocused !== undefined &&
                    entry.clusterIndex === this.props.keyClusterFocused
                      ? "white"
                      : "none"
                  }
                  strokeWidth={
                    this.props.keyClusterFocused !== undefined &&
                    entry.clusterIndex === this.props.keyClusterFocused
                      ? 8
                      : 0
                  }
                  fillOpacity={
                    this.props.invisibleClusterIdsSet &&
                    this.props.invisibleClusterIdsSet.has(entry.clusterIndex)
                      ? 0.25
                      : 1
                  }
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </React.Fragment>
    );
  }
}

TrajectoryClustersGraph.propTypes = {
  /**
   * Trajectory Clustering Json data
   */
  data: PropTypes.array.isRequired,
  /**
   * Cluster id of currently focused cluster
   */
  keyClusterFocused: PropTypes.string,
  /**
   * Cluster ids Set of invisible clusters
   */
  invisibleClusterIdsSet: PropTypes.object,
  /**
   * @param data {object}
   */
  onClusterBarMouseOver: PropTypes.func,
  /**
   */
  onClusterBarMouseOut: PropTypes.func,
  /**
   * @param data {object}
   */
  onClusterBarClick: PropTypes.func,
};

export default TrajectoryClustersGraph;
