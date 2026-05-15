// SPDX-License-Identifier: MIT
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { DeleteConfirmDialog } from '../../lib-src/components/DeleteConfirmDialog';
import type { StreamInfo } from '../../lib-src/types';
import { makeStream, videoStream, rtspStream } from '../helpers/streamFixtures';

const noop = () => {};

const defaultProps = {
  isOpen: true,
  streams: [videoStream] as StreamInfo[],
  isDeleting: false,
  onCancel: noop,
  onConfirm: noop,
};

function renderDialog(props: Partial<Parameters<typeof DeleteConfirmDialog>[0]> = {}) {
  return render(<DeleteConfirmDialog {...defaultProps} {...props} />);
}

describe('DeleteConfirmDialog — visibility', () => {
  it('renders nothing when isOpen is false', () => {
    const { container } = renderDialog({ isOpen: false });
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByTestId('delete-confirm-dialog')).not.toBeInTheDocument();
  });

  it('renders the dialog when isOpen is true', () => {
    renderDialog();
    expect(screen.getByTestId('delete-confirm-dialog')).toBeInTheDocument();
    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
  });

  it('uses the fixed title "DELETE STREAMS/VIDEOS" regardless of stream type or count', () => {
    const { rerender } = renderDialog({ streams: [videoStream] });
    expect(screen.getByText('DELETE STREAMS/VIDEOS')).toBeInTheDocument();

    rerender(<DeleteConfirmDialog {...defaultProps} streams={[rtspStream]} />);
    expect(screen.getByText('DELETE STREAMS/VIDEOS')).toBeInTheDocument();

    rerender(<DeleteConfirmDialog {...defaultProps} streams={[videoStream, rtspStream]} />);
    expect(screen.getByText('DELETE STREAMS/VIDEOS')).toBeInTheDocument();
  });

  it('renders the primary confirmation message', () => {
    renderDialog();
    expect(screen.getByText('Are you sure you want to delete the following?')).toBeInTheDocument();
  });

  it('does not render the legacy red warning callout (removed per UX feedback)', () => {
    renderDialog();
    expect(
      screen.queryByText(/permanently removed from the Video Storage Toolkit/i),
    ).not.toBeInTheDocument();
  });

  it('exposes the dialog with accessible alertdialog semantics', () => {
    renderDialog();
    const dlg = screen.getByRole('alertdialog');
    expect(dlg).toHaveAttribute('aria-modal', 'true');
    expect(dlg).toHaveAttribute('aria-labelledby', 'delete-confirm-title');
    expect(dlg).toHaveAttribute('aria-describedby', 'delete-confirm-desc');
  });
});

describe('DeleteConfirmDialog — preview list', () => {
  it('renders one row per selected stream when count is at or below the preview cap', () => {
    const streams = [
      makeStream({ name: 'alpha', streamId: 'a' }),
      makeStream({ name: 'beta', streamId: 'b' }),
      makeStream({ name: 'gamma', streamId: 'c' }),
    ];
    renderDialog({ streams });

    expect(screen.getByText('alpha')).toBeInTheDocument();
    expect(screen.getByText('beta')).toBeInTheDocument();
    expect(screen.getByText('gamma')).toBeInTheDocument();
    expect(screen.queryByText(/more$/)).not.toBeInTheDocument();
  });

  it('caps preview at 5 names and shows "+ N more" footer for larger selections', () => {
    const streams = Array.from({ length: 8 }, (_, i) =>
      makeStream({ name: `cam-${i + 1}`, streamId: `s-${i + 1}` }),
    );
    renderDialog({ streams });

    // First five are visible
    for (let i = 1; i <= 5; i++) {
      expect(screen.getByText(`cam-${i}`)).toBeInTheDocument();
    }
    // Sixth onward are collapsed into the footer
    expect(screen.queryByText('cam-6')).not.toBeInTheDocument();
    expect(screen.getByText('+ 3 more')).toBeInTheDocument();
  });

  it('omits the preview list block entirely when there are no streams to display', () => {
    renderDialog({ streams: [] });

    // Body shell + message still render
    expect(screen.getByText('Are you sure you want to delete the following?')).toBeInTheDocument();
    // But there's no <ul> of items
    expect(screen.queryByRole('list')).not.toBeInTheDocument();
  });
});

describe('DeleteConfirmDialog — user interactions', () => {
  it('invokes onConfirm when the Confirm button is clicked', () => {
    const onConfirm = jest.fn();
    renderDialog({ onConfirm });

    fireEvent.click(screen.getByTestId('delete-confirm-button'));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('invokes onCancel when the Cancel button is clicked', () => {
    const onCancel = jest.fn();
    renderDialog({ onCancel });

    fireEvent.click(screen.getByText('Cancel'));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('invokes onCancel when the header × close button is clicked', () => {
    const onCancel = jest.fn();
    renderDialog({ onCancel });

    fireEvent.click(screen.getByLabelText('Close'));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('invokes onCancel when the backdrop is clicked', () => {
    const onCancel = jest.fn();
    const { container } = renderDialog({ onCancel });

    // Backdrop is the first sibling div with the bg-black/85 overlay class.
    const backdrop = container.querySelector('.bg-black\\/85') as HTMLElement;
    expect(backdrop).toBeTruthy();
    fireEvent.click(backdrop);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('invokes onCancel when Escape is pressed while the dialog is open', () => {
    const onCancel = jest.fn();
    renderDialog({ onCancel });

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('does not invoke onCancel on Escape when the dialog is closed (listener detached)', () => {
    const onCancel = jest.fn();
    renderDialog({ isOpen: false, onCancel });

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onCancel).not.toHaveBeenCalled();
  });
});

describe('DeleteConfirmDialog — isDeleting state', () => {
  it('shows "Confirm" label by default and "Deleting..." while a delete is in flight', () => {
    const { rerender } = renderDialog({ isDeleting: false });
    expect(screen.getByTestId('delete-confirm-button')).toHaveTextContent('Confirm');

    rerender(<DeleteConfirmDialog {...defaultProps} isDeleting={true} />);
    expect(screen.getByTestId('delete-confirm-button')).toHaveTextContent('Deleting...');
  });

  it('disables Confirm, Cancel, and × while deleting', () => {
    renderDialog({ isDeleting: true });

    expect(screen.getByTestId('delete-confirm-button')).toBeDisabled();
    expect(screen.getByText('Cancel')).toBeDisabled();
    expect(screen.getByLabelText('Close')).toBeDisabled();
  });

  it('ignores backdrop clicks while deleting so an in-flight request is not abandoned', () => {
    const onCancel = jest.fn();
    const { container } = renderDialog({ onCancel, isDeleting: true });

    const backdrop = container.querySelector('.bg-black\\/85') as HTMLElement;
    fireEvent.click(backdrop);
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('ignores Escape while deleting', () => {
    const onCancel = jest.fn();
    renderDialog({ onCancel, isDeleting: true });

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onCancel).not.toHaveBeenCalled();
  });
});

describe('DeleteConfirmDialog — streams snapshot on open', () => {
  // The parent (VideoManagementComponent) clears its `selectedStreams` set
  // mid-delete (after the API resolves, before refetch finishes). Without the
  // internal snapshot, the dialog's preview list would visibly empty out while
  // still mounted and read as "a second dialog flashing" to the user. These
  // tests pin that behaviour so it doesn't regress.

  it('keeps the preview list stable when the streams prop becomes empty mid-open', () => {
    const streams = [
      makeStream({ name: 'first', streamId: 's1' }),
      makeStream({ name: 'second', streamId: 's2' }),
    ];
    const { rerender } = renderDialog({ streams });

    expect(screen.getByText('first')).toBeInTheDocument();
    expect(screen.getByText('second')).toBeInTheDocument();

    // Parent clears selection while the dialog is still open.
    rerender(<DeleteConfirmDialog {...defaultProps} streams={[]} />);

    expect(screen.getByText('first')).toBeInTheDocument();
    expect(screen.getByText('second')).toBeInTheDocument();
  });

  it('re-snapshots when the dialog is closed and reopened with a new selection', () => {
    const firstBatch = [makeStream({ name: 'old-pick', streamId: 'old' })];
    const secondBatch = [makeStream({ name: 'new-pick', streamId: 'new' })];

    const { rerender } = renderDialog({ streams: firstBatch });
    expect(screen.getByText('old-pick')).toBeInTheDocument();

    // Close the dialog.
    rerender(<DeleteConfirmDialog {...defaultProps} isOpen={false} streams={firstBatch} />);
    // Reopen with a different selection.
    rerender(<DeleteConfirmDialog {...defaultProps} isOpen={true} streams={secondBatch} />);

    expect(screen.queryByText('old-pick')).not.toBeInTheDocument();
    expect(screen.getByText('new-pick')).toBeInTheDocument();
  });
});
