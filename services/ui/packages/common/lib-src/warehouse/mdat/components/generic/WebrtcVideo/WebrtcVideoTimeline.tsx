// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { Slider, Tooltip, styled } from "common/warehouse/mui";
import React from "react";

const PrettySlider = styled(Slider)({
  color: "#76b900",
  height: 8,
  "& .MuiSlider-track": {
    border: "none",
  },
  "& .MuiSlider-thumb": {
    height: 24,
    width: 24,
    backgroundColor: "#fff",
    border: "2px solid currentColor",
    "&:focus, &:hover, &.Mui-active, &.Mui-focusVisible": {
      boxShadow: "inherit",
    },
    "&:before": {
      display: "none",
    },
  },
  "& .MuiSlider-valueLabel": {
    lineHeight: 1.2,
    fontSize: 12,
    background: "unset",
    padding: 0,
    width: 32,
    height: 32,
    borderRadius: "50% 50% 50% 0",
    backgroundColor: "#52af77",
    transformOrigin: "bottom left",
    transform: "translate(50%, -100%) rotate(-45deg) scale(0)",
    "&:before": { display: "none" },
    "&.MuiSlider-valueLabelOpen": {
      transform: "translate(50%, -100%) rotate(-45deg) scale(1)",
    },
    "& > *": {
      transform: "rotate(45deg)",
    },
  },
});

function ValueLabelComponent(props) {
  const { children, value } = props;

  return (
    <Tooltip
      enterTouchDelay={0}
      placement="top"
      title={value}
      arrow
      PopperProps={{
        container:
          document.fullscreenElement !== null
            ? document.fullscreenElement
            : document.body,
      }}
    >
      {children}
    </Tooltip>
  );
}

class WebrtcVideoTimeline extends React.Component {
  state = {
    currentTimeMs: this.props.startMs,
    isControlled: false,
  };

  shouldComponentUpdate(nextProps, nextState) {
    if (
      this.props.currentTimeMs === nextProps.currentTimeMs &&
      this.state.currentTimeMs === nextState.currentTimeMs
    ) {
      return false;
    }
    return true;
  }

  render() {
    return (
      <PrettySlider
        min={this.props.startMs}
        max={this.props.endMs}
        valueLabelDisplay="on"
        valueLabelFormat={(ms) => new Date(ms).toLocaleString()}
        value={
          this.state.isControlled
            ? this.state.currentTimeMs
            : this.props.currentTimeMs
        }
        onChange={(event, newValue) => {
          if (!this.props.seekable) return;

          this.state.isControlled && this.setState({ currentTimeMs: newValue });
        }}
        onChangeCommitted={(event, newValue) => {
          if (!this.props.seekable) return;

          // change state after a short timeout to avoid effects getting ignored after mouse down
          this._timeoutId1 = setTimeout(() => {
            this.props.onSeekEnd && this.props.onSeekEnd(newValue);
          }, 500);
          this._timeoutId2 = setTimeout(() => {
            this.setState({
              isControlled: false,
            });
          }, 1000);
        }}
        slots={{ valueLabel: ValueLabelComponent }}
        onMouseDown={() => {
          if (!this.props.seekable) return;
          this.setState({ isControlled: true });
          this.props.onSeekStart && this.props.onSeekStart();
        }}
      />
    );
  }

  componentWillUnmount() {
    clearTimeout(this._timeoutId1);
    clearTimeout(this._timeoutId2);
  }
}

export default WebrtcVideoTimeline;
