// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React, { useEffect, useMemo, useRef, useState } from "react";
import MarqueeText from "react-marquee-text";
import "react-marquee-text/dist/styles.css";

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
        {/* Hidden element to measure content width */}
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
          <MarqueeText pauseOnHover={true} willChange={true} direction="right">
            {children}
          </MarqueeText>
        ) : (
          <div>{children}</div>
        )}
      </div>
    );
  },
  (prevProps, nextProps) => prevProps.children === nextProps.children
);

export default AutoMarqueeHorizontalText;
