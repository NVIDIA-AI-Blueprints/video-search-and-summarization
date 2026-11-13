// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React, { useEffect, useMemo, useState } from 'react';
import { ThemeProvider } from '@nvidia/foundations-react-core';
import {
  AppConfigReduxUtil,
  AppConstants,
  AppLogsConsoleReduxUtil,
  CalibrationUrl,
  getEnv,
  CONFIG_PANEL_ALLOWED_CONFIG_TYPES_ITS,
  LoaderDisplay,
  ReduxUtils,
  UserMessage,
  withLoaderDisplay,
  readJson,
  Provider,
  connect,
  combineReducers,
  createStore,
  MapSidebarControls,
} from 'common/map';

import MainView from './page-builder/MainView';
import MetaDataReduxUtil from './utils/MetaDataReduxUtil';
import UiStateReduxUtil from './utils/UiStateReduxUtil';
import classes from './App.module.css';

export interface TrafficLightSidebarControlHandlers {
  controlsComponent: React.ReactNode;
}

export interface TrafficLightData {
  systemStatus?: string;
  apiUrl?: string | null;
  mediaStreamingUrl?: string | null;
  googleMapsApiKey?: string | null;
  uiConfigUrl?: string | null;
}

export interface TrafficLightComponentProps {
  theme?: 'light' | 'dark';
  className?: string;
  style?: React.CSSProperties;
  warehouseData?: TrafficLightData | null;
  mapData?: TrafficLightData | null;
  renderControlsInLeftSidebar?: boolean;
  onControlsReady?: (handlers: TrafficLightSidebarControlHandlers) => void;
  isActive?: boolean;
  registerChatAnswerHandler?: (handler: (answer: string) => boolean | void) => void | (() => void);
  registerSidebarChatEventSubscriber?: (
    handler: (event: { type: 'messageSubmitted' } | { type: 'answerComplete' }) => void,
  ) => void | (() => void);
}

const MainViewWithLoaderDisplay = withLoaderDisplay(MainView);

const createTrafficLightStore = () =>
  createStore(
    combineReducers({
      [AppConstants.APP_LOGS_CONSOLE_NAME]: AppLogsConsoleReduxUtil.slice.reducer,
      appConfig: AppConfigReduxUtil.slice.reducer,
      metadata: MetaDataReduxUtil.slice.reducer,
      uiState: UiStateReduxUtil.slice.reducer,
    }),
  );

const DEFAULT_INITIAL_USER_MESSAGE = 'Loading map…';

type AppInnerProps = TrafficLightComponentProps & {
  sidebarControls: React.ReactNode;
  metadata?: { cityConfig?: { name?: string } };
  updateAppConfig: (config: unknown) => void;
  updateMetaData: (data: unknown) => void;
  updateUiState: (state: unknown) => void;
  addLogToAppLogs?: (message: unknown) => void;
};

const TrafficLightAppInner: React.FC<AppInnerProps> = ({
  theme = 'light',
  className = '',
  style,
  warehouseData,
  mapData,
  renderControlsInLeftSidebar = false,
  onControlsReady,
  sidebarControls,
  metadata,
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
          data?.uiConfigUrl || '/traffic-light/default-ui-config.json';
        const configResponse = await fetch(configUrl);
        const configData = await readJson(configResponse) as {
          uiDelaySeconds?: number;
          defaultCorridor?: string;
        };
        if (cancelled) return;
        updateAppConfig(configData);
        const uiDelayMs = (configData?.uiDelaySeconds || 0) * 1000;
        updateUiState({
          appStartTimeMs: Date.now() - uiDelayMs,
          ...(configData?.defaultCorridor
            ? { selectedCorridor: configData.defaultCorridor }
            : {}),
        });

        const calibrationResponse = await fetch(CalibrationUrl);
        const calibrationData = await readJson(calibrationResponse);
        if (cancelled) return;
        updateMetaData(calibrationData);
        setUserMessage(null);
        setLoadState(LoaderDisplay.TYPE.LOAD_SUCCESS);
      } catch (error) {
        fail('Fetch traffic-light configuration', error);
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
          <MainViewWithLoaderDisplay
            loaderDisplayType={
              loadState === LoaderDisplay.TYPE.LOAD_SUCCESS &&
              !metadata?.cityConfig?.name
                ? LoaderDisplay.TYPE.LOADING
                : loadState
            }
            loaderDisplayUserMessage={userMessage}
            loaderDisplaySize={LoaderDisplay.SIZE.SMALL}
            loaderAfterSuccessDelayMs={0}
          />
        </div>
      </div>
    </ThemeProvider>
  );
};

const ConnectedTrafficLightApp = connect(
  (state: { metadata?: { cityConfig?: { name?: string } } }) => ({
    metadata: state.metadata,
  }),
  ReduxUtils.combineDispatchToProps(
    AppLogsConsoleReduxUtil.slice.actions,
    AppConfigReduxUtil.slice.actions,
    MetaDataReduxUtil.slice.actions,
    UiStateReduxUtil.slice.actions,
  ),
)(TrafficLightAppInner);

export const TrafficLightComponent: React.FC<TrafficLightComponentProps> = (props) => {
  const store = useMemo(() => createTrafficLightStore(), []);
  const sidebarControls = useMemo(
    () => (
      <Provider store={store}>
        <MapSidebarControls
          configTypes={CONFIG_PANEL_ALLOWED_CONFIG_TYPES_ITS}
          theme={props.theme}
        />
      </Provider>
    ),
    [store, props.theme],
  );
  return (
    <Provider store={store}>
      <ConnectedTrafficLightApp {...props} sidebarControls={sidebarControls} />
    </Provider>
  );
};

export default TrafficLightComponent;
