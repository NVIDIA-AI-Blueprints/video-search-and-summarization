// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";

import Styles from "./AboutModal.module.css";

/**
 * Takes full width/height of the parent div/widget.
 * Provides on close callback.
 * Provide content as children.
 */
const AboutModal = (props) => {
  return (
    <div className={Styles.card}>
      <div className={Styles.header}>
        <div>
          <div className={Styles.kicker}>Help</div>
          <h2 className={Styles.title}>{props.heading || "About"}</h2>
        </div>
        <button
          type="button"
          className={Styles.close}
          onClick={props.onClose}
          aria-label="Close help"
        >
          ×
        </button>
      </div>
      <div className={Styles.body}>{props.children}</div>
      <div className={Styles.footer}>
        <button type="button" className={Styles.closeBtn} onClick={props.onClose}>
          Close
        </button>
      </div>
    </div>
  );
};

export default AboutModal;
