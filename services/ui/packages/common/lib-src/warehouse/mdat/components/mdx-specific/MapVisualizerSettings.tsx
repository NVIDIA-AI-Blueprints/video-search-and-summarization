// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { Settings } from "common/warehouse/mui-icons";
import React, { useCallback, useState } from "react";
import { FormControlLabel, FormGroup, Switch } from "common/warehouse/mui";

import Styles from "./MapVisualizerSettings.module.css";

const MapVisualizerSettings = (props) => {
  const [showMenu, setShowMenu] = useState(false);
  const [settings, setSettings] = useState({
    showCameras: true,
  });

  const onClickSettings = useCallback(() => {
    setShowMenu(!showMenu);
  }, [showMenu]);

  const onCamerasSwitchChange = useCallback((event) => {
    const newSettings = { ...settings, showCameras: event.target.checked };
    setSettings(newSettings);
    props.onChange && props.onChange(newSettings);
  }, []);

  return (
    <div className={Styles.main}>
      <Settings
        title="Map Visualizer Settings"
        className={Styles.settingsBtn}
        onClick={onClickSettings}
      />
      {showMenu && (
        <div className={Styles.menuList}>
          {/* START show camera icons setting */}
          <FormGroup>
            <FormControlLabel
              control={
                <Switch
                  checked={settings.showCameras}
                  onChange={onCamerasSwitchChange}
                />
              }
              label="Show Cameras?"
            />
          </FormGroup>
          {/* END show camera icons setting */}
        </div>
      )}
    </div>
  );
};

export default MapVisualizerSettings;
