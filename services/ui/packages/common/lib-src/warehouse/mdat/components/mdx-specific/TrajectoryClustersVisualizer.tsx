// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import PropTypes from "prop-types";
import {
  Close as CloseIcon,
  Warning as WarningIcon,
} from "common/warehouse/mui-icons";
import { Paper } from "common/warehouse/mui";
import { joinUrl, isAbortError, readJson } from "common/warehouse";

import StartEndDateTimeSelector from "./../generic/StartEndDateTimeSelector";
import NewValueApplier from "./../generic/NewValueApplier";
import Menu from "./../generic/Menu";
import AboutModal from "./../generic/AboutModal";
import LoaderDisplay from "./../generic/LoaderDisplay/LoaderDisplay";
import WithLoaderDisplay from "./../generic/Hoc/WithLoaderDisplay";

import TrajectoryClustersPlot from "./TrajectoryClustersVisualizerBase/TrajectoryClustersPlot";
import ClusterLabelUpdatePanel from "./TrajectoryClustersVisualizerBase/ClusterLabelUpdatePanel";

import ColorSet from "./../../utils/32ColorSet";
import UserMessage from "./../../utils/UserMessage";
import { BeautifyNameString } from "./../../utils/Utils";

import Styles from "./TrajectoryClustersVisualizer.module.css";

const MenuItemsLabels = {
  setTimeRange: "Set Date-Time Range",
  setMaxTrajectoriesCount: "Set Max Trajectories Per Cluster",
  setMinBehaviorDistance: "Set Min Trajectory length",
  about: "About/Help",
};

const AboutHtml = (
  <React.Fragment>
    <p>
      Shows trajectory clusters on the floor plan together with a count chart.
    </p>
    <h3>Defaults</h3>
    <ul>
      <li>Uses the past 1 hour of data.</li>
      <li>Draws up to 100 sample trajectories per cluster.</li>
    </ul>
    <h3>How to use</h3>
    <ul>
      <li>
        <strong>Time range</strong> — {MenuItemsLabels.setTimeRange} in the
        top-left menu.
      </li>
      <li>
        <strong>Sample size</strong> — {MenuItemsLabels.setMaxTrajectoriesCount}{" "}
        in the top-left menu. This is the maximum number of trajectories drawn
        for the largest cluster. Only a subset of a cluster is shown on the map.
      </li>
      <li>
        <strong>Minimum length</strong> — {MenuItemsLabels.setMinBehaviorDistance}{" "}
        in the top-left menu. Trajectories shorter than this (in meters) are
        ignored.
      </li>
      <li>
        <strong>Show or hide a cluster</strong> — click its bar on the chart.
        Hover a cluster on the map to highlight the matching bar.
      </li>
      <li>
        <strong>Rename a cluster</strong> — double-click it on the map.
      </li>
    </ul>
  </React.Fragment>
);

let TrajectoryClustersPlotWithLoaderDisplay = WithLoaderDisplay(
  TrajectoryClustersPlot
);

/**
 * UI Component to present Trajectory Cluster Data for a sensor.
 *
 * Displays trajectory clusters over a base map (geo or floor) with optional
 * graphs representation upon selection of a time range.
 */
export default class TrajectoryClustersVisualizer extends React.Component {
  constructor(props) {
    super(props);

    this.state = {
      showHelp: false,
      showTimeRangeSelector: false,
      showMaxTrajCountSelector: false,
      showMinBehaviorDistanceSelector: false,
      presentationData: null,
    };

    this.endTime = new Date();
    this.startTime = new Date(this.endTime);
    this.startTime.setHours(this.endTime.getHours() - 1); // default before 1 hr
    this.maxTrajCount = 100; // default
    if (props.type === "vehicles") {
      this.minBehaviorDistance = 30;
    } else {
      // people visualizer
      this.minBehaviorDistance = 1;
    }

    this.loaderDisplayOptions = {
      loaderDisplayType: LoaderDisplay.TYPE.LOADING,
      loaderDisplayUserMessage: new UserMessage(
        UserMessage.SRC_TYPE.GENERIC,
        UserMessage.MESSAGE_LEVEL.INFO,
        "GET Trajectory Clustering data for sensor id: " +
          this.props.sensorConfig.id,
        "Loading new data"
      ),
      loaderDisplaySize: LoaderDisplay.SIZE.MEDIUM,
    };
  }

  queryData = (start, end, count, minBehaviorDistance) => {
    this.state.presentationData !== null &&
      this.setState({ presentationData: null });
    this.loaderDisplayOptions = {
      loaderDisplayType: LoaderDisplay.TYPE.LOADING,
      loaderDisplayUserMessage: new UserMessage(
        UserMessage.SRC_TYPE.GENERIC,
        UserMessage.MESSAGE_LEVEL.INFO,
        "GET Trajectory Clustering data for sensor id: " +
          this.props.sensorConfig.id,
        "Loading new data"
      ),
      loaderDisplaySize: LoaderDisplay.SIZE.MEDIUM,
    };

    fetch(joinUrl(this.props.clusteringBehaviorEndpoint, {
        sensorId: this.props.sensorConfig.id,
        maxClusterSampleSize: count,
        fromTimestamp: start.toISOString(),
        toTimestamp: end.toISOString(),
        minBehaviorDistance: minBehaviorDistance,
      }), {
      signal: this.abortController?.signal,
    })
      .then((response) => readJson(response))
      .then((data) => {
        this.loaderDisplayOptions = {
          loaderDisplayType: LoaderDisplay.TYPE.LOAD_SUCCESS,
          loaderDisplayUserMessage: new UserMessage(
            UserMessage.SRC_TYPE.GENERIC,
            UserMessage.MESSAGE_LEVEL.INFO,
            "GET Trajectory Clustering data for sensor id: " +
              this.props.sensorConfig.id,
            "Loaded new data"
          ),
          loaderDisplaySize: LoaderDisplay.SIZE.MEDIUM,
          loaderAfterSuccessDelayMs: 100,
        };
        this.startTime = start;
        this.endTime = end;
        this.maxTrajCount = count;
        this.minBehaviorDistance = minBehaviorDistance;

        let maxActualCount = -1;
        // Append color for UI purposes
        data.clusters.forEach((cluster, index) => {
          cluster.color = ColorSet[index % ColorSet.length];
          maxActualCount = Math.max(maxActualCount, cluster.count);
        });
        // Show proportional number of trajectories on the map views based on the actual
        // total count of trajectories in clusters. To do so, process the received data
        // here beforehand.
        //
        // Query returns with max 100 trajectories per cluster. Keep 100 trajectories for
        // the cluster with highest actual count value. And then only keep subset of
        // proportional trajectories for the other clusters. Set a min 5 trajectory count
        // for the clusters having more than 5 trajectories
        data.clusters.forEach((cluster) => {
          let presentationTrajCount = Math.floor(
            ((1.0 * cluster.count) / maxActualCount) * 100
          );
          if (presentationTrajCount < 5) presentationTrajCount = 5;
          if (presentationTrajCount < cluster.sampledBehaviors.length) {
            cluster.sampledBehaviors = cluster.sampledBehaviors.slice(
              0,
              presentationTrajCount
            );
          }
        });

        this.setState({ presentationData: data.clusters });
      })
      .catch((thrown) => {
        let userMessage = new UserMessage(
          UserMessage.SRC_TYPE.HTTP_ERROR,
          UserMessage.MESSAGE_LEVEL.ERROR,
          "GET Trajectory Clustering data for sensor id: " +
            this.props.sensorConfig.id,
          thrown
        );

        if (isAbortError(thrown)) {
          console.log(userMessage.getLongString());
        } else {
          console.error(userMessage.getLongString());
          this.loaderDisplayOptions = {
            loaderDisplayType: LoaderDisplay.TYPE.LOAD_FAILURE,
            loaderDisplayUserMessage: userMessage,
            loaderDisplaySize: LoaderDisplay.SIZE.MEDIUM,
          };
          this.setState({ presentationData: null });
        }
      });
  };

  componentDidMount() {
    this.abortController = new AbortController();
    this.queryData(
      this.startTime,
      this.endTime,
      this.maxTrajCount,
      this.minBehaviorDistance
    );
  }

  menuItems = [
    {
      label: MenuItemsLabels.setTimeRange,
      onClick: (e) => this.setState({ showTimeRangeSelector: true }),
    },
    {
      label: MenuItemsLabels.setMaxTrajectoriesCount,
      onClick: (e) => this.setState({ showMaxTrajCountSelector: true }),
    },
    {
      label: MenuItemsLabels.setMinBehaviorDistance,
      onClick: (e) => this.setState({ showMinBehaviorDistanceSelector: true }),
    },
    {
      label: MenuItemsLabels.about,
      onClick: (e) => this.setState({ showHelp: true }),
    },
  ];

  onTimeRangeSelected = (start, end) => {
    this.setState({
      showTimeRangeSelector: false,
    });
    this.queryData(start, end, this.maxTrajCount, this.minBehaviorDistance);
    this.props.onTimeRangeChanged && this.props.onTimeRangeChanged(start, end);
  };

  onCloseHelp = (mouseevent) => {
    this.setState({ showHelp: false });
  };

  onTimeRangeSelectorClosed = () => {
    this.setState({
      showTimeRangeSelector: false,
    });
  };

  onCountSelected = (count) => {
    this.setState({
      showMaxTrajCountSelector: false,
    });
    this.trajCount = count;
    this.queryData(
      this.startTime,
      this.endTime,
      count,
      this.minBehaviorDistance
    );
    this.props.onMaxTrajCountChanged && this.props.onMaxTrajCountChanged(count);
  };

  onMinBehaviorDistanceSelected = (minBehaviorDistance) => {
    this.setState({
      showMinBehaviorDistanceSelector: false,
    });
    this.minBehaviorDistance = minBehaviorDistance;
    this.queryData(
      this.startTime,
      this.endTime,
      this.maxTrajCount,
      minBehaviorDistance
    );
    this.props.onMinBehaviorDistanceChanged &&
      this.props.onMinBehaviorDistanceChanged(minBehaviorDistance);
  };

  onClusterLabelUpdateRequest = (clusterData) => {
    this.setState({ selectedClusterForLabelUpdate: clusterData });
  };

  onNewClusterLabelUpdateSuccess = () => {
    this.loaderDisplayOptions = {
      loaderDisplayType: LoaderDisplay.TYPE.LOADING,
      loaderDisplayUserMessage: new UserMessage(
        UserMessage.SRC_TYPE.GENERIC,
        UserMessage.MESSAGE_LEVEL.INFO,
        "GET Trajectory Clustering data for sensor id: " +
          this.props.sensorConfig.id,
        "Loading new data"
      ),
      loaderDisplaySize: LoaderDisplay.SIZE.MEDIUM,
    };
    this.setState({
      selectedClusterForLabelUpdate: undefined,
      presentationData: null,
    });
    this.queryData(
      this.startTime,
      this.endTime,
      this.maxTrajCount,
      this.minBehaviorDistance
    );
  };

  onCountSelectorClosed = () => {
    this.setState({
      showMaxTrajCountSelector: false,
    });
  };

  onMinBehaviorDistanceSelectorClosed = () => {
    this.setState({
      showMinBehaviorDistanceSelector: false,
    });
  };

  onCloseClusterLabelUpdatePanel = () => {
    this.setState({ selectedClusterForLabelUpdate: undefined });
  };

  render() {
    if (this.props.type !== "vehicles" && this.props.type !== "people") {
      return (
        "Unable to launch Trajectory Cluster View for type: " + this.props.type
      );
    }

    let timeRangeSelector = this.state.showTimeRangeSelector ? (
      <Paper className={Styles.selectorContainer} elevation={2} style={{ backgroundColor: "#fff", color: "#1a1a1a" }}>
        <StartEndDateTimeSelector
          disableFuture={true}
          defaultStartDateTime={this.startTime}
          defaultEndDateTime={this.endTime}
          onTimeRangeSelected={this.onTimeRangeSelected.bind(this)}
          onClose={this.onTimeRangeSelectorClosed.bind(this)}
        />
      </Paper>
    ) : null;

    let maxTrajCountSelector = this.state.showMaxTrajCountSelector ? (
      <Paper className={Styles.selectorContainer} elevation={2} style={{ backgroundColor: "#fff", color: "#1a1a1a" }}>
        <NewValueApplier
          label="Max Trajectories per Cluster"
          onNewValueSelected={this.onCountSelected.bind(this)}
          onClose={this.onCountSelectorClosed.bind(this)}
          defaultValue={this.maxTrajCount}
          infoString="integer >= 1 && <= 100"
          validateValue={(c) => {
            const numericalRegex = /^[0-9]+$/;
            const count = parseFloat(c);
            if (
              numericalRegex.test(c) &&
              Number.isInteger(count) &&
              count >= 1 &&
              count <= 100
            ) {
              return true;
            }
            return "Integer Value must be within range";
          }}
        />
      </Paper>
    ) : null;

    let minBehaviorDistanceSelector = this.state
      .showMinBehaviorDistanceSelector ? (
      <Paper className={Styles.selectorContainer} elevation={2} style={{ backgroundColor: "#fff", color: "#1a1a1a" }}>
        <NewValueApplier
          label="Min Trajectory length (meter)"
          onNewValueSelected={this.onMinBehaviorDistanceSelected.bind(this)}
          onClose={this.onMinBehaviorDistanceSelectorClosed.bind(this)}
          defaultValue={this.minBehaviorDistance}
          infoString="length >= 0 && Integer"
          validateValue={(c) => {
            const count = parseFloat(c);
            if (count >= 0 && Number.isInteger(Number(count))) return true;
            return "Value doesn't meet criteria";
          }}
        />
      </Paper>
    ) : null;

    let clusterLabelUpdatePanel = this.state.selectedClusterForLabelUpdate ? (
      <Paper className={Styles.clusterLabelUpdatePanelContainer} elevation={2} style={{ backgroundColor: "#fff", color: "#1a1a1a" }}>
        <ClusterLabelUpdatePanel
          onSuccess={this.onNewClusterLabelUpdateSuccess.bind(this)}
          onClose={this.onCloseClusterLabelUpdatePanel.bind(this)}
          sensorConfig={this.props.sensorConfig}
          clusterData={this.state.selectedClusterForLabelUpdate}
          clusterLabelUpdateEndpoint={this.props.clusterLabelUpdateEndpoint}
        />
      </Paper>
    ) : null;

    let closeBtn = this.props.onClose ? (
      <CloseIcon
        className={Styles.closeBtn}
        title="Close"
        onClick={() => this.props.onClose()}
      />
    ) : null;

    let helpEl = this.state.showHelp && (
      <React.Fragment>
        <div
          className={Styles.helpBackdrop}
          onClick={this.onCloseHelp.bind(this)}
        />
        <div className={Styles.helpElementContainer}>
          <AboutModal
            heading="Trajectories Clustering View"
            onClose={this.onCloseHelp.bind(this)}
          >
            {AboutHtml}
          </AboutModal>
        </div>
      </React.Fragment>
    );

    let mainContent = (
      <div className={Styles.mainContentContainer}>
        <TrajectoryClustersPlotWithLoaderDisplay
          data={this.state.presentationData}
          onClusterLabelUpdateRequest={this.onClusterLabelUpdateRequest.bind(
            this
          )}
          {...this.props}
          {...this.loaderDisplayOptions}
        />
      </div>
    );

    let infoOverlayEl = this.state.presentationData !== null && (
      <div className={Styles.infoOverlayContainer}>
        <Paper
          className={Styles.infoOverlayItem}
          elevation={2}
          // TODO: not working in css class
          style={{ backgroundColor: "#edfcd2" }}
        >
          <b>
            {" " +
              this.startTime.toLocaleString() +
              " -- " +
              this.endTime.toLocaleString() +
              ". "}
          </b>
          Max
          <b>{" " + this.maxTrajCount + " "}</b>
          sample trajectories per cluster. Min Trajectory length
          <b>{" " + this.minBehaviorDistance + " m"}</b>.
        </Paper>
        {this.state.presentationData !== null &&
        this.state.presentationData.length > ColorSet.length ? (
          <Paper
            className={Styles.infoOverlayItem}
            elevation={2}
            // TODO: not working in css class
            style={{ backgroundColor: "#e69302" }}
          >
            <WarningIcon className={Styles.warningIcon} />
            If more than {ColorSet.length} clusters, repeated colors may show!
          </Paper>
        ) : null}
        {this.state.presentationData !== null &&
        this.state.presentationData.length === 0 ? (
          <Paper
            className={Styles.infoOverlayItem}
            elevation={2}
            // TODO: not working in css class
            style={{ backgroundColor: "#e69302" }}
          >
            <WarningIcon className={Styles.warningIcon} />
            No Clusters to be shown!
          </Paper>
        ) : null}
      </div>
    );

    return (
      <React.Fragment>
        <span className={Styles.headerBar}>
          <span className={Styles.menuBtn}>
            <Menu items={this.menuItems} />
          </span>
          <span className={Styles.headerTitle}>
            {"Clustering Behavior: " +
              BeautifyNameString(this.props.sensorConfig.id)}
          </span>
          {closeBtn}
        </span>
        {timeRangeSelector}
        {maxTrajCountSelector}
        {minBehaviorDistanceSelector}
        {infoOverlayEl}
        {mainContent}
        {helpEl}
        {clusterLabelUpdatePanel}
      </React.Fragment>
    );
  }

  componentWillUnmount() {
    this.abortController &&
      this.abortController.abort(
        "Canceling queries for Trajectories clustering"
      );
  }
}

TrajectoryClustersVisualizer.propTypes = {
  /**
   * Sensor Config JSON
   */
  sensorConfig: PropTypes.object.isRequired,
  /**
   * Type of base map.
   *
   * vehicles: GeoMap
   * people: Floor Top View Image
   */
  type: PropTypes.oneOf(["vehicles", "people"]).isRequired,
  /**
   * Callback on time range selection.
   *
   * @param startDateTime {Date} Date Object
   * @param endDateTime {Date} Date Object
   */
  onTimeRangeChanged: PropTypes.func,
  /**
   * Callback on Max trajectories per cluster count changed.
   *
   * @param count {number}
   */
  onMaxTrajCountChanged: PropTypes.func,
  /**
   * Callback on Min Behavior Distance changed.
   *
   * @param minBehaviorDistance {number}
   */
  onMinBehaviorDistanceChanged: PropTypes.func,
  /**
   * Callback on close button of widget.
   *
   * If provided, a clickable close button appears in the header.
   */
  onClose: PropTypes.func,
  /**
   * Maps Api Key if GeoMap in use
   */
  mapsApiKey: PropTypes.string,
  /**
   * Maps Options of Geo Map
   */
  mapOptions: PropTypes.object,
  /**
   * People Floor Plan Config Options
   */
  floorPlanViewConfig: PropTypes.object,
  /**
   * REST Endpoint for data.
   */
  clusteringBehaviorEndpoint: PropTypes.string.isRequired,
  /**
   * REST Endpoint for adding cluster labels.
   */
  clusterLabelUpdateEndpoint: PropTypes.string.isRequired,
  /**
   * Global Meters:Pixel Ratio for top view floor map cartesian coordinates plotting.
   */
  globalM2PxScaleFactor: PropTypes.number,
  /**
   * Canvas contents render update rate (frames per second). Pass through from UI config cartesianMapVizContentsRenderUpdateFps (default 2).
   */
  mapVizContentsRenderUpdateFps: PropTypes.number,
};
