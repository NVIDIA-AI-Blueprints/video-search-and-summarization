// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import Styles from "./EventCard.module.css";
import EventThumbnail from "./EventThumbnail";
import {
  Avatar,
  Card,
  CardContent,
  CardHeader,
  CardMedia,
} from "common/warehouse/mui";
import React, { useRef, useState, useEffect } from "react";
import { isEqual } from "lodash";

/**
 * Extract place name from data
 */
function extractPlaceName(data) {
  if (data.place && data.place.name) {
    return data.place.name;
  }
  if (data.place && typeof data.place === "string") {
    return data.place;
  }
  return "";
}

/**
 * Extract timestamp as localized string
 */
function extractStartTime(data) {
  if (data.timestamp) {
    return new Date(data.timestamp).toLocaleString();
  }
  if (data.behavior && data.behavior.timestamp) {
    return new Date(data.behavior.timestamp).toLocaleString();
  }
  return "";
}

/**
 * Extract end time as localized string
 */
function extractEndTime(data) {
  if (data.end) {
    return new Date(data.end).toLocaleString();
  }
  if (data.behavior && data.behavior.end) {
    return new Date(data.behavior.end).toLocaleString();
  }
  return "";
}

/**
 * Extract sensor ID from data
 */
function extractSensorId(data) {
  if (data.sensorId) {
    return data.sensorId;
  }
  if (data.sensor && data.sensor.id) {
    return data.sensor.id;
  }
  if (data.behavior && data.behavior.sensor && data.behavior.sensor.id) {
    return data.behavior.sensor.id;
  }
  return "";
}

/**
 * Extract object ID from data
 */
function extractObjectId(data) {
  if (data.objectId) {
    return data.objectId;
  }
  if (data.object && data.object.id) {
    return data.object.id;
  }
  if (data.info && data.info.primaryObjectId) {
    return data.info.primaryObjectId;
  }
  return "";
}

/**
 * Process TripEvent data
 */
function processTripEvent(data) {
  const placeName = extractPlaceName(data);
  const startTimeLocal = extractStartTime(data);
  const endTimeLocal = extractEndTime(data);
  let title = "";
  let tripwireId = "";
  const objectId = extractObjectId(data);
  const sensorId = extractSensorId(data);

  if (data.event && data.event.type) {
    title = data.event.type;
  }
  if (data.event && data.event.id) {
    tripwireId = data.event.id;
  }
  title += ` - ${tripwireId} (ObjectID: ${objectId})`;

  return {
    placeName,
    startTimeLocal,
    endTimeLocal,
    objectId,
    tripwireId,
    title,
    description: "",
    sensorId,
  };
}

/**
 * Process ROIEvent data
 */
function processROIEvent(data) {
  const placeName = extractPlaceName(data);
  const startTimeLocal = extractStartTime(data);
  const endTimeLocal = extractEndTime(data);
  let title = "";
  let roiId = "";
  const objectId = extractObjectId(data);
  const sensorId = extractSensorId(data);

  if (data.event && data.event.type) {
    title = data.event.type;
  }
  if (data.event && data.event.id) {
    roiId = data.event.id;
  }
  title += ` - ROI: ${roiId} (ObjectID: ${objectId})`;

  return {
    placeName,
    startTimeLocal,
    endTimeLocal,
    objectId,
    tripwireId: "",
    title,
    description: "",
    sensorId,
  };
}

/**
 * Process ITS alerts and incidents data
 */
function processITSAlertsIncidents(data) {
  const placeName = extractPlaceName(data);
  const startTimeLocal = extractStartTime(data);
  const endTimeLocal = extractEndTime(data);
  let description = "";
  let title = "";
  let objectId = "";
  let sensorId = "";

  if (data.analyticsModule && data.analyticsModule.description) {
    description = data.analyticsModule.description;
  }

  if (data.type === "mdx-incidents") {
    title = "Incident";
    sensorId = data.sensorId || "";
    objectId = data.info && data.info.primaryObjectId ? data.info.primaryObjectId : "";
  } else if (data.type === "mdx-alerts") {
    title = "Alert";
    sensorId = extractSensorId(data);
    objectId = extractObjectId(data);
  }

  return {
    placeName,
    startTimeLocal,
    endTimeLocal,
    objectId,
    tripwireId: "",
    title,
    description,
    sensorId,
  };
}

/**
 * Process MTMC global event data
 */
function processMTMCGlobal(data) {
  const startTimeLocal = extractStartTime(data);
  const endTimeLocal = extractEndTime(data);
  let title = "";

  if (data.globalId) {
    title = `globalId: ${data.globalId}`;
  }

  return {
    placeName: "",
    startTimeLocal,
    endTimeLocal,
    objectId: "",
    tripwireId: "",
    title,
    description: "",
    sensorId: "",
  };
}

/**
 * Process QBE behavior data
 */
function processQBEBehavior(data) {
  const placeName = data.behavior.place && data.behavior.place.name
    ? data.behavior.place.name
    : "";
  const startTimeLocal = extractStartTime(data);
  const endTimeLocal = extractEndTime(data);
  const sensorId = extractSensorId(data);
  let title = "";
  let mdxBehaviorEventScore = "";

  if (data.behavior.id) {
    title = data.behavior.id;
  }
  if (data.match && data.match.highestScore) {
    mdxBehaviorEventScore = data.match.highestScore;
  }

  return {
    placeName,
    startTimeLocal,
    endTimeLocal,
    objectId: "",
    tripwireId: "",
    title,
    description: "",
    sensorId,
    mdxBehaviorEventScore,
  };
}

/**
 * Process default/fallback event data
 */
function processDefaultEvent(data) {
  const startTimeLocal = extractStartTime(data);
  const endTimeLocal = extractEndTime(data);
  const placeName = extractPlaceName(data);
  let title = "";

  if (data.sensorId && data.objectId) {
    title = `Sensor: ${data.sensorId}, Object Id: ${data.objectId}`;
  }

  return {
    placeName,
    startTimeLocal,
    endTimeLocal,
    objectId: data.objectId || "",
    tripwireId: "",
    title,
    description: "",
    sensorId: data.sensorId || "",
  };
}

/**
 * Build card content JSX elements
 */
function buildCardContentElements(fields) {
  const { mdxBehaviorEventScore, placeName, sensorId, startTimeLocal, endTimeLocal } = fields;

  return (
    <React.Fragment>
      {mdxBehaviorEventScore &&
        (typeof mdxBehaviorEventScore === "string" ||
          typeof mdxBehaviorEventScore === "number") && (
          <span className={Styles.contentRow}>
            <b>Score: </b>
            {mdxBehaviorEventScore}
          </span>
        )}
      {placeName &&
        (typeof placeName === "string" || typeof placeName === "number") && (
          <span className={Styles.contentRow}>
            <b>Place: </b>
            {placeName}
          </span>
        )}
      {sensorId &&
        (typeof sensorId === "string" || typeof sensorId === "number") && (
          <span className={Styles.contentRow}>
            <b>Sensor: </b>
            {sensorId}
          </span>
        )}
      {startTimeLocal &&
        (typeof startTimeLocal === "string" ||
          typeof startTimeLocal === "number") && (
          <span className={Styles.contentRow}>
            <b>Start Time: </b>
            {startTimeLocal}
          </span>
        )}
      {endTimeLocal &&
        (typeof endTimeLocal === "string" ||
          typeof endTimeLocal === "number") && (
          <span className={Styles.contentRow}>
            <b>End Time: </b>
            {endTimeLocal}
          </span>
        )}
    </React.Fragment>
  );
}

const EventData2EventCardContentElements = (data) => {
  let fields;

  if (data.analyticsModule && data.analyticsModule.id === "TripEvent") {
    fields = processTripEvent(data);
  } else if (data.analyticsModule && data.analyticsModule.id === "ROIEvent") {
    fields = processROIEvent(data);
  } else if (data.type && (data.type === "mdx-alerts" || data.type === "mdx-incidents")) {
    fields = processITSAlertsIncidents(data);
  } else if (data.type && data.type === "mdx-mtmc") {
    fields = processMTMCGlobal(data);
  } else if (data.behavior && data.behavior.type === "mdx-behavior") {
    fields = processQBEBehavior(data);
  } else {
    fields = processDefaultEvent(data);
  }

  return {
    ...fields,
    cardContentEl: buildCardContentElements(fields),
  };
};

const EventCard = React.memo(
  (props) => {
    const cardRef = useRef(null);
    const [isVisible, setIsVisible] = useState(false);

    useEffect(() => {
      const node = cardRef.current;
      if (!node) return;

      const observer = new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            setIsVisible(entry.isIntersecting);
          });
        },
        {
          root: null, // viewport
          rootMargin: "0px",
          threshold: 0, // trigger when even 1px is visible
        }
      );

      observer.observe(node);

      return () => {
        observer.disconnect();
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const { title, cardContentEl, description } = EventData2EventCardContentElements(
      props.data
    );

    let eventThumbnailProps;
    if (
      props.data.type &&
      props.data.type === "mdx-mtmc" &&
      props.data.globalId
    ) {
      eventThumbnailProps = {
        ...props,
        ...{
          data: props.data.matched[props.data.matched.length - 1],
        },
      };
    } else if (
      props.data.behavior &&
      props.data.behavior.type === "mdx-behavior"
    ) {
      eventThumbnailProps = {
        ...props,
        ...{
          data: props.data.behavior,
        },
      };
    } else {
      eventThumbnailProps = props;
    }

    return (
      <Card
        ref={cardRef}
        className={Styles.cardContainer}
        title={title}
        description={description}
        style={{
          backgroundColor: props.highlighted ? "#aaa" : "white",
        }}
        onMouseEnter={(e) => {
          props.onMouseEnter && props.onMouseEnter(e, props.data);
        }}
        onMouseLeave={(e) => {
          props.onMouseLeave && props.onMouseLeave(e, props.data);
        }}
        onClick={(e) => {
          props.onClick && props.onClick(e, props.data);
        }}
      >
        <CardHeader
          avatar={
            props.data.color ? (
              <Avatar
                style={{
                  backgroundColor: props.data.color,
                  color: props.data.color,
                }}
              />
            ) : null
          }
          title={title}
          subheader={description}
          titleTypographyProps={{
            variant: "caption",
            style: { fontWeight: "bold", textAlign: "center" },
          }}
          subheaderTypographyProps={{
            variant: "caption",
            style: { fontStyle: "italic", textAlign: "center" },
          }}
        />
        <CardMedia>
          <EventThumbnail {...eventThumbnailProps} isVisible={isVisible} />
        </CardMedia>
        <CardContent className={Styles.content}>{cardContentEl}</CardContent>
      </Card>
    );
  },
  (prevProps, nextProps) => {
    return (
      isEqual(prevProps.data, nextProps.data) &&
      isEqual(prevProps.sensorConfig, nextProps.sensorConfig) &&
      isEqual(prevProps.endpoints, nextProps.endpoints) &&
      isEqual(prevProps.highlighted, nextProps.highlighted)
    );
  }
);

export default EventCard;
