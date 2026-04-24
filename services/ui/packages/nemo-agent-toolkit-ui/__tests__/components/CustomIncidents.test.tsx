import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { CustomIncidents } from '@/components/Markdown/CustomIncidents';

const mockCopyToClipboard = jest.fn();
const mockFormatTimestamp = jest.fn((timestamp: string) => `formatted-${timestamp}`);

jest.mock('@aiqtoolkit-ui/common', () => ({
  VideoModal: ({ isOpen, videoUrl, title, onClose }: { isOpen: boolean; videoUrl: string; title: string; onClose: () => void }) =>
    isOpen ? (
      <div data-testid="video-modal">
        <span data-testid="video-url">{videoUrl}</span>
        <span data-testid="video-title">{title}</span>
        <button onClick={onClose}>Close</button>
      </div>
    ) : null,
  copyToClipboard: (...args: string[]) => mockCopyToClipboard(...args),
  formatTimestamp: (...args: string[]) => mockFormatTimestamp(...args),
}));

function createIncident(overrides: Record<string, unknown> = {}) {
  return {
    'Alert Title': 'Test Alert',
    'Clip Information': {
      Timestamp: '2024-01-01T00:00:00Z',
      Stream: 'stream-1',
      Alerts: 'PPE violation',
      snapshot_url: 'http://example.com/snap.jpg',
      video_url: 'http://example.com/video.mp4',
      'CV Metadata': {
        Box_on_floor: 'false',
        Number_of_people: '3',
        PPE: 'missing',
      },
    },
    'Alert Details': {
      'Alert Triggered': 'PPE Violation Detected',
      Validation: true,
      'Alert Description': 'A worker was detected without a hard hat.',
    },
    ...overrides,
  };
}

function createPayload(count: number, message?: string) {
  return {
    incidents: Array.from({ length: count }, () => createIncident()),
    ...(message !== undefined ? { message } : {}),
  };
}

describe('CustomIncidents', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  // =========================================================================
  // EMPTY / MISSING DATA
  // =========================================================================
  describe('Empty and missing data', () => {
    it.each([
      ['payload is undefined', undefined],
      ['payload has no incidents key', {} as any],
      ['incidents array is empty', { incidents: [] }],
      ['incidents is not an array', { incidents: 'bad' as any }],
    ])('renders "No incidents found" when %s', (_label, payload) => {
      render(<CustomIncidents payload={payload} />);
      expect(screen.getByText('No incidents found')).toBeInTheDocument();
    });
  });

  // =========================================================================
  // BASIC RENDERING
  // =========================================================================
  describe('Basic rendering', () => {
    it('renders incident headers with alert info and formatted timestamp', () => {
      render(<CustomIncidents payload={createPayload(1)} />);

      expect(screen.getByText(/Alert Triggered 1:/)).toBeInTheDocument();
      expect(screen.getByText(/PPE Violation Detected/)).toBeInTheDocument();
      expect(mockFormatTimestamp).toHaveBeenCalledWith('2024-01-01T00:00:00Z');
    });

    it('renders the summary message when provided', () => {
      render(<CustomIncidents payload={createPayload(1, 'Summary text here')} />);
      expect(screen.getByText('Summary text here')).toBeInTheDocument();
    });

    it('does not render message section when message is empty string', () => {
      render(<CustomIncidents payload={createPayload(1, '  ')} />);
      expect(screen.queryByText(/Summary/)).not.toBeInTheDocument();
    });

    it('does not render message section when message is not provided', () => {
      render(<CustomIncidents payload={createPayload(1)} />);
      const messageContainer = screen.queryByText('Summary text here');
      expect(messageContainer).not.toBeInTheDocument();
    });
  });

  // =========================================================================
  // EXPAND / COLLAPSE
  // =========================================================================
  describe('Expand and collapse', () => {
    it('expands incident details on header click', () => {
      render(<CustomIncidents payload={createPayload(1)} />);

      expect(screen.queryByText('Clip Information')).not.toBeInTheDocument();

      fireEvent.click(screen.getByText(/Alert Triggered 1:/));

      expect(screen.getByText('Clip Information')).toBeInTheDocument();
      expect(screen.getByText('Alert Details')).toBeInTheDocument();
    });

    it('collapses incident details when clicking expanded header again', () => {
      render(<CustomIncidents payload={createPayload(1)} />);

      const header = screen.getByText(/Alert Triggered 1:/);
      fireEvent.click(header);
      expect(screen.getByText('Clip Information')).toBeInTheDocument();

      fireEvent.click(header);
      expect(screen.queryByText('Clip Information')).not.toBeInTheDocument();
    });

    it('expands Clip Information sub-section', () => {
      render(<CustomIncidents payload={createPayload(1)} />);

      fireEvent.click(screen.getByText(/Alert Triggered 1:/));
      fireEvent.click(screen.getByText('Clip Information'));

      expect(screen.getByText(/"Stream": "stream-1"/)).toBeInTheDocument();
    });

    it('expands Alert Details sub-section', () => {
      render(<CustomIncidents payload={createPayload(1)} />);

      fireEvent.click(screen.getByText(/Alert Triggered 1:/));
      fireEvent.click(screen.getByText('Alert Details'));

      expect(
        screen.getByText(/"Alert Triggered": "PPE Violation Detected"/),
      ).toBeInTheDocument();
    });

    it('collapses sub-sections when parent is collapsed', () => {
      render(<CustomIncidents payload={createPayload(1)} />);

      const header = screen.getByText(/Alert Triggered 1:/);

      fireEvent.click(header);
      fireEvent.click(screen.getByText('Clip Information'));
      expect(screen.getByText(/"Stream": "stream-1"/)).toBeInTheDocument();

      fireEvent.click(header);
      fireEvent.click(header);

      expect(screen.queryByText(/"Stream": "stream-1"/)).not.toBeInTheDocument();
    });

    it('switching to another incident collapses sub-sections', () => {
      render(<CustomIncidents payload={createPayload(2)} />);

      fireEvent.click(screen.getByText(/Alert Triggered 1:/));
      fireEvent.click(screen.getByText('Clip Information'));

      fireEvent.click(screen.getByText(/Alert Triggered 2:/));

      const clipInfoSections = screen.getAllByText('Clip Information');
      expect(clipInfoSections).toHaveLength(1);
      expect(screen.queryByText(/"Stream": "stream-1"/)).not.toBeInTheDocument();
    });
  });

  // =========================================================================
  // VIDEO MODAL
  // =========================================================================
  describe('Video modal', () => {
    it('opens video modal when play button is clicked', () => {
      render(<CustomIncidents payload={createPayload(1)} />);

      expect(screen.queryByTestId('video-modal')).not.toBeInTheDocument();

      const playButton = screen.getByRole('button', { name: '' });
      fireEvent.click(playButton);

      expect(screen.getByTestId('video-modal')).toBeInTheDocument();
      expect(screen.getByTestId('video-url')).toHaveTextContent(
        'http://example.com/video.mp4',
      );
    });

    it('closes video modal via onClose callback', () => {
      render(<CustomIncidents payload={createPayload(1)} />);

      const playButton = screen.getByRole('button', { name: '' });
      fireEvent.click(playButton);
      expect(screen.getByTestId('video-modal')).toBeInTheDocument();

      fireEvent.click(screen.getByText('Close'));
      expect(screen.queryByTestId('video-modal')).not.toBeInTheDocument();
    });

    it('does not open video modal when video_url is missing', () => {
      const incident = createIncident();
      incident['Clip Information'].video_url = undefined;
      render(<CustomIncidents payload={{ incidents: [incident] }} />);

      const playButton = screen.getByRole('button', { name: '' });
      fireEvent.click(playButton);

      expect(screen.queryByTestId('video-modal')).not.toBeInTheDocument();
    });
  });

  // =========================================================================
  // COPY TO CLIPBOARD
  // =========================================================================
  describe('Copy to clipboard', () => {
    it('copies Clip Information JSON when copy button is clicked', () => {
      render(<CustomIncidents payload={createPayload(1)} />);

      fireEvent.click(screen.getByText(/Alert Triggered 1:/));

      const copyButtons = screen.getAllByTitle('Copy Clip Information JSON');
      fireEvent.click(copyButtons[0]);

      expect(mockCopyToClipboard).toHaveBeenCalledWith(
        JSON.stringify(createIncident()['Clip Information'], null, 2),
      );
    });

    it('copies Alert Details JSON when copy button is clicked', () => {
      render(<CustomIncidents payload={createPayload(1)} />);

      fireEvent.click(screen.getByText(/Alert Triggered 1:/));

      const copyButtons = screen.getAllByTitle('Copy Alert Details JSON');
      fireEvent.click(copyButtons[0]);

      expect(mockCopyToClipboard).toHaveBeenCalledWith(
        JSON.stringify(createIncident()['Alert Details'], null, 2),
      );
    });
  });

  // =========================================================================
  // VIEW MORE / VIEW LESS (pagination)
  // =========================================================================
  describe('View more / view less', () => {
    it('initially shows only 3 incidents', () => {
      render(<CustomIncidents payload={createPayload(5)} />);

      const headers = screen.getAllByText(/Alert Triggered \d+:/);
      expect(headers).toHaveLength(3);
    });

    it('shows "Show more" button with remaining count', () => {
      render(<CustomIncidents payload={createPayload(5)} />);

      expect(screen.getByText('Show more (2 more)')).toBeInTheDocument();
    });

    it('loads more incidents on "Show more" click', () => {
      render(<CustomIncidents payload={createPayload(7)} />);

      fireEvent.click(screen.getByText(/Show more/));

      const headers = screen.getAllByText(/Alert Triggered \d+:/);
      expect(headers).toHaveLength(6);
      expect(screen.getByText('Show more (1 more)')).toBeInTheDocument();
    });

    it('shows "Show less" button after loading more', () => {
      render(<CustomIncidents payload={createPayload(5)} />);

      fireEvent.click(screen.getByText(/Show more/));

      expect(screen.getByText('Show less')).toBeInTheDocument();
    });

    it('resets to initial count on "Show less" click', () => {
      render(<CustomIncidents payload={createPayload(5)} />);

      fireEvent.click(screen.getByText(/Show more/));
      fireEvent.click(screen.getByText('Show less'));

      const headers = screen.getAllByText(/Alert Triggered \d+:/);
      expect(headers).toHaveLength(3);
    });

    it('does not show pagination buttons when incidents <= 3', () => {
      render(<CustomIncidents payload={createPayload(3)} />);

      expect(screen.queryByText(/Show more/)).not.toBeInTheDocument();
      expect(screen.queryByText('Show less')).not.toBeInTheDocument();
    });
  });

  // =========================================================================
  // MEMO BEHAVIOR
  // =========================================================================
  describe('Memo comparison', () => {
    it('skips re-render when the same payload reference is passed', () => {
      const payload = createPayload(1);
      const { rerender } = render(<CustomIncidents payload={payload} />);

      const callCountAfterFirstRender = mockFormatTimestamp.mock.calls.length;

      rerender(<CustomIncidents payload={payload} />);

      expect(mockFormatTimestamp.mock.calls.length).toBe(callCountAfterFirstRender);
    });

    it('re-renders when a new payload reference with the same shape is passed', () => {
      const payload = createPayload(1);
      const { rerender } = render(<CustomIncidents payload={payload} />);

      const callCountAfterFirstRender = mockFormatTimestamp.mock.calls.length;

      rerender(<CustomIncidents payload={{ ...payload }} />);

      expect(mockFormatTimestamp.mock.calls.length).toBeGreaterThan(callCountAfterFirstRender);
    });
  });
});
