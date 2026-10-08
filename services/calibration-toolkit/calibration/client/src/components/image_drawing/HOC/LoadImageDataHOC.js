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


import React, { Component } from "react";

export function withLoadImageData(Comp) {
  return class LoadImageLayer extends Component {
    _img = null;
    constructor(props) {
      super(props);
      this.state = {
        height: null,
        width: null,
        imageData: null
      };

      this.componentDidUpdate({}, this.state);
    }

    componentDidUpdate(prevProps) {
      const { imageUrl } = this.props;
      // console.log("load img", imageUrl)
      if (imageUrl !== prevProps.imageUrl) {
        this._img = new Image();
        const setState = this.setState.bind(this);
        this._img.onload = async function() {
          const { height, width } = this;
          // console.log("height,width", height,width)
          setState({ height, width });
        };
        this._img.src = imageUrl;
      }
    }

    componentWillUnmount() {
      if (!this._img) {
        return;
      }
      this._img.onload = function() {};
      delete this._img;
    }

    render() {
      const { props, state } = this;
      const { height, width, imageData } = state;

      if (!height) return null;
      console.log("im", height, width, imageData)

      return (
        <Comp height={height} width={width} imageData={imageData} {...props} />
      );
    }
  };
}
