// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { Axios } from "common/warehouse";
import { CircularProgress } from "common/warehouse/mui";

import NewValueApplier from "./../../generic/NewValueApplier";

import UserMessage from "./../../../utils/UserMessage";

import Styles from "./ClusterLabelUpdatePanel.module.css";

export default class ClusterLabelUpdatePanel extends React.PureComponent {
  state = {
    lastActionStatus: "",
  };

  onNewClusterLabelSelected = (label) => {
    this.setState({ lastActionStatus: "working" });

    Axios({
      url: this.props.clusterLabelUpdateEndpoint,
      method: "POST",
      data: {
        sensorId: this.props.sensorConfig.id,
        modelVersion: this.props.clusterData.modelVersion,
        clusterIndex: this.props.clusterData.clusterIndex,
        label: label,
      },
      cancelToken: this.clusterLabelUpdateCancelSource.token,
    })
      .then((res) => {
        this.setState({
          lastActionStatus: "Success",
        });
        this.onSuccessTimerId = setTimeout(this.props.onSuccess, 2000);
      })
      .catch((thrown) => {
        let userMessage = new UserMessage(
          UserMessage.SRC_TYPE.AXIOS_ERROR,
          UserMessage.MESSAGE_LEVEL.ERROR,
          "Update Cluster label for sensor id: " + this.props.sensorConfig.id,
          thrown
        );

        if (Axios.isCancel(thrown)) {
          console.log(userMessage.getLongString());
        } else {
          console.error(userMessage.getLongString());
          this.setState({
            lastActionStatus: userMessage.getShortString(),
          });
        }
      });
  };

  componentDidMount() {
    this.clusterLabelUpdateCancelSource = Axios.CancelToken.source();
  }

  render() {
    let statusEl;

    if (this.state.lastActionStatus !== "") {
      if (this.state.lastActionStatus === "working") {
        statusEl = <CircularProgress />;
      } else {
        statusEl = "Last Action Status: " + this.state.lastActionStatus;
      }
    }

    return (
      <React.Fragment>
        <NewValueApplier
          label="New Cluster Label"
          onNewValueSelected={this.onNewClusterLabelSelected.bind(this)}
          onClose={this.props.onClose}
          defaultValue={this.props.clusterData.label || ""}
          infoString={
            "Index: " +
            this.props.clusterData.clusterIndex +
            ". Current Label: '" +
            (this.props.clusterData.label || "") +
            "'"
          }
          validateValue={(v) => {
            let currentValue = this.props.clusterData.label || "";
            if (v !== currentValue) return true;
            return "Enter a different label than the current one";
          }}
        />
        <div className={Styles.statusRow}>{statusEl}</div>
      </React.Fragment>
    );
  }

  componentWillUnmount() {
    this.clusterLabelUpdateCancelSource &&
      this.clusterLabelUpdateCancelSource.cancel(
        "Canceling label update action on close"
      );
    clearTimeout(this.onSuccessTimerId);
  }
}
