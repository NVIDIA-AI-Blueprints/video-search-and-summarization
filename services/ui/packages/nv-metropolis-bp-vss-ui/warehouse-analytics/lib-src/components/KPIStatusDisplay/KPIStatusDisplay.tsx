// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { CircularProgress } from "common/warehouse/mui";
import classes from "./KPIStatusDisplay.module.css";

/**
 * Display component for KPI loading and error states
 */
const KPIStatusDisplay = ({ type }) => {
  const renderStatusContent = () => {
    if (type === "loading") {
      return (
        <>
          <CircularProgress size={16} />
          <span className={classes.statusText}>Loading Metrics</span>
        </>
      );
    }

    if (type === "error") {
      return (
        <>
          <span className={classes.errorIcon}>⚠</span>
          <span className={classes.errorText}>Unable to load metrics</span>
        </>
      );
    }

    return null;
  };

  return (
    <div className={classes.kpiPanel}>
      <div className={classes.metricsLabel}>METRICS</div>
      <div className={classes.statusContent}>
        {renderStatusContent()}
      </div>
    </div>
  );
};

export default KPIStatusDisplay;

