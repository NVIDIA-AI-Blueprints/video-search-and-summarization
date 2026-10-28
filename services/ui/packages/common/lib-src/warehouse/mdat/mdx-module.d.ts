// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
declare module "common/warehouse/mdat" {
  // Define the types and exports for the module
  // For example, if EventCard is a React component, you can declare its props and type.
  import React from "react";

  export interface EventCardProps {
    data: any;
    endpoints: {
      framesUrl: String;
      mediaServerBaseUrl: String;
    };
    sensorConfig: any;
    highlighted?: Boolean | undefined;
    avatarColor?: String | undefined;
    onMouseEnter?: (e: any, data: any) => void;
    onMouseLeave?: (e: any, data: any) => void;
    onClick?: (e: any, data: any) => void;
  }

  export const EventCard: React.FC<EventCardProps>;
}
