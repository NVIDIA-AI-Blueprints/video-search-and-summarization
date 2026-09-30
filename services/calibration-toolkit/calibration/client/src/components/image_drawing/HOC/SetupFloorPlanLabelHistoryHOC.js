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


import React, { Component } from 'react';
import update from 'immutability-helper';
import { flipYLabels } from '../../common/mathUtils';

export function withSetupFloorPlanImageHistory(Comp) {
  return class HistoryLayer extends Component {
    constructor(props) {
      super(props);

      const { mapLabelData, mapLabels, drawLabels } = props;
      let mapShapes = {};
      console.log("fphoc1", mapLabelData, mapLabels, this.props.height)

      mapLabels.map(label => (mapShapes[label.id] = []));
      mapShapes.__temp = [];
      Object.keys(mapLabelData).forEach(key => {
        mapShapes[key] = (mapShapes[key] || []).concat(mapLabelData[key]);
      });
      console.log ("fphoc2", mapShapes )
      mapShapes = flipYLabels(mapShapes, drawLabels);
      this.state = {
        mapShapes, // mapping from label name to a list of Figure structures
        unfinishedMapFigure: null,
        mapFiguresHistory: [],
        mapUnfinishedFigureHistory: [],
        showMapMarkers: true
      };

      this.pushMapState = this.pushMapState.bind(this);
      this.popMapState = this.popMapState.bind(this);
      this.clearMapData = this.clearMapData.bind(this);

    }

    clearMapData(category) {
      let { mapShapes } = this.state;
      console.log("fphoc del", mapShapes, category)
      mapShapes = update(mapShapes, { [category]: { $set: [] } });
      this.setState({ mapShapes });
    }

    toggleMapMarkers() {
      const { showMapMarkers } = this.state;
      this.setState({ showMapMarkers: !showMapMarkers });
    }

    pushMapState(stateChange, cb) {
      console.log ("Il.12,", stateChange, this.state)
      this.setState(
        state => ({
          mapFiguresHistory: update(state.mapFiguresHistory, {
            $push: [state.mapShapes]
          }),
          mapUnfinishedFigureHistory: update(state.mapUnfinishedFigureHistory, {
            $push: [state.unfinishedMapFigure]
          }),
          ...stateChange(state)
        }),
        cb
      );
      console.log("IL.12 push", this.state, this.state.mapFiguresHistory, this.state.mapUnfinishedFigureHistory)
    }

    popMapState() {
      this.setState(state => {
        let { mapFiguresHistory, mapUnfinishedFigureHistory } = state;
        if (!mapFiguresHistory.length) {
          return {};
        }

        mapFiguresHistory = mapFiguresHistory.slice();
        mapUnfinishedFigureHistory = mapUnfinishedFigureHistory.slice();
        const mapShapes = mapFiguresHistory.pop();
        let unfinishedMapFigure = mapUnfinishedFigureHistory.pop();

        if (unfinishedMapFigure && !unfinishedMapFigure.points.length) {
          unfinishedMapFigure = null;
        }

        return {
          mapShapes,
          unfinishedMapFigure,
          mapFiguresHistory,
          mapUnfinishedFigureHistory
        };
      });
    }

    render() {
      const { props, state, pushMapState, popMapState, clearMapData } = this;
      const { mapShapes, unfinishedMapFigure, showMapMarkers } = state;
      const passedProps = {
        pushMapState,
        popMapState,
        mapShapes,
        unfinishedMapFigure,
        clearMapData
      };
      return (
        <Comp
          {...passedProps}
          {...props}
          handleDrawingChange={mapShapes => this.setState({ mapShapes })}
          toggleMapMarkers={this.toggleMapMarkers.bind(this)}
          showMapMarkers={showMapMarkers}
        />
      );
    }
  };
}
