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


import update from 'immutability-helper';
import React, { Component } from 'react';
import { flipYLabels } from '../../common/mathUtils';

export function withImageDisplayHistory(Comp) {
  return class HistoryLayer extends Component {
    constructor(props) {
      super(props);

      const { mapLabelData, labels } = props;
      let mapFigures = {};
      console.log("history" , labels, mapLabelData)
      labels.map(label => (mapFigures[label.id] = []));

      mapFigures.__temp = [];
      mapFigures = flipYLabels(mapFigures, labels)
      Object.keys(mapLabelData).forEach(key => {
        mapFigures[key] = (mapFigures[key] || []).concat(mapLabelData[key]);
      });
      // mapFigures = flipY(mapFigures, this.props.height);
      console.log("do we get here??", mapFigures)
      this.state = {
        mapFigures, // mapping from label name to a list of Figure structures
        mapUnfinishedFigure: null,
        mapFiguresHistory: [],
        mapUnfinishedFigureHistory: [],
        showImageMarkers: true
      };

      // this.pushState = this.pushState.bind(this);
      // this.popState = this.popState.bind(this);
      this.clearImageData = this.clearImageData.bind(this);
    }

    clearImageData(category) {
      let { mapFigures } = this.state;
      mapFigures = update(mapFigures, { [category]: { $set: [] } });
      this.setState({ mapFigures });
    }

    toggleImageMarkers() {
      const { showImageMarkers } = this.state;
      this.setState({ showImageMarkers: !showImageMarkers });
    }

    // pushState(stateChange, cb) {
    //   this.setState(
    //     state => ({
    //       mapFiguresHistory: update(state.mapFiguresHistory, {
    //         $push: [state.mapFigures]
    //       }),
    //       mapUnfinishedFigureHistory: update(state.mapUnfinishedFigureHistory, {
    //         $push: [state.mapUnfinishedFigure]
    //       }),
    //       ...stateChange(state)
    //     }),
    //     cb
    //   );
    // }

    // popState() {
    //   this.setState(state => {
    //     let { mapFiguresHistory, mapUnfinishedFigureHistory } = state;
    //     if (!mapFiguresHistory.length) {
    //       return {};
    //     }

    //     mapFiguresHistory = mapFiguresHistory.slice();
    //     mapUnfinishedFigureHistory = mapUnfinishedFigureHistory.slice();
    //     const mapFigures = mapFiguresHistory.pop();
    //     let mapUnfinishedFigure = mapUnfinishedFigureHistory.pop();

    //     if (mapUnfinishedFigure && !mapUnfinishedFigure.points.length) {
    //       mapUnfinishedFigure = null;
    //     }

    //     return {
    //       mapFigures,
    //       mapUnfinishedFigure,
    //       mapFiguresHistory,
    //       mapUnfinishedFigureHistory
    //     };
    //   });
    // }

    render() {
      const { props, state, pushState, popState, clearImageData } = this;
      const { mapFigures, mapUnfinishedFigure, showImageMarkers } = state;
      const passedProps = {
        // pushState,
        // popState,
        mapFigures,
        mapUnfinishedFigure,
        clearImageData
      };
      return (
        <Comp
          {...passedProps}
          {...props}
          handleDrawingChange={mapFigures => this.setState({ mapFigures })}
          toggleImageMarkers={this.toggleImageMarkers.bind(this)}
          showImageMarkers={showImageMarkers}
        />
      );
    }
  };
}
