// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React, { Component } from "react";

import {
  UserMessage,
  GoogleMapsToolTip,
  joinUrl,
  isAbortError,
  readJson,
  connect,
  MetricsRoadNetworkSegmentSpeedUrl,
  RoadNetworkUrl,
} from "common/warehouse";

import RoadSegment from "./RoadSegment/RoadSegment";
import SpeedInfoBox from "./SpeedInfoBox/SpeedInfoBox";
import {
  filterSegmentsForView,
  intersectionSegmentIdsFromRoadNetwork,
  latLngLiteralFromMapEvent,
  mergeRoadSegments,
  seedSegmentsFromRoadNetwork,
  segmentHasLiveTraffic,
  segmentSpeedLabel,
  segmentTrafficColor,
} from "./roadNetworkTraffic";

/**
 * Layer over map that shows roads with different colors representing current traffic conditions.
 * The colors keep changing at regular intervals to depict real time traffic. This layer also contains
 * SpeedInfo boxes that can be instantiated over road links, which display the current average traffic
 * speed at a road link.
 */
class RoadNetworkOverlay extends Component {
  state = {
    roadsData: [],
    trafficLayerToolTip: null,
  };

  static SegmentsInfoMap = new Map();
  _placesProcessedSet = new Set();
  _allRoadsData = [];
  _intersectionSegmentIds = new Map();

  _visibleRoads(roads = this._allRoadsData, props = this.props) {
    return filterSegmentsForView(
      roads,
      props.uiState.viewType,
      props.uiState.selectedIntersection,
      this._intersectionSegmentIds
    );
  }

  _loadRoadNetworkConfig() {
    this._roadNetworkAbortController = new AbortController();
    fetch(String(RoadNetworkUrl), {
      signal: this._roadNetworkAbortController.signal,
    })
      .then((response) => readJson(response))
      .then((data) => {
        this._intersectionSegmentIds = intersectionSegmentIdsFromRoadNetwork(data);
        this._allRoadsData = mergeRoadSegments(
          seedSegmentsFromRoadNetwork(data),
          this._allRoadsData
        );
        this.setState({
          roadsData: this._visibleRoads(),
        });
      })
      .catch((thrown) => {
        if (isAbortError(thrown)) {
          return;
        }
        console.error(
          new UserMessage(
            thrown.config
              ? UserMessage.SRC_TYPE.HTTP_ERROR
              : UserMessage.SRC_TYPE.GENERIC,
            UserMessage.MESSAGE_LEVEL.ERROR,
            "Fetch road-network config",
            thrown
          ).getLongString()
        );
      });
  }

  updateTrafficDataPlotting(props) {
    this._stopTrafficDataPlotting();
    let fromTimestamp =
      Date.now() -
      props.appConfig.uiDelaySeconds * 1000 -
      props.appConfig.apiRefreshIntervalSeconds * 1000;
    const cityName = props.metadata?.cityConfig?.name;
    if (!cityName) {
      return;
    }
    const place = "city=" + cityName;

    const query = () => {
      let toTimestamp = Date.now() - props.appConfig.uiDelaySeconds * 1000;

      this._abortController = new AbortController();

      fetch(
        joinUrl(MetricsRoadNetworkSegmentSpeedUrl, {
          place: place,
          fromTimestamp: new Date(fromTimestamp).toISOString(),
          toTimestamp: new Date(toTimestamp).toISOString(),
          segmentInfo: !this._placesProcessedSet.has(place),
        }),
        {
          signal: this._abortController.signal,
        }
      )
        .then((response) => readJson(response))
        .then((data) => {
          const incoming = data && data.roadSegments ? data.roadSegments : [];
          if (incoming.length > 0) {
            this._allRoadsData = mergeRoadSegments(this._allRoadsData, incoming);
            this._placesProcessedSet.add(place);
            this.setState({
              roadsData: this._visibleRoads(),
            });
            return;
          }
          if (this._allRoadsData.length === 0) {
            throw Error("No roadSegments data received");
          }
        })
        .catch((thrown) => {
          if (isAbortError(thrown)) {
            return;
          }
          const purposeMsg = `Query Road segments data for place: ${place}`;

          if (thrown.config) {
            let userMessage = new UserMessage(
              UserMessage.SRC_TYPE.HTTP_ERROR,
              UserMessage.MESSAGE_LEVEL.ERROR,
              purposeMsg,
              thrown
            );
            console.error(userMessage.getLongString());
          } else {
            let userMessage = new UserMessage(
              UserMessage.SRC_TYPE.GENERIC,
              UserMessage.MESSAGE_LEVEL.ERROR,
              purposeMsg,
              thrown
            );
            console.error(userMessage.getLongString());
          }
        })
        .finally(() => {
          fromTimestamp = toTimestamp;
        });
    };

    query();
    this._queryIntervalTimerID = setInterval(
      query,
      props.appConfig.apiRefreshIntervalSeconds * 1000
    );
  }

  _stopTrafficDataPlotting = () => {
    clearInterval(this._queryIntervalTimerID);
    this._abortController && this._abortController.abort("canceled");
  };

  onClickTrafficLine = (event, segmentId) => {
    const segmentInfo = RoadNetworkOverlay.SegmentsInfoMap.get(segmentId);
    if (segmentInfo?.isActive !== true) {
      return;
    }
    const position = latLngLiteralFromMapEvent(event);
    if (!position) {
      return;
    }
    this.speedInfoBoxesDataMap.set(segmentId, {
      isClosed: false,
      position,
    });
    this.forceUpdate();
  };

  onMouseOverTrafficLine = (event, segmentId) => {
    clearInterval(this.trafficLayerToolTipTimer);

    this.trafficLayerToolTipTimer = setTimeout(() => {
      const segmentInfo = RoadNetworkOverlay.SegmentsInfoMap.get(segmentId);
      const speedInfoBoxData = this.speedInfoBoxesDataMap.get(segmentId);
      const position = latLngLiteralFromMapEvent(event);
      if (!position) {
        return;
      }

      let text;
      if (!segmentInfo || segmentInfo.isActive === false) {
        text = "Inactive: No info for the road segment yet";
      } else if (
        speedInfoBoxData !== undefined &&
        speedInfoBoxData.isClosed === false
      ) {
        text = "Click to move existing Speed Info Box here";
      } else {
        text = "Click to Spawn Speed Info Box for this Road Segment";
      }

      this.setState({
        trafficLayerToolTip: { position, text },
      });
    }, 500);
  };

  onMouseOutTrafficLine = () => {
    clearInterval(this.trafficLayerToolTipTimer);

    this.setState({ trafficLayerToolTip: null });
  };

  onClickSpeedInfoBox = (segmentId) => {
    this.isTrafficLayerTooltipDisabled = false;
    const box = this.speedInfoBoxesDataMap.get(segmentId);
    if (box) {
      box.isClosed = true;
    }
    this.forceUpdate();
  };

  onMouseEnterSpeedInfoBox = () => {
    this.isTrafficLayerTooltipDisabled = true;
  };

  onMouseLeaveSpeedInfoBox = () => {
    this.isTrafficLayerTooltipDisabled = false;
  };

  componentDidMount() {
    this.isTrafficLayerTooltipDisabled = false;
    this.speedInfoBoxesDataMap = new Map();
    this._loadRoadNetworkConfig();
    this.updateTrafficDataPlotting(this.props);
  }

  UNSAFE_componentWillReceiveProps(nextProps) {
    if (nextProps.uiState.viewType === "intersection-level-alert") {
      this._stopTrafficDataPlotting();
      return;
    }

    if (this.props.uiState.viewType === "intersection-level-alert") {
      this.isTrafficLayerTooltipDisabled = false;
      this.updateTrafficDataPlotting(nextProps);
    }

    if (
      nextProps.uiState.viewType !== this.props.uiState.viewType ||
      nextProps.uiState.selectedIntersection !==
        this.props.uiState.selectedIntersection
    ) {
      this.setState({
        roadsData: this._visibleRoads(this._allRoadsData, nextProps),
      });
    }
  }

  componentWillUnmount() {
    this._stopTrafficDataPlotting();
    this._roadNetworkAbortController &&
      this._roadNetworkAbortController.abort("canceled");
  }

  _initializeSegmentInfo(segment) {
    if (!segment?.id || !Array.isArray(segment.points) || segment.points.length < 2) {
      return;
    }
    if (!RoadNetworkOverlay.SegmentsInfoMap.has(segment.id)) {
      RoadNetworkOverlay.SegmentsInfoMap.set(segment.id, {
        locations: segment.points,
        isActive: false,
      });
      return;
    }
    const stored = RoadNetworkOverlay.SegmentsInfoMap.get(segment.id);
    if (!stored.locations || stored.locations.length < 2) {
      stored.locations = segment.points;
    }
  }

  _processSegment(segment, speedInfoBoxes, roadSegments) {
    this._initializeSegmentInfo(segment);
    const segmentInfo = RoadNetworkOverlay.SegmentsInfoMap.get(segment.id);
    if (segmentHasLiveTraffic(segment) && segmentInfo) {
      segmentInfo.isActive = true;
    }

    const speedInfoBoxData = this.speedInfoBoxesDataMap.get(segment.id);
    if (
      speedInfoBoxData !== undefined &&
      speedInfoBoxData.isClosed === false &&
      segmentInfo &&
      segmentInfo.isActive === true
    ) {
      speedInfoBoxes.push(
        <SpeedInfoBox
          key={segment.id}
          id={segment.id}
          speed={segmentSpeedLabel(segment, segmentInfo.isActive)}
          position={speedInfoBoxData.position}
          onClick={this.onClickSpeedInfoBox}
          onMouseEnter={this.onMouseEnterSpeedInfoBox}
          onMouseLeave={this.onMouseLeaveSpeedInfoBox}
        />
      );
    }

    if (segmentInfo?.locations?.length >= 2) {
      roadSegments.push(
        <RoadSegment
          key={segment.id}
          id={segment.id}
          segmentLocations={segmentInfo.locations}
          segmentColor={segmentTrafficColor(segment, segmentInfo.isActive)}
          segmentStrokeWeight={this.props.lineStrokeWeight}
          onClick={this.onClickTrafficLine}
          onMouseOver={this.onMouseOverTrafficLine}
          onMouseOut={this.onMouseOutTrafficLine}
        />
      );
    }
  }

  render() {
    if (this.props.uiState.viewType === "intersection-level-alert") {
      return null;
    }

    let speedInfoBoxes = [];
    let roadSegments = [];

    this.state.roadsData.forEach((segment) => {
      this._processSegment(segment, speedInfoBoxes, roadSegments);
    });

    let trafficLayerToolTipOverlay;
    if (
      this.state.trafficLayerToolTip !== null &&
      this.isTrafficLayerTooltipDisabled === false
    ) {
      trafficLayerToolTipOverlay = (
        <GoogleMapsToolTip
          position={this.state.trafficLayerToolTip.position}
          text={this.state.trafficLayerToolTip.text}
        />
      );
    }

    return (
      <React.Fragment>
        {roadSegments}
        {speedInfoBoxes}
        {trafficLayerToolTipOverlay}
      </React.Fragment>
    );
  }
}

let myConnect = connect((state) => ({
  appConfig: state.appConfig,
  uiState: state.uiState,
  metadata: state.metadata,
}));

export default myConnect(RoadNetworkOverlay);
