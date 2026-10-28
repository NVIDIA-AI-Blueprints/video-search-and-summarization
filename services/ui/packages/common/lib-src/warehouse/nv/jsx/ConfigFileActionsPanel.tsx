// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { readJson } from "common/warehouse";
import React from "react";
import {
  Close as CloseIcon,
  ExpandLess as ExpandLessIcon,
  ExpandMore as ExpandMoreIcon,
  Info as InfoIcon,
} from "common/warehouse/mui-icons";

import {
  CalibrationUrl,
  RoadNetworkUrl,
  UploadConfigFileUrl,
} from "../js/UrlConstants";
import Styles from "./../styles/SidebarForm.module.css";

/**
 * A config panel for modifying backend ES database from UI via the backend Node JS REST APIs
 */
export default class ConfigFileActionsPanel extends React.PureComponent {
  state = {
    selectListOption: ConfigFileActionsPanel.SelectedAction.upload.title,
    selectedConfigType: undefined,
    postSubmitInfo: null,
  };

  static get SelectedAction() {
    const actions = {
      upload: {
        title: "Upload Config",
        info: "Select config type. Upload a json config file for the type.",
      },
      get: {
        title: "Get Config",
        info: "Select config type. Shows config file content in output area",
      },
    };

    return actions;
  }

  handleActionSelectChange = (event) => {
    this.setState({
      selectListOption: event.target.value,
      postSubmitInfo: null,
    });
  };

  handleFileInput = (e) => {
    this.filesToUpload = e.target.files;
  };

  onSubmit = () => {
    this.setState({ postSubmitInfo: "Working..." });

    if (!this.state.selectedConfigType) {
      this.setState({ postSubmitInfo: "No config type selected" });
      return;
    }

    if (
      this.state.selectListOption ===
      ConfigFileActionsPanel.SelectedAction.upload.title
    ) {
      if (this.filesToUpload.length === 0) {
        this.setState({ postSubmitInfo: "No file(s) selected to upload" });
      } else {
        let formdata = new FormData();
        for (let i = 0; i < this.filesToUpload.length; i++) {
          formdata.append("configFiles", this.filesToUpload[i]);
        }

        fetch(`${UploadConfigFileUrl}/${this.state.selectedConfigType}`, {
          method: "POST",
          body: formdata,
        })
          .then((response) => readJson(response, "POST"))
          .then(() => {
            this.setState({ postSubmitInfo: "Successfully uploaded files" });
          })
          .catch((err) => {
            this.setState({
              postSubmitInfo: `${err.message}. Server message: ${JSON.stringify(err.response?.data)}`,
            });
          });
      }
      this.uploadConfigInputAreaRef.value = "";
    } else if (
      this.state.selectListOption ===
      ConfigFileActionsPanel.SelectedAction.get.title
    ) {
      this.getConfigOutputTextAreaRef.value = "";
      let url;
      if (this.state.selectedConfigType === "calibration") {
        url = CalibrationUrl;
      } else if (this.state.selectedConfigType === "road-network") {
        url = RoadNetworkUrl;
      }
      fetch(url)
        .then((response) => readJson(response))
        .then((data) => {
          this.getConfigOutputTextAreaRef.value = JSON.stringify(data);
          this.setState({ postSubmitInfo: "Successfully fetched" });
        })
        .catch((err) => {
          console.error(err);
          this.setState({
            postSubmitInfo: err.message + " -> " + (err.response?.data?.error || err.message),
          });
        });
    }
  };

  _processConfigTypes = (props) => {
    if (
      props.configTypes &&
      Array.isArray(props.configTypes) &&
      props.configTypes.length > 0
    ) {
      this.setState({ selectedConfigType: props.configTypes[0] });
    } else {
      this.setState({ selectedConfigType: undefined });
    }
  };

  UNSAFE_componentWillMount() {
    this.filesToUpload = [];
  }

  componentDidMount() {
    this._processConfigTypes(this.props);
  }

  UNSAFE_componentWillReceiveProps(nextProps) {
    if (this.props !== nextProps) {
      this._processConfigTypes(nextProps);
    }
  }

  render() {
    let actionSubPanel;
    let actionInfo;

    if (
      this.state.selectListOption ===
      ConfigFileActionsPanel.SelectedAction.upload.title
    ) {
      actionSubPanel = (
        <label className={Styles.field}>
          <span className={Styles.label}>Select file(s)</span>
          <input
            ref={(ref) => (this.uploadConfigInputAreaRef = ref)}
            disabled={this.state.selectedConfigType === undefined}
            className={Styles.file}
            type="file"
            multiple="multiple"
            onChange={(e) => this.handleFileInput(e)}
          />
        </label>
      );
      actionInfo = ConfigFileActionsPanel.SelectedAction.upload.info;
    } else if (
      this.state.selectListOption ===
      ConfigFileActionsPanel.SelectedAction.get.title
    ) {
      actionInfo = ConfigFileActionsPanel.SelectedAction.get.info;
      actionSubPanel = (
        <label className={Styles.field}>
          <span className={Styles.label}>Fetched config</span>
          <textarea
            ref={(ref) => (this.getConfigOutputTextAreaRef = ref)}
            className={Styles.textarea}
            disabled={this.state.selectedConfigType === undefined}
            rows="8"
            readOnly={true}
            placeholder="Fetched document contents appear here"
          />
        </label>
      );
    }

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
              aria-label={expanded ? "Collapse View/Update Config Files" : "Expand View/Update Config Files"}
              onClick={this.props.onToggle}
            >
              {expanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
              <span className={Styles.title}>View/Update Config Files</span>
            </button>
          ) : (
            <div className={Styles.headerStatic}>
              <span className={Styles.title}>View/Update Config Files</span>
            </div>
          )}
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
          <span className={Styles.label}>Action</span>
          <select
            className={Styles.select}
            onChange={this.handleActionSelectChange.bind(this)}
            required
          >
            <option>
              {ConfigFileActionsPanel.SelectedAction.upload.title}
            </option>
            <option>{ConfigFileActionsPanel.SelectedAction.get.title}</option>
          </select>
        </label>
        <div className={Styles.info}>
          <InfoIcon />
          <span>{actionInfo}</span>
        </div>
        <label className={Styles.field}>
          <span className={Styles.label}>Config type</span>
          <select
            className={Styles.select}
            defaultValue={
              this.state.selectedConfigType
                ? this.state.selectedConfigType
                : "(No types available)"
            }
            required
            onChange={(event) => {
              this.setState({ selectedConfigType: event.target.value });
            }}
          >
            {this.props.configTypes &&
            Array.isArray(this.props.configTypes) &&
            this.props.configTypes.length > 0 ? (
              this.props.configTypes.map((type, key) => (
                <option
                  key={`configfile-actions-panel-config-type-select-${key}`}
                >
                  {type}
                </option>
              ))
            ) : (
              <option disabled>(No types available)</option>
            )}
          </select>
        </label>
        {actionSubPanel}
        <button
          type="button"
          className={Styles.submit}
          disabled={this.state.selectedConfigType === undefined}
          onClick={this.onSubmit.bind(this)}
        >
          Submit
        </button>
        {this.state.postSubmitInfo !== null ? (
          <div className={Styles.status}>Last action: {this.state.postSubmitInfo}</div>
        ) : null}
        </div>
      </div>
    );
  }
}

ConfigFileActionsPanel.prototype.PANEL_TITLE = "Config File Actions";
ConfigFileActionsPanel.prototype.TOOL_TIP =
  "Opens panel to update/view configs in backend database";
