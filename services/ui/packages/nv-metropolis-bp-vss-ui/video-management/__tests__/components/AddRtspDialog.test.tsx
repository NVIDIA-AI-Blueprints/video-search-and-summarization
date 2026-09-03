// SPDX-License-Identifier: MIT
import React from 'react';
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';
import { AddRtspDialog } from '../../lib-src/components/AddRtspDialog';

const mockAddRtspStream = jest.fn(async () => ({ sensorId: 'sensor-new' }));

jest.mock('../../lib-src/rtspStream', () => ({
  addRtspStream: (...args: unknown[]) => mockAddRtspStream(...(args as [])),
}));

const RTSP_URL = 'rtsp://cam.example.com:554/cam01';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const defaultProps = {
  isOpen: true,
  vstApiUrl: 'https://vst.example.com/vst/api',
  onClose: jest.fn(),
  onSuccess: jest.fn(),
  onAwaitStream: jest.fn(async () => ({ found: true })),
};

function renderDialog(props: Partial<Parameters<typeof AddRtspDialog>[0]> = {}) {
  const merged = { ...defaultProps, ...props };
  const utils = render(<AddRtspDialog {...merged} />);
  return { ...utils, props: merged };
}

function fillUrl(url = RTSP_URL) {
  // Sensor Name auto-fills from the URL's last path segment
  fireEvent.change(screen.getByPlaceholderText(/^rtsp:\/\/cam-warehouse/), {
    target: { value: url },
  });
}

const submitButton = () => screen.getByRole('button', { name: /Add RTSP|Waiting for VST|Adding|Retry/ });

// The add call answers with a sensorId before VST lists the sensor. Closing on
// that answer left the grid without the stream the user just added, until a
// manual refresh or a tab switch.
describe('AddRtspDialog — waits for VST to list the added stream', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockAddRtspStream.mockResolvedValue({ sensorId: 'sensor-new' });
  });

  it('stays open while VST has not listed the stream yet', async () => {
    const wait = deferred<{ found: boolean }>();
    const onAwaitStream = jest.fn(() => wait.promise);
    const { props } = renderDialog({ onAwaitStream });

    fillUrl();
    await act(async () => {
      fireEvent.click(submitButton());
    });

    expect(onAwaitStream).toHaveBeenCalledWith('sensor-new');
    expect(screen.getByTestId('add-rtsp-dialog')).toBeInTheDocument();
    expect(screen.getByTestId('add-rtsp-confirming')).toBeInTheDocument();
    expect(props.onClose).not.toHaveBeenCalled();
    expect(props.onSuccess).not.toHaveBeenCalled();

    await act(async () => {
      wait.resolve({ found: true });
    });

    expect(props.onClose).toHaveBeenCalledTimes(1);
    expect(props.onSuccess).toHaveBeenCalledTimes(1);
  });

  it('keeps the submit button busy for the whole wait, not just the add call', async () => {
    const wait = deferred<{ found: boolean }>();
    renderDialog({ onAwaitStream: jest.fn(() => wait.promise) });

    fillUrl();
    await act(async () => {
      fireEvent.click(submitButton());
    });

    expect(submitButton()).toBeDisabled();
    expect(submitButton()).toHaveTextContent('Waiting for VST...');

    await act(async () => {
      wait.resolve({ found: true });
    });
  });

  it('closes and reports success once VST lists the stream', async () => {
    const { props } = renderDialog();

    fillUrl();
    await act(async () => {
      fireEvent.click(submitButton());
    });

    await waitFor(() => expect(props.onClose).toHaveBeenCalledTimes(1));
    expect(props.onSuccess).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('add-rtsp-error')).not.toBeInTheDocument();
  });

  it('stays open with a retry message when VST never lists the stream', async () => {
    const { props } = renderDialog({ onAwaitStream: jest.fn(async () => ({ found: false })) });

    fillUrl();
    await act(async () => {
      fireEvent.click(submitButton());
    });

    expect(screen.getByTestId('add-rtsp-dialog')).toBeInTheDocument();
    expect(screen.getByTestId('add-rtsp-error')).toHaveTextContent(
      'VST accepted the stream but has not listed it yet. Retry to check again.',
    );
    expect(props.onClose).not.toHaveBeenCalled();
    expect(props.onSuccess).not.toHaveBeenCalled();
    expect(submitButton()).toHaveTextContent('Retry');
  });

  // Re-sending the add would come back as a duplicate URL, reading as a failure
  // for a sensor VST already holds.
  it('retries only the wait, never a second add, once VST accepted the sensor', async () => {
    const onAwaitStream = jest
      .fn<Promise<{ found: boolean }>, [string]>()
      .mockResolvedValueOnce({ found: false })
      .mockResolvedValueOnce({ found: true });
    const { props } = renderDialog({ onAwaitStream });

    fillUrl();
    await act(async () => {
      fireEvent.click(submitButton());
    });
    expect(screen.getByTestId('add-rtsp-error')).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(submitButton());
    });

    expect(mockAddRtspStream).toHaveBeenCalledTimes(1);
    expect(onAwaitStream).toHaveBeenNthCalledWith(2, 'sensor-new');
    expect(props.onClose).toHaveBeenCalledTimes(1);
    expect(props.onSuccess).toHaveBeenCalledTimes(1);
  });

  it('surfaces an add failure without waiting on a sensor it never got', async () => {
    mockAddRtspStream.mockRejectedValueOnce(
      new Error('{"error_code":"InvalidParameterError","error_message":"sensor exists"}'),
    );
    const onAwaitStream = jest.fn(async () => ({ found: true }));
    const { props } = renderDialog({ onAwaitStream });

    fillUrl();
    await act(async () => {
      fireEvent.click(submitButton());
    });

    expect(onAwaitStream).not.toHaveBeenCalled();
    expect(screen.getByTestId('add-rtsp-error')).toHaveTextContent(
      'A sensor with this RTSP URL already exists.',
    );
    expect(props.onClose).not.toHaveBeenCalled();
    // A failed add leaves nothing accepted, so the next click must add again
    expect(submitButton()).toHaveTextContent('Add RTSP');
  });

  it('blocks dismissal during the add call but allows abandoning the wait', async () => {
    const wait = deferred<{ found: boolean }>();
    const add = deferred<{ sensorId: string }>();
    mockAddRtspStream.mockImplementationOnce(() => add.promise);
    const { props } = renderDialog({ onAwaitStream: jest.fn(() => wait.promise) });

    fillUrl();
    await act(async () => {
      fireEvent.click(submitButton());
    });

    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();

    await act(async () => {
      add.resolve({ sensorId: 'sensor-new' });
    });

    const cancel = screen.getByRole('button', { name: 'Cancel' });
    expect(cancel).not.toBeDisabled();
    await act(async () => {
      fireEvent.click(cancel);
    });
    expect(props.onClose).toHaveBeenCalledTimes(1);

    // The abandoned wait must not write back into a dialog the user closed
    await act(async () => {
      wait.resolve({ found: false });
    });
    expect(props.onSuccess).not.toHaveBeenCalled();
  });

  it('closes on success when no wait callback is wired', async () => {
    const { props } = renderDialog({ onAwaitStream: undefined });

    fillUrl();
    await act(async () => {
      fireEvent.click(submitButton());
    });

    await waitFor(() => expect(props.onClose).toHaveBeenCalledTimes(1));
    expect(props.onSuccess).toHaveBeenCalledTimes(1);
  });

  it('rejects an invalid URL before calling VST', async () => {
    renderDialog();

    fillUrl('http://cam.example.com/stream');
    await act(async () => {
      fireEvent.click(submitButton());
    });

    expect(mockAddRtspStream).not.toHaveBeenCalled();
    expect(screen.getByTestId('add-rtsp-error')).toHaveTextContent(
      'RTSP URL must start with "rtsp://".',
    );
  });
});
