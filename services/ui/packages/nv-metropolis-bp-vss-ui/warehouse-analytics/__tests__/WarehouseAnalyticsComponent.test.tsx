// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import React from 'react';
import { render, screen } from '@testing-library/react';
import { WarehouseAnalyticsComponent } from '../lib-src/WarehouseAnalyticsComponent';

jest.mock('../lib-src/page-builder/BuildingsLevelView', () => ({
  __esModule: true,
  default: () => null,
}));

jest.mock('../lib-src/App.module.css', () => ({
  placeContent: 'placeContent',
}));

jest.mock('common/warehouse', () => {
  const ReactLib = require('react');

  class UserMessage {
    static SRC_TYPE = { GENERIC: 'generic', HTTP_ERROR: 'http-error', AXIOS_ERROR: 'http-error' };
    static MESSAGE_LEVEL = { INFO: 'Info', ERROR: 'Error' };
    shortMessage: string;
    constructor(_srcType: string, _level: string, srcTitle: string, errorObj: unknown) {
      this.shortMessage = `${srcTitle}: ${String(errorObj)}`;
    }
    getShortString() {
      return this.shortMessage;
    }
    getLongString() {
      return this.shortMessage;
    }
  }

  const LoaderDisplay = ({ userMessage }: { userMessage?: { getShortString?: () => string } }) =>
    ReactLib.createElement('div', null, userMessage?.getShortString?.() || 'loading');
  LoaderDisplay.TYPE = { LOADING: 0, LOAD_FAILURE: 1, LOAD_SUCCESS: 2 };
  LoaderDisplay.SIZE = { SMALL: 0, MEDIUM: 1, LARGE: 2 };

  const emptySlice = {
    reducer: (state = {}) => state,
    actions: {
      updateAppConfig: () => ({ type: 'appConfig/update' }),
      updateMetaData: () => ({ type: 'metadata/update' }),
      updateUiState: () => ({ type: 'uiState/update' }),
      addLogToAppLogs: () => ({ type: 'logs/add' }),
    },
  };

  return {
    readJson: jest.fn(),
    getEnv: jest.fn(),
    CalibrationUrl: 'http://example.test/config/calibration',
    CONFIG_PANEL_ALLOWED_CONFIG_TYPES_DEFAULT: ['calibration'],
    ConfigFileActionsPanel: () => null,
    LoaderDisplay,
    ReduxUtils: {
      combineDispatchToProps: (...slices: Array<Record<string, unknown>>) =>
        Object.assign({}, ...slices),
    },
    UploadImagesPanel: () => null,
    UserMessage,
    withLoaderDisplay: (Comp: React.ComponentType<any>) => (props: any) => {
      if (props.loaderDisplayType === LoaderDisplay.TYPE.LOADING) {
        return ReactLib.createElement(LoaderDisplay, {
          userMessage: props.loaderDisplayUserMessage,
        });
      }
      return ReactLib.createElement(Comp, props);
    },
    AppConfigReduxUtil: { slice: emptySlice },
    AppLogsConsoleReduxUtil: { slice: emptySlice },
    AppConstants: { APP_LOGS_CONSOLE_NAME: 'AppLogs' },
  };
});

describe('WarehouseAnalyticsComponent', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    global.fetch = jest.fn(() => new Promise(() => undefined)) as typeof fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('renders the warehouse analytics surface while configuration loads', () => {
    const { container } = render(
      <WarehouseAnalyticsComponent theme="dark" warehouseData={{ apiUrl: 'http://example.test' }} />,
    );
    expect(container.firstChild).toHaveClass('bg-black');
    expect(screen.getByText(/invoke app/i)).toBeInTheDocument();
  });
});
