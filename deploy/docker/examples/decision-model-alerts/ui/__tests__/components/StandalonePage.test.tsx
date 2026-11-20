// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import Monitor, { getServerSideProps } from '../../pages/index';
jest.mock('../../components/D1AgentPanel', () => ({ __esModule: true, default: () => <section aria-label="Monitor content" /> }));
it('provides the VSS shell independently of the core UI', () => {
  render(<Monitor embedded={false} initialTheme="dark" />);
  expect(screen.getByRole('banner')).toHaveTextContent('VSS BLUEPRINT');
  expect(screen.getByRole('region', {name: 'Monitor content'})).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: 'Switch to light theme'}));
  expect(document.documentElement).not.toHaveClass('dark');
});
it('omits only the duplicate shell when embedded', () => {
  render(<Monitor embedded initialTheme="dark" />);
  expect(screen.queryByRole('banner')).not.toBeInTheDocument();
  expect(screen.getByRole('region', {name: 'Monitor content'})).toBeInTheDocument();
});
it('accepts only explicit embed and theme choices', async () => {
  expect(await getServerSideProps({query: {embed: '1', theme: 'light'}} as any)).toEqual({props: {embedded: true, initialTheme: 'light'}});
  expect(await getServerSideProps({query: {embed: 'http://example.com', theme: 'other'}} as any)).toEqual({props: {embedded: false, initialTheme: 'dark'}});
});
