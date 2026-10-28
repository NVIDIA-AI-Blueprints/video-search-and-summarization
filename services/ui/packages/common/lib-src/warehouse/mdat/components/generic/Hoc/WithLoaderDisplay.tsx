// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import LoaderDisplay from "../LoaderDisplay/LoaderDisplay";

/**
 * Returns Higher Order Component with Loader Display
 *
 * Props:
 *   loaderDisplayType: LoaderDisplay.TYPE enum (REQUIRED)
 *   loaderDisplayUserMessage: UserMessage object
 *   loaderDisplaySize: LoaderDisplay.SIZE enum
 *   loaderAfterSuccessDelayMs: Ms delay to load main component after showing success
 *
 * Usage:
 *  import { withLoaderDisplay } from 'common/warehouse';
 *  let BuildingsLevelViewWithLoaderDisplay = withLoaderDisplay(BuildingsLevelView);
 *
 *  // in render function
 *  render() {
 *    return (
 *      <div className={classes.placeContent}>
 *        <BuildingsLevelViewWithLoaderDisplay
 *          loaderDisplayType={this.state.loadState}
 *          loaderDisplayUserMessage={this.userMessage}
 *          loaderDisplaySize={LoaderDisplay.SIZE.LARGE}
 *          endpointsConfig={this.endpointsConfig}
 *          appConfig={this.appConfig}
 *        />
 *      </div>
 *    );
 *  }
 */
const withLoaderDisplay = (BaseComponent) => {
  return class BaseWithLoaderDisplay extends React.PureComponent {
    state = {
      didShowSuccess: false,
    };

    static DEFAULT_AFTER_SUCCESS_DELAY_MS = 1000;

    UNSAFE_componentWillReceiveProps(nextProps) {
      // reset state if loader display type has changed back from success
      if (this.props.loaderDisplayType !== nextProps.loaderDisplayType) {
        this.setState({ didShowSuccess: false });
      }
    }

    render() {
      let {
        loaderDisplayType,
        loaderDisplayUserMessage,
        loaderDisplaySize,
        loaderAfterSuccessDelayMs,
        ...baseComponentProps
      } = this.props;

      if (loaderDisplayType === LoaderDisplay.TYPE.LOAD_SUCCESS) {
        // First show success display for 1s and then the base component

        if (this.state.didShowSuccess === true) {
          return <BaseComponent {...baseComponentProps} />;
        }
        // else schedule to show base component after a while
        this.uponSuccessDelayTimerId = setTimeout(
          () => {
            this.setState({ didShowSuccess: true });
          },
          loaderAfterSuccessDelayMs
            ? loaderAfterSuccessDelayMs
            : BaseWithLoaderDisplay.DEFAULT_AFTER_SUCCESS_DELAY_MS
        );
      }

      return (
        <LoaderDisplay
          type={loaderDisplayType}
          userMessage={loaderDisplayUserMessage}
          size={loaderDisplaySize}
        />
      );
    }

    componentWillUnmount() {
      // if timeout didn't complete, clear it
      clearTimeout(this.uponSuccessDelayTimerId);
    }
  };
};

export default withLoaderDisplay;
