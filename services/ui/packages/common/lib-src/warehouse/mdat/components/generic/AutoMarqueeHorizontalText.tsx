// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React, { useEffect, useRef, useState } from "react";

import Styles from "./AutoMarqueeHorizontalText.module.css";

const AutoMarqueeHorizontalText = React.memo(
  ({ children }) => {
    const [isContentOverflowing, setIsContentOverflowing] = useState(false);
    const prevChildrenRef = useRef(children);
    const hiddenContainerRef = useRef(null);
    const containerParentRef = useRef(null);

    const checkContentOverflow = () => {
      if (children !== prevChildrenRef.current) {
        prevChildrenRef.current = children;

        if (containerParentRef.current && hiddenContainerRef.current) {
          const containerWidth =
            containerParentRef.current.getBoundingClientRect().width;
          const contentWidth =
            hiddenContainerRef.current.getBoundingClientRect().width;

          setIsContentOverflowing(contentWidth > containerWidth);
        }
      }
    };

    useEffect(() => {
      checkContentOverflow();
    }, [children]);

    return (
      <div ref={containerParentRef} className={Styles.contentDiv}>
        <div
          ref={hiddenContainerRef}
          style={{
            visibility: "hidden",
            position: "absolute",
            whiteSpace: "nowrap",
          }}
        >
          {children}
        </div>
        {isContentOverflowing ? (
          <div className={Styles.marquee}>
            <span className={Styles.marqueeInner}>{children}</span>
            <span className={Styles.marqueeInner} aria-hidden="true">
              {children}
            </span>
          </div>
        ) : (
          <div>{children}</div>
        )}
      </div>
    );
  },
  (prevProps, nextProps) => prevProps.children === nextProps.children
);

export default AutoMarqueeHorizontalText;
