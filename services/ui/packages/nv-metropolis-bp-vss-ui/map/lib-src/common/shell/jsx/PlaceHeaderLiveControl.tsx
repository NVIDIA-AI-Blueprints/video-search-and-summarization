// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";

import CssClasses from "./../styles/PlaceHeaderLiveControl.module.css";

/**
 * Live / Go Back Live pill for {@link PlaceHeaderContent}'s center slot.
 */
const PlaceHeaderLiveControl = ({ isLive, onGoBackLive }) => {
  if (isLive) {
    return (
      <span className={CssClasses.headerControl}>
        <span className={CssClasses.liveBadge}>
          <span className={CssClasses.liveDot} aria-hidden="true" />
          Live
        </span>
      </span>
    );
  }

  return (
    <span className={CssClasses.headerControl}>
      <span
        className={`${CssClasses.liveBadge} ${CssClasses.replayBadge}`}
        role="button"
        tabIndex={0}
        onClick={onGoBackLive}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onGoBackLive && onGoBackLive();
          }
        }}
      >
        Go Back Live
      </span>
    </span>
  );
};

export default PlaceHeaderLiveControl;
