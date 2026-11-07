// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import MetaDataReduxUtil from '../lib-src/utils/MetaDataReduxUtil';
import { VideoElementDefaultConfig } from '../lib-src/utils/Utils';

describe('VideoElementDefaultConfig', () => {
  it('has expected default dimensions', () => {
    expect(VideoElementDefaultConfig.htmlTagOptions.height).toBe('16.875vw');
    expect(VideoElementDefaultConfig.htmlTagOptions.width).toBe('30vw');
  });
});

describe('MetaDataReduxUtil', () => {
  it('exports a valid Redux slice', () => {
    expect(MetaDataReduxUtil.slice).toBeDefined();
    expect(MetaDataReduxUtil.slice.name).toBe('metadata');
    expect(typeof MetaDataReduxUtil.slice.reducer).toBe('function');
  });

  it('has updateMetaData action', () => {
    expect(MetaDataReduxUtil.slice.actions.updateMetaData).toBeDefined();
    expect(typeof MetaDataReduxUtil.slice.actions.updateMetaData).toBe('function');
  });

  it('has middleware configuration', () => {
    expect(MetaDataReduxUtil.middleware).toBeDefined();
    expect(typeof MetaDataReduxUtil.middleware).toBe('function');
  });
});
