// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { Axios } from "common/warehouse";
import PropTypes from "prop-types";
import React from "react";
import {
  Close as CloseIcon,
  ExpandLess as ExpandLessIcon,
  ExpandMore as ExpandMoreIcon,
  Refresh as RefreshIcon,
} from "common/warehouse/mui-icons";

import { CalibrationImagesUrl } from "../js/UrlConstants";
import Styles from "./../styles/SidebarForm.module.css";

/**
 * Panel to Upload  Images from UI to the backend
 */
export default class UploadImagesPanel extends React.PureComponent {
  handleImagesFilesInput = (e) => {
    this.imagesFiles = e.target.files;
  };

  handleImagesMetadataFileInput = (e) => {
    this.imagesMetadataFiles =
      e.target.files.length === 1 ? e.target.files : undefined;
  };

  onUpload = () => {
    this.outputBoxRef.value = "";

    if (!this.imagesFiles || this.imagesFiles.length === 0) {
      this.outputBoxRef.value = "Error: No images files selected";
      return;
    }

    if (!this.imagesMetadataFiles || this.imagesMetadataFiles.length === 0) {
      this.outputBoxRef.value =
        "Error: Select single images metadata json file";
      return;
    }

    let formdata = new FormData();
    for (let i = 0; i < this.imagesFiles.length; i++) {
      formdata.append("images", this.imagesFiles[i]);
    }

    formdata.append("imageMetadata", this.imagesMetadataFiles[0]);

    Axios.post(CalibrationImagesUrl, formdata)
      .then((res) => {
        this.outputBoxRef.value = `Success! Server message: ${JSON.stringify(res.data)}`;
      })
      .catch((err) => {
        this.outputBoxRef.value = `${err.message}. Server message: ${JSON.stringify(err.response.data)}`;
      });
  };

  componentDidMount() {
    this.file = null;
  }

  render() {
    const expanded = this.props.expanded !== false;
    const canToggle = typeof this.props.onToggle === "function";

    return (
      <div className={Styles.panel}>
        <div className={Styles.header}>
          {canToggle ? (
            <button
              type="button"
              className={Styles.headerToggle}
              title={expanded ? "Collapse" : "Expand"}
              aria-expanded={expanded}
              aria-label={expanded ? "Collapse Upload Calibration Images" : "Expand Upload Calibration Images"}
              onClick={this.props.onToggle}
            >
              {expanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
              <span className={Styles.title}>Upload Calibration Images</span>
            </button>
          ) : (
            <div className={Styles.headerStatic}>
              <span className={Styles.title}>Upload Calibration Images</span>
            </div>
          )}
          {expanded ? (
            <button
              type="button"
              className={Styles.iconBtn}
              title="Reset output"
              aria-label="Reset output"
              onClick={() => {
                if (this.outputBoxRef) this.outputBoxRef.value = "";
              }}
            >
              <RefreshIcon />
            </button>
          ) : null}
          {this.props.onClose ? (
            <button
              type="button"
              className={Styles.iconBtn}
              title="Close"
              aria-label="Close"
              onClick={() => this.props.onClose?.()}
            >
              <CloseIcon />
            </button>
          ) : null}
        </div>
        <div className={`${Styles.body} ${expanded ? "" : Styles.bodyHidden}`}>
        <label className={Styles.field}>
          <span className={Styles.label}>Image files</span>
          <input
            className={Styles.file}
            type="file"
            multiple="multiple"
            onChange={(e) => this.handleImagesFilesInput(e)}
          />
        </label>
        <label className={Styles.field}>
          <span className={Styles.label}>Images metadata JSON</span>
          <input
            className={Styles.file}
            type="file"
            accept="application/json,.json"
            onChange={(e) => this.handleImagesMetadataFileInput(e)}
          />
        </label>
        <button
          type="button"
          className={Styles.submit}
          onClick={this.onUpload.bind(this)}
        >
          Upload
        </button>
        <label className={Styles.field}>
          <span className={Styles.label}>Output</span>
          <textarea
            ref={(ref) => (this.outputBoxRef = ref)}
            className={Styles.textarea}
            rows="8"
            readOnly={true}
            placeholder="Upload results appear here"
          />
        </label>
        </div>
      </div>
    );
  }
}

UploadImagesPanel.propTypes = {
  /**
   * Callback on closing the panel
   */
  onClose: PropTypes.func,
};

UploadImagesPanel.prototype.PANEL_TITLE = "Upload Images";
UploadImagesPanel.prototype.TOOL_TIP =
  "Allows uploading images associated with cameras/calibration";
