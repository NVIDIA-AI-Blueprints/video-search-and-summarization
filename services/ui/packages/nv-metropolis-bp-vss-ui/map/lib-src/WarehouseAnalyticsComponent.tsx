// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React, { useEffect, useMemo, useState } from 'react';
import { combineReducers, configureStore } from '@reduxjs/toolkit';
import { ThemeProvider } from '@nvidia/foundations-react-core';
import { Provider, connect } from 'react-redux';
import {
  AppConfigReduxUtil,
  AppConstants,
  AppLogsConsoleReduxUtil,
  CalibrationUrl,
  getEnv,
  CONFIG_PANEL_ALLOWED_CONFIG_TYPES_DEFAULT,
  LoaderDisplay,
  ReduxUtils,
  UserMessage,
  withLoaderDisplay,
  readJson,
} from 'common/warehouse';

import BuildingsLevelView from './page-builder/BuildingsLevelView';
import MetaDataReduxUtil from './utils/MetaDataReduxUtil';
import UiStateReduxUtil from './utils/UiStateReduxUtil';
import { WarehouseAnalyticsSidebarControls } from './components/WarehouseAnalyticsSidebarControls';
import classes from './App.module.css';

export interface WarehouseAnalyticsSidebarControlHandlers {
  controlsComponent: React.ReactNode;
}

export interface WarehouseAnalyticsData {
  systemStatus?: string;
  apiUrl?: string | null;
  mediaStreamingUrl?: string | null;
  googleMapsApiKey?: string | null;
  uiConfigUrl?: string | null;
}

export interface WarehouseAnalyticsComponentProps {
  theme?: 'light' | 'dark';
  className?: string;
  style?: React.CSSProperties;
  warehouseData?: WarehouseAnalyticsData | null;
  mapData?: WarehouseAnalyticsData | null;
  renderControlsInLeftSidebar?: boolean;
  onControlsReady?: (handlers: WarehouseAnalyticsSidebarControlHandlers) => void;
  isActive?: boolean;
  registerChatAnswerHandler?: (handler: (answer: string) => boolean | void) => void | (() => void);
  registerSidebarChatEventSubscriber?: (
    handler: (event: { type: 'messageSubmitted' } | { type: 'answerComplete' }) => void,
  ) => void | (() => void);
}

const BuildingsLevelViewWithLoaderDisplay = withLoaderDisplay(BuildingsLevelView);

const createStore = () =>
  configureStore({
    reducer: combineReducers(
      Object.fromEntries([
        [AppConstants.APP_LOGS_CONSOLE_NAME, AppLogsConsoleReduxUtil.slice.reducer],
        ['appConfig', AppConfigReduxUtil.slice.reducer],
        ['metadata', MetaDataReduxUtil.slice.reducer],
        ['uiState', UiStateReduxUtil.slice.reducer],
      ]),
    ),
    middleware: (getDefaultMiddleware) =>
      getDefaultMiddleware({
        immutableCheck: false,
        serializableCheck: false,
      }),
  });

const DEFAULT_INITIAL_USER_MESSAGE = 'Loading map…';

const selectInitialView = (metadata: {
  buildingsMap?: Map<string, { rooms?: Set<string> }>;
  roomsMap?: Map<string, unknown>;
}) => {
  const buildings = metadata?.buildingsMap;
  const rooms = metadata?.roomsMap;
  if (rooms && rooms.size === 1 && buildings && buildings.size >= 1) {
    const buildingName = buildings.keys().next().value as string;
    const roomName = rooms.keys().next().value as string;
    return {
      selectedBuilding: buildingName,
      selectedRoom: roomName,
      viewType: 'room-level-live',
    };
  }
  if (buildings && buildings.size === 1) {
    const buildingName = buildings.keys().next().value as string;
    return {
      selectedBuilding: buildingName,
      viewType: 'building-level-live',
    };
  }
  return { viewType: 'city-level-live' };
};

type AppInnerProps = WarehouseAnalyticsComponentProps & {
  sidebarControls: React.ReactNode;
  updateAppConfig: (config: unknown) => void;
  updateMetaData: (data: unknown) => void;
  updateUiState: (state: unknown) => void;
  addLogToAppLogs?: (message: unknown) => void;
};

const WarehouseAnalyticsAppInner: React.FC<AppInnerProps> = ({
  theme = 'light',
  className = '',
  style,
  warehouseData,
  mapData,
  renderControlsInLeftSidebar = false,
  onControlsReady,
  sidebarControls,
  updateAppConfig,
  updateMetaData,
  updateUiState,
  addLogToAppLogs,
}) => {
  const data = warehouseData || mapData;
  const [loadState, setLoadState] = useState(LoaderDisplay.TYPE.LOADING);
  const [userMessage, setUserMessage] = useState<
    InstanceType<typeof UserMessage> | string | null
  >(DEFAULT_INITIAL_USER_MESSAGE);

  useEffect(() => {
    if (onControlsReady && renderControlsInLeftSidebar) {
      onControlsReady({ controlsComponent: sidebarControls });
    }
  }, [onControlsReady, renderControlsInLeftSidebar, sidebarControls]);

  useEffect(() => {
    const mapsKey = data?.googleMapsApiKey || getEnv('REACT_APP_GOOGLE_MAPS_API_KEY');
    if (!mapsKey || typeof window === 'undefined') return;
    const current = window.__ENV || {};
    if (current.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY !== mapsKey) {
      window.__ENV = {
        ...current,
        NEXT_PUBLIC_GOOGLE_MAPS_API_KEY: mapsKey,
      };
    }
  }, [data?.googleMapsApiKey]);

  useEffect(() => {
    let cancelled = false;
    const fail = (purpose: string, error: unknown) => {
      const message = new UserMessage(
        error && typeof error === 'object' && 'config' in (error as object)
          ? UserMessage.SRC_TYPE.HTTP_ERROR
          : UserMessage.SRC_TYPE.GENERIC,
        UserMessage.MESSAGE_LEVEL.ERROR,
        purpose,
        error,
        'RETRY',
        () => window.location.reload(),
      );
      console.error(message.getLongString());
      addLogToAppLogs?.(message);
      if (!cancelled) {
        setUserMessage(message);
        setLoadState(LoaderDisplay.TYPE.LOAD_FAILURE);
      }
    };

    const load = async () => {
      try {
        const configUrl =
          data?.uiConfigUrl || '/warehouse-analytics/default-ui-config.json';
        const configResponse = await fetch(configUrl);
        const configData = await readJson(configResponse) as { uiDelaySeconds?: number };
        if (cancelled) return;
        updateAppConfig(configData);
        const uiDelayMs = (configData?.uiDelaySeconds || 0) * 1000;
        updateUiState({ appStartTimeMs: Date.now() - uiDelayMs });

        const calibrationResponse = await fetch(CalibrationUrl);
        const calibrationData = await readJson(calibrationResponse);
        if (cancelled) return;
        updateMetaData(calibrationData);
        updateUiState(selectInitialView(calibrationData as never));
        setUserMessage(null);
        setLoadState(LoaderDisplay.TYPE.LOAD_SUCCESS);
      } catch (error) {
        fail('Fetch warehouse analytics configuration', error);
      }
    };

    void load();
    return () => {
      cancelled = true;
    };
  }, [addLogToAppLogs, data?.uiConfigUrl, updateAppConfig, updateMetaData, updateUiState]);

  return (
    <ThemeProvider theme="light" asChild>
      <div
        className={`nv-light relative h-full min-h-0 w-full overflow-hidden text-neutral-900 ${theme === 'dark' ? 'bg-black' : 'bg-white'} ${className}`}
        style={{ colorScheme: 'light', ...style }}
      >
        {!renderControlsInLeftSidebar ? (
          <div className="absolute right-2 top-2 z-20 flex max-h-[40%] max-w-md flex-col gap-2 overflow-auto rounded border border-neutral-300 bg-white/90 p-2 text-xs text-neutral-900">
            {sidebarControls}
          </div>
        ) : null}
        <div className={classes.placeContent}>
          <BuildingsLevelViewWithLoaderDisplay
            loaderDisplayType={loadState}
            loaderDisplayUserMessage={userMessage}
            loaderDisplaySize={LoaderDisplay.SIZE.SMALL}
            loaderAfterSuccessDelayMs={0}
          />
        </div>
      </div>
    </ThemeProvider>
  );
};

const ConnectedWarehouseAnalyticsApp = connect(
  null,
  ReduxUtils.combineDispatchToProps(
    AppLogsConsoleReduxUtil.slice.actions,
    AppConfigReduxUtil.slice.actions,
    MetaDataReduxUtil.slice.actions,
    UiStateReduxUtil.slice.actions,
  ),
)(WarehouseAnalyticsAppInner);

export const WarehouseAnalyticsComponent: React.FC<WarehouseAnalyticsComponentProps> = (props) => {
  const store = useMemo(() => createStore(), []);
  const sidebarControls = useMemo(
    () => (
      <Provider store={store}>
        <WarehouseAnalyticsSidebarControls
          configTypes={CONFIG_PANEL_ALLOWED_CONFIG_TYPES_DEFAULT}
          theme={props.theme}
        />
      </Provider>
    ),
    [store, props.theme],
  );
  return (
    <Provider store={store}>
      <ConnectedWarehouseAnalyticsApp {...props} sidebarControls={sidebarControls} />
    </Provider>
  );
};

export default WarehouseAnalyticsComponent;
