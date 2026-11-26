// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React, { PureComponent } from "react";
import CssClasses from "./SpeedLegendsStrip.module.css";

/**
 * Stateless Speed Legends Strip to depict progression in speed as per colors
 */
class SpeedLegendsStrip extends PureComponent {
  render() {
    return (
      <div className={CssClasses.container}>
        <div className={CssClasses.textLeft}>Slow</div>
        <div className={CssClasses.legendContainer1} title={"<10mph"}>
          <div className={CssClasses.legendIcon0To10Speed} />
        </div>
        <div className={CssClasses.legendContainer2} title={"<20mph"}>
          <div className={CssClasses.legendIcon10To20Speed} />
        </div>
        <div className={CssClasses.legendContainer3} title={"<30mph"}>
          <div className={CssClasses.legendIcon20To30Speed} />
        </div>
        <div className={CssClasses.legendContainer4} title={"<45mph"}>
          <div className={CssClasses.legendIcon30To45Speed} />
        </div>
        <div className={CssClasses.legendContainer5} title={">45mph"}>
          <div className={CssClasses.legendIcon45ToHigherSpeed} />
        </div>
        <div className={CssClasses.textRight}>Fast</div>
      </div>
    );
  }
}

export default SpeedLegendsStrip;
