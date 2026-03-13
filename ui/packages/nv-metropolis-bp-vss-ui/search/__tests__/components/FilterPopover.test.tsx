// SPDX-License-Identifier: MIT
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { FilterDialog } from '../../lib-src/components/FilterPopover';

jest.mock('@nemo-agent-toolkit/ui');

const defaultProps = {
  isOpen: true,
  isDark: false,
  handleConfirm: jest.fn(),
  close: jest.fn(),
  streams: [
    { name: 'Camera-1', type: 'sensor_file' },
    { name: 'Camera-2', type: 'sensor_file' },
    { name: 'RTSP-1', type: 'sensor_rtsp' },
  ],
  filterParams: {
    startDate: null,
    endDate: null,
    videoSources: [],
    similarity: 0,
    topK: 10,
  },
  setFilterParams: jest.fn(),
  containerRef: React.createRef<HTMLDivElement>(),
  disabled: false,
  sourceType: 'video_file',
};

describe('FilterDialog', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('returns null when isOpen is false', () => {
    const { container } = render(<FilterDialog {...defaultProps} isOpen={false} />);
    expect(container.innerHTML).toBe('');
  });

  it('renders when isOpen is true', () => {
    render(<FilterDialog {...defaultProps} />);
    expect(screen.getByText('From:')).toBeInTheDocument();
    expect(screen.getByText('To:')).toBeInTheDocument();
    expect(screen.getByText('Video sources:')).toBeInTheDocument();
    expect(screen.getByText('Min Cosine Similarity:')).toBeInTheDocument();
  });

  it('renders Show top K Results label with required marker', () => {
    render(<FilterDialog {...defaultProps} />);
    expect(screen.getByText(/Show top K Results/)).toBeInTheDocument();
    expect(screen.getByText('*')).toBeInTheDocument();
  });

  it('renders Apply and Cancel buttons', () => {
    render(<FilterDialog {...defaultProps} />);
    expect(screen.getByText('Apply')).toBeInTheDocument();
    expect(screen.getByText('Cancel')).toBeInTheDocument();
  });

  it('calls handleConfirm with pending params when Apply is clicked', () => {
    const handleConfirm = jest.fn();
    render(<FilterDialog {...defaultProps} handleConfirm={handleConfirm} />);

    fireEvent.click(screen.getByText('Apply'));
    expect(handleConfirm).toHaveBeenCalledWith(defaultProps.filterParams);
  });

  it('calls close when Cancel is clicked', () => {
    const close = jest.fn();
    render(<FilterDialog {...defaultProps} close={close} />);

    fireEvent.click(screen.getByText('Cancel'));
    expect(close).toHaveBeenCalledTimes(1);
  });

  it('calls handleConfirm on Enter key', () => {
    const handleConfirm = jest.fn();
    render(<FilterDialog {...defaultProps} handleConfirm={handleConfirm} />);

    const popover = document.querySelector('[style*="position"]');
    if (popover) {
      fireEvent.keyDown(popover, { key: 'Enter' });
      expect(handleConfirm).toHaveBeenCalled();
    }
  });

  it('renders with dark theme styles', () => {
    const { container } = render(<FilterDialog {...defaultProps} isDark={true} />);
    const popover = container.firstChild as HTMLElement;
    expect(popover).toBeTruthy();
    // jsdom converts hex to rgb
    expect(popover.style.backgroundColor).toBe('rgb(26, 29, 36)');
  });

  it('renders with light theme styles', () => {
    const { container } = render(<FilterDialog {...defaultProps} isDark={false} />);
    const popover = container.firstChild as HTMLElement;
    expect(popover).toBeTruthy();
    expect(popover.style.backgroundColor).toBe('rgb(255, 255, 255)');
  });

  it('resets pending params when dialog is closed and reopened', () => {
    const handleConfirm = jest.fn();
    const newFilterParams = { ...defaultProps.filterParams, similarity: 0.5, topK: 20 };

    const { rerender } = render(
      <FilterDialog {...defaultProps} handleConfirm={handleConfirm} />
    );

    // Close the dialog
    rerender(
      <FilterDialog {...defaultProps} isOpen={false} filterParams={newFilterParams} handleConfirm={handleConfirm} />
    );

    // Reopen with updated filterParams
    rerender(
      <FilterDialog {...defaultProps} isOpen={true} filterParams={newFilterParams} handleConfirm={handleConfirm} />
    );

    fireEvent.click(screen.getByText('Apply'));
    expect(handleConfirm).toHaveBeenCalledWith(newFilterParams);
  });

  it('uses portal when triggerRef is provided', () => {
    const triggerRef = React.createRef<HTMLDivElement>();
    const triggerDiv = document.createElement('div');
    document.body.appendChild(triggerDiv);
    (triggerRef as any).current = triggerDiv;
    triggerDiv.getBoundingClientRect = jest.fn(() => ({
      bottom: 100,
      left: 50,
      top: 60,
      right: 150,
      width: 100,
      height: 40,
      x: 50,
      y: 60,
      toJSON: () => {},
    }));

    render(<FilterDialog {...defaultProps} triggerRef={triggerRef} />);

    // The popover should be portalled to document.body
    const popoverInBody = document.body.querySelector('[style*="position: fixed"]');
    expect(popoverInBody).toBeInTheDocument();

    document.body.removeChild(triggerDiv);
  });
});
