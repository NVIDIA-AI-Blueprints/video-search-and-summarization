// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SearchComponent } from '../../lib-src/SearchComponent';
import type { SearchData, SearchSidebarControlHandlers } from '../../lib-src/types';

jest.mock('../../lib-src/hooks/useSearchByImage', () => ({
  useSearchByImage: () => ({
    searchByImageActive: false, searchByImageLoading: false, searchByImageError: null,
    searchByImageFrameData: null, startSearchByImage: jest.fn(), cancelSearchByImage: jest.fn(),
  }),
}));
jest.mock('common', () => ({
  ...jest.requireActual('common'),
  useVideoModal: () => ({
    videoModal: { isOpen: false, videoUrl: '', title: '' },
    openVideoModal: jest.fn(), closeVideoModal: jest.fn(),
  }),
}));
jest.mock('next/dynamic', () => () => () => null);

const sensors = [
  { sensorId: 'file-id', name: 'factory.mp4', type: 'sensor_file', state: 'online' },
  { sensorId: 'live-id', name: 'live-camera', type: 'sensor_rtsp', state: 'online' },
];
const result = (sensorId: string, name: string): SearchData => ({
  sensor_id: sensorId, video_name: name, description: 'returned scene',
  start_time: '2025-01-01T00:00:00Z', end_time: '2025-01-01T00:00:10Z',
  similarity: 0.9, screenshot_url: 'http://vst.test/vst/api/frame.png', object_ids: [],
});
const data = { systemStatus: 'ok', vstApiUrl: 'http://vst.test/vst/api' };

function Harness({ sidebar, register, active = true }: {
  sidebar: boolean;
  register: (handler: (answer: string) => boolean | void) => () => void;
  active?: boolean;
}) {
  const [controls, setControls] = React.useState<React.ReactNode>(null);
  const onControlsReady = React.useCallback((handlers: SearchSidebarControlHandlers) => {
    setControls(handlers.controlsComponent);
  }, []);
  return <>
    {sidebar && controls}
    <SearchComponent
      searchData={data}
      isActive={active}
      renderControlsInLeftSidebar={sidebar}
      onControlsReady={onControlsReady}
      registerChatAnswerHandler={register}
    />
  </>;
}

describe('new chat Search results reconcile source scope', () => {
  let answer: ((value: string) => boolean | void) | undefined;
  const register = (handler: (value: string) => boolean | void) => {
    answer = handler;
    return () => undefined;
  };

  beforeEach(() => {
    answer = undefined;
    const stored: Record<string, string> = { vss_search_sourceType: 'rtsp' };
    (sessionStorage.getItem as jest.Mock).mockImplementation((key: string) => stored[key] ?? null);
    (sessionStorage.setItem as jest.Mock).mockImplementation((key: string, value: string) => { stored[key] = value; });
    global.fetch = jest.fn(async () => ({
      ok: true, json: async () => sensors,
    })) as jest.Mock;
  });

  it.each([false, true])('shows archive results from a saved RTSP selection (sidebar=%s)', async (sidebar) => {
    const view = render(<Harness sidebar={sidebar} register={register} />);
    await act(async () => { await Promise.resolve(); });
    await waitFor(() => expect(screen.getByTestId('search-source-type')).toHaveValue('rtsp'));

    act(() => { answer?.(JSON.stringify({ data: [result('file-id', 'factory.mp4')] })); });
    expect(screen.getByText('factory.mp4')).toBeInTheDocument();
    expect(screen.getByTestId('search-source-type')).toHaveValue('video_file');
    expect(sessionStorage.setItem).toHaveBeenCalledWith('vss_search_sourceType', 'video_file');

    fireEvent.change(screen.getByTestId('search-source-type'), { target: { value: 'rtsp' } });
    expect(screen.queryByText('factory.mp4')).not.toBeInTheDocument();
    view.rerender(<Harness sidebar={sidebar} register={register} active={false} />);
    view.rerender(<Harness sidebar={sidebar} register={register} active />);
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByTestId('search-source-type')).toHaveValue('rtsp');
    expect(screen.queryByText('factory.mp4')).not.toBeInTheDocument();
  });

  it('shows mixed-source results with All sources and honors subsequent manual filtering', async () => {
    render(<Harness sidebar register={register} />);
    await act(async () => { await Promise.resolve(); });
    act(() => { answer?.(JSON.stringify({ data: [
      result('file-id', 'factory.mp4'), result('live-id', 'live-camera'),
    ] })); });
    expect(screen.getByTestId('search-source-type')).toHaveValue('all');
    expect(screen.getByText('factory.mp4')).toBeInTheDocument();
    expect(screen.getByText('live-camera')).toBeInTheDocument();

    fireEvent.change(screen.getByTestId('search-source-type'), { target: { value: 'rtsp' } });
    expect(screen.queryByText('factory.mp4')).not.toBeInTheDocument();
    expect(screen.getByText('live-camera')).toBeInTheDocument();
  });

  it('keeps results visible when VIOS source metadata is not yet available', async () => {
    (fetch as jest.Mock).mockResolvedValue({ ok: true, json: async () => [] });
    render(<Harness sidebar register={register} />);
    await act(async () => { await Promise.resolve(); });
    act(() => { answer?.(JSON.stringify({ data: [result('unknown-id', 'returned-archive.mp4')] })); });
    expect(screen.getByTestId('search-source-type')).toHaveValue('all');
    expect(screen.getByText('returned-archive.mp4')).toBeInTheDocument();
  });
});
