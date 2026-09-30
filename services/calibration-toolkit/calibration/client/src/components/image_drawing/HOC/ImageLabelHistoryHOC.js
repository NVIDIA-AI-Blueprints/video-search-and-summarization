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

export function withImageHistory(Comp) {
  return class HistoryLayer extends Component {
    constructor(props) {
      super(props);

      const { labelData, drawLabels } = props;
      let figures = {};
      console.log("HOC", drawLabels)
      drawLabels.map(label => (figures[label.id] = []));
      figures.__temp = [];
      Object.keys(labelData).forEach(key => {
        figures[key] = (figures[key] || []).concat(labelData[key]);
      });
      console.log("ilhoc", figures)
      // figures = flipY(figures, this.props.height);
      figures = flipYLabels(figures,drawLabels)
      this.state = {
        figures, // mapping from label name to a list of Figure structures
        unfinishedFigure: null,
        figuresHistory: [],
        unfinishedFigureHistory: [],
        showImageMarkers: true
      };

      this.pushState = this.pushState.bind(this);
      this.popState = this.popState.bind(this);
      this.clearImageData = this.clearImageData.bind(this);
    }

    clearImageData(category) {
      let { figures } = this.state;
      figures = update(figures, { [category]: { $set: [] } });
      this.setState({ figures });
    }

    toggleImageMarkers() {
      const { showImageMarkers } = this.state;
      this.setState({ showImageMarkers: !showImageMarkers });
    }

    pushState(stateChange, cb) {
      console.log ("fph12,", stateChange)
      this.setState(
        state => ({
          figuresHistory: update(state.figuresHistory, {
            $push: [state.figures]
          }),
          unfinishedFigureHistory: update(state.unfinishedFigureHistory, {
            $push: [state.unfinishedFigure]
          }),
          ...stateChange(state)
        }),
        cb
      );
      console.log("fph12 push", this.state.figuresHistory, this.state.unfinishedFigureHistory)

    }

    popState() {
      this.setState(state => {
        let { figuresHistory, unfinishedFigureHistory } = state;
        if (!figuresHistory.length) {
          return {};
        }

        figuresHistory = figuresHistory.slice();
        unfinishedFigureHistory = unfinishedFigureHistory.slice();
        const figures = figuresHistory.pop();
        let unfinishedFigure = unfinishedFigureHistory.pop();

        if (unfinishedFigure && !unfinishedFigure.points.length) {
          unfinishedFigure = null;
        }

        return {
          figures,
          unfinishedFigure,
          figuresHistory,
          unfinishedFigureHistory
        };
      });
    }

    render() {
      const { props, state, pushState, popState, clearImageData } = this;
      const { figures, unfinishedFigure, showImageMarkers } = state;
      const passedProps = {
        pushState,
        popState,
        figures,
        unfinishedFigure,
        clearImageData
      };
      return (
        <Comp
          {...passedProps}
          {...props}
          toggleImageMarkers={this.toggleImageMarkers.bind(this)}
          showImageMarkers={showImageMarkers}
        />
      );
    }
  };
}
