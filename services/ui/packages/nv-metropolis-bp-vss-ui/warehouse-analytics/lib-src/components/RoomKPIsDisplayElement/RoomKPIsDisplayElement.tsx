// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
//import PropTypes from 'prop-types';

import classes from "./RoomKPIsDisplayElement.module.css";
import sharedClasses from "./../KPIShared/KPIAnimations.module.css";

/**
 * Display Panel for KPIs for a Room
 */
class RoomKPIsDisplayElement extends React.PureComponent {
  constructor(props) {
    super(props);
    this.state = {
      currentObjectTypeIndex: 0,
      previousObjectTypeIndex: null,
      isAnimating: false,
      isPaused: false,
      secondsAgo: this.calculateSecondsAgo(props.lastSuccessfulUpdateTimeMs),
    };
  }

  ANIMATION_DURATION = 500; // Match CSS animation duration (0.5s)
  CYCLE_DURATION = 2500; // Total time for one complete cycle (in ms)
  SECONDS_AGO_UPDATE_INTERVAL = 1000; // Update seconds ago every 1 second

  componentDidMount() {
    // Start the rolling animation if there are multiple object types
    if (this.props.kpiData.objectTypes && this.props.kpiData.objectTypes.length > 1) {
      this.startRollingAnimation();
    }
    // Start seconds ago timer if there's an error
    if (this.props.hasError) {
      this.startSecondsAgoTimer();
    }
  }

  componentDidUpdate(prevProps) {
    // Restart animation if object types data changes
    if (this.props.kpiData.objectTypes !== prevProps.kpiData.objectTypes) {
      this.stopRollingAnimation();
      this.setState({ currentObjectTypeIndex: 0, previousObjectTypeIndex: null, isAnimating: false });
      // Only restart animation if not paused and there are multiple object types
      if (!this.state.isPaused && this.props.kpiData.objectTypes && this.props.kpiData.objectTypes.length > 1) {
        this.startRollingAnimation();
      }
    }
    // Manage seconds ago timer based on error state
    if (this.props.hasError && !prevProps.hasError) {
      this.startSecondsAgoTimer();
    } else if (!this.props.hasError && prevProps.hasError) {
      this.stopSecondsAgoTimer();
    }
  }

  componentWillUnmount() {
    this.stopRollingAnimation();
    this.stopSecondsAgoTimer();
  }

  calculateSecondsAgo = (timestampMs) => {
    if (!timestampMs) return null;
    return Math.round((Date.now() - timestampMs) / 1000);
  };

  startSecondsAgoTimer = () => {
    this.stopSecondsAgoTimer(); // Clear any existing timer
    this.secondsAgoInterval = setInterval(() => {
      this.setState({
        secondsAgo: this.calculateSecondsAgo(this.props.lastSuccessfulUpdateTimeMs),
      });
    }, this.SECONDS_AGO_UPDATE_INTERVAL);
  };

  stopSecondsAgoTimer = () => {
    if (this.secondsAgoInterval) {
      clearInterval(this.secondsAgoInterval);
      this.secondsAgoInterval = null;
    }
  };

  startRollingAnimation = () => {
    this.rollingInterval = setInterval(() => {
      const nextIndex = (this.state.currentObjectTypeIndex + 1) % this.props.kpiData.objectTypes.length;
      
      // Start animation: set previous index and trigger animation
      this.setState({
        previousObjectTypeIndex: this.state.currentObjectTypeIndex,
        currentObjectTypeIndex: nextIndex,
        isAnimating: true,
      });

      // End animation after duration
      setTimeout(() => {
        this.setState({
          previousObjectTypeIndex: null,
          isAnimating: false,
        });
      }, this.ANIMATION_DURATION);
    }, this.CYCLE_DURATION);
  };

  stopRollingAnimation = () => {
    if (this.rollingInterval) {
      clearInterval(this.rollingInterval);
      this.rollingInterval = null;
    }
  };

  togglePause = () => {
    this.setState((prevState) => {
      const newPausedState = !prevState.isPaused;
      
      if (newPausedState) {
        this.stopRollingAnimation();
      } else {
        this.startRollingAnimation();
      }
      
      return { isPaused: newPausedState };
    });
  };

  renderTickerRow = (objectType, animationClass) => {
    return (
      <div className={`${sharedClasses.tickerRow} ${animationClass}`}>
        <div className={classes.kpiPairR1}>
          <div className={classes.kpiPairR1Name}>Type</div>
          <div className={classes.kpiPairR1Value}>{objectType.type}</div>
        </div>
        <div className={classes.kpiPairR2}>
          <div className={classes.kpiPairR2Name}>IN</div>
          <div className={classes.kpiPairR2Value}>{objectType.inCount}</div>
        </div>
        <div className={classes.kpiPairR3}>
          <div className={classes.kpiPairR3Name}>OUT</div>
          <div className={classes.kpiPairR3Value}>{objectType.outCount}</div>
        </div>
        <div className={classes.kpiPairR4}>
          <div className={classes.kpiPairR4Name}>Occupancy</div>
          <div className={classes.kpiPairR4Value}>{objectType.occupancy}</div>
        </div>
      </div>
    );
  };

  render() {
    const { objectTypes, type, inCount, outCount, occupancy } = this.props.kpiData;
    const { hasError } = this.props;
    const { currentObjectTypeIndex, previousObjectTypeIndex, isAnimating, isPaused, secondsAgo } = this.state;
    const hasMultipleObjectTypes = objectTypes && objectTypes.length > 1;

    // Get current and previous object types
    // Use ?? instead of || to allow 0 as a valid value
    const currentObjectType = objectTypes && objectTypes.length > 0
      ? {
          ...objectTypes[currentObjectTypeIndex],
          type: objectTypes[currentObjectTypeIndex].type || "UNKNOWN",
          inCount: objectTypes[currentObjectTypeIndex].inCount ?? "N/A",
          outCount: objectTypes[currentObjectTypeIndex].outCount ?? "N/A",
          occupancy: objectTypes[currentObjectTypeIndex].occupancy ?? "N/A",
        }
      : { type: type || "UNKNOWN", inCount: inCount ?? "N/A", outCount: outCount ?? "N/A", occupancy: occupancy ?? "N/A" };

    const previousObjectType = previousObjectTypeIndex !== null && objectTypes
      ? objectTypes[previousObjectTypeIndex]
      : null;

    return (
      <div className={classes.kpiPanel}>
        <div className={classes.metricsLabel}>METRICS</div>
        {hasError && (
          <div 
            className={classes.errorIndicator}
            title={`Last successful data update: ${secondsAgo !== null ? secondsAgo : "unknown"} seconds ago`}
          >
            <span className={classes.errorIcon}>⚠</span>
          </div>
        )}
        <div 
          className={`${classes.kpiTupleCenter} ${isPaused ? classes.paused : ""} ${hasMultipleObjectTypes ? classes.clickable : ""}`}
          onClick={hasMultipleObjectTypes ? this.togglePause : undefined}
          title={hasMultipleObjectTypes 
            ? (isPaused ? "Click to resume rolling type and its counts" : "Click to pause rolling type and its counts") 
            : ""}
        >
          <div className={sharedClasses.tickerContainer}>
            {/* Outgoing content (slides up and out) */}
            {isAnimating && previousObjectType && (
              this.renderTickerRow(previousObjectType, sharedClasses.slideOut)
            )}
            {/* Incoming content (slides up from below) */}
            {this.renderTickerRow(
              currentObjectType,
              isAnimating && hasMultipleObjectTypes ? sharedClasses.slideIn : ""
            )}
          </div>
          {hasMultipleObjectTypes && isPaused && (
            <div className={sharedClasses.pauseIndicator}>
              <span className={sharedClasses.pauseIcon}>⏸</span>
            </div>
          )}
        </div>
      </div>
    );
  }
}

//RoomKPIsDisplayElement.propTypes = {
//  /**
//   * Place String name to be used for Query to the backend for KPIs for the place
//   */
//  placeName: PropTypes.string.isRequired,
//  /**
//   * JSON Object containing Application's config params
//   */
//  appConfig: PropTypes.object.isRequired,
//  /**
//   * Function to get current time w.r.t. the application's start time
//   *
//   * @return { Number } time in Milliseconds
//   */
//  getCurrentTimestampMs: PropTypes.func.isRequired,
//}

export default RoomKPIsDisplayElement;
