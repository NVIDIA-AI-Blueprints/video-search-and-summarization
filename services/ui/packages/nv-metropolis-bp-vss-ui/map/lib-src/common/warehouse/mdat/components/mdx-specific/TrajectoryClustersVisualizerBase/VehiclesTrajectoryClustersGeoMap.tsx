// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { Polyline } from "@react-google-maps/api";

import Map from "./../../generic/Map";
import GoogleMapsToolTip from "./../../generic/GoogleMapsToolTip";

export default class VehiclesTrajectoryClustersGeoMap extends React.PureComponent {
  state = {
    hoveredClusterToolTipLatLng: undefined,
  };

  onMouseOverTrajectory = (e, cluster) => {
    this.hoveredCluster = cluster;
    this.setState({
      hoveredClusterToolTipLatLng: e.latLng,
    });
    this.props.onMouseOverTrajectoryCluster(e, cluster.clusterIndex);
  };

  onMouseOutTrajectory = (e) => {
    this.hoveredCluster = undefined;
    this.setState({
      hoveredClusterToolTipLatLng: undefined,
    });
    this.props.onMouseOutTrajectoryCluster(e);
  };

  render() {
    let polylineElements = [];
    let directionArrowElements = [];
    let clusterToolTip;

    this.props.data &&
      this.props.data.forEach((cluster) => {
        // Dont show clusters that are currently not required
        if (
          (this.props.keyClusterFocused !== null &&
            cluster.clusterIndex !== this.props.keyClusterFocused) ||
          this.props.invisibleClusterIdsSet.has(cluster.clusterIndex)
        ) {
          return;
        }

        cluster.sampledBehaviors.forEach(
          (trajectoryBehavior, trajectoryArrayIndex) => {
            let path = [];
            let directionArrowLinePath = [];
            trajectoryBehavior.locations.coordinates.forEach(
              (pair, pairArrayIndex) => {
                path.push({
                  lat: pair[1],
                  lng: pair[0],
                });

                if (trajectoryArrayIndex % 10 === 0) {
                  if (
                    pairArrayIndex === 0 ||
                    pairArrayIndex ===
                      trajectoryBehavior.locations.coordinates.length - 1
                  ) {
                    directionArrowLinePath.push({
                      lat: pair[1],
                      lng: pair[0],
                    });
                  }
                }
              }
            );

            polylineElements.push(
              <Polyline
                key={"" + cluster.clusterIndex + trajectoryBehavior.Id}
                path={path}
                options={{
                  strokeColor: cluster.color,
                  // keep original trajectories below the direction arrow ones
                  zIndex: 1,
                }}
                onMouseOver={(e) => this.onMouseOverTrajectory(e, cluster)}
                onMouseOut={(e) => this.onMouseOutTrajectory(e)}
                // stop event propagation to avoid zoom in on double click on trajectory
                onDblClick={(e) => {
                  e.stop();
                  this.props.onClusterLabelUpdateRequest(cluster);
                }}
              />
            );

            if (
              trajectoryArrayIndex % 10 === 0 &&
              cluster.clusterIndex === this.props.keyClusterFocused
            ) {
              directionArrowElements.push(
                <Polyline
                  key={
                    "directionArrow" +
                    cluster.clusterIndex +
                    trajectoryBehavior.Id
                  }
                  path={directionArrowLinePath}
                  options={{
                    strokeOpacity: 0,
                    // keep original trajectories below the direction arrow ones
                    zIndex: 2,
                    icons: [
                      {
                        icon: {
                          path:
                            window.google &&
                            window.google.maps.SymbolPath.FORWARD_OPEN_ARROW,
                          strokeColor: "white",
                          strokeOpacity: 1,
                          scale: 5,
                        },
                        offset: "50%",
                      },
                    ],
                  }}
                />
              );
            }
          }
        );
      });

    if (this.hoveredCluster && this.state.hoveredClusterToolTipLatLng) {
      clusterToolTip = (
        <GoogleMapsToolTip
          position={this.state.hoveredClusterToolTipLatLng}
          text={
            <div>
              {"(Double Click to update label for the cluster)"}
              <br />
              <br />
              {"Cluster Index: " + this.hoveredCluster.clusterIndex}
              <br />
              {"Cluster Label: " + (this.hoveredCluster.label || "")}
              <br />
              {"Cluster Model Version: " + this.hoveredCluster.modelVersion}
              <br />
              {"Trajectories Count: " + this.hoveredCluster.count}
            </div>
          }
        />
      );
    }

    return (
      <Map
        apiKey={this.props.mapsApiKey}
        mapContainerStyle={{ height: "100%", flex: 1 }}
        mapTypeId={(this.map && this.map.getMapTypeId()) || "satellite"}
        zoom={this.props.mapOptions.defaultZoom || 19}
        center={this.props.sensorConfig.geoLocation}
        tilt={0}
        options={{
          minZoom: this.props.mapOptions.minZoom || 18,
          maxZoom: this.props.mapOptions.maxZoom || 21,
          mapTypeId: (this.map && this.map.getMapTypeId()) || "satellite",
          rotateControl: true,
          rotateControlOptions: {
            position:
              window.google && window.google.maps.ControlPosition.RIGHT_BOTTOM,
          },
          mapTypeControl: true,
          mapTypeControlOptions: {
            style:
              window.google && window.google.maps.MapTypeControlStyle.DEFAULT,
            position:
              window.google && window.google.maps.ControlPosition.TOP_RIGHT,
          },
        }}
        onLoad={(map) => (this.map = map)}
      >
        {polylineElements}
        {directionArrowElements}
        {clusterToolTip}
      </Map>
    );
  }
}
