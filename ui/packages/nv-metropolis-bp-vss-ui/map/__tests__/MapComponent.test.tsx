// SPDX-License-Identifier: MIT
import { render, screen } from '@testing-library/react';
import { MapComponent } from '../lib-src/MapComponent';

describe('MapComponent', () => {
  it('shows error when mapUrl is not configured', () => {
    render(<MapComponent mapData={{ mapUrl: '' }} />);
    expect(
      screen.getByText(/environment variables/i),
    ).toBeInTheDocument();
  });

  it('shows error when mapUrl is invalid', () => {
    render(<MapComponent mapData={{ mapUrl: 'not-a-url' }} />);
    expect(
      screen.getByText(/Map URL is invalid/i),
    ).toBeInTheDocument();
  });

  it('renders loading state with a valid URL', () => {
    render(<MapComponent mapData={{ mapUrl: 'https://maps.example.com' }} />);
    expect(screen.getByText('Loading map...')).toBeInTheDocument();
  });

  it('applies dark theme classes', () => {
    const { container } = render(
      <MapComponent theme="dark" mapData={{ mapUrl: '' }} />,
    );
    expect(container.firstChild).toHaveClass('bg-black');
  });

  it('applies light theme classes by default', () => {
    const { container } = render(
      <MapComponent mapData={{ mapUrl: '' }} />,
    );
    expect(container.firstChild).toHaveClass('bg-white');
  });
});
