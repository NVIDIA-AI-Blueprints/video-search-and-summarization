/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: LicenseRef-NvidiaProprietary
 *
 * NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
 * property and proprietary rights in and to this material, related
 * documentation and any modifications thereto. Any use, reproduction,
 * disclosure or distribution of this material and related documentation
 * without an express license agreement from NVIDIA CORPORATION or
 * its affiliates is strictly prohibited.
 */


import React, { PureComponent, forwardRef } from 'react';
import { CRS, LatLngBounds } from 'leaflet';

export const maxZoom = 7;
export function withBounds(Comp) {
  class CalcBoundsLayer extends PureComponent {
    calcBounds() {
      const crs = CRS.Simple;
      const { height, width } = this.props;

      if (!height || !width) return null;
      const southWest = crs.unproject({ x: 0, y: height }, maxZoom - 1);
      const northEast = crs.unproject({ x: width, y: 0 }, maxZoom - 1);
      const bounds = new LatLngBounds(southWest, northEast);

      return bounds;
    }

    render() {
      const { forwardedRef, ...rest } = this.props;
      const bounds = this.calcBounds();
      if (!bounds) return null;
      return <Comp bounds={bounds} ref={forwardedRef} {...rest} />;
    }
  }

  return forwardRef((props, ref) => (
    <CalcBoundsLayer {...props} forwardedRef={ref} />
  ));
}
