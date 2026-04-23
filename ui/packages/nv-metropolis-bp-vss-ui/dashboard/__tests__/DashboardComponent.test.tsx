// SPDX-License-Identifier: MIT
import { render, screen, act } from '@testing-library/react';
import { DashboardComponent } from '../lib-src/DashboardComponent';

describe('DashboardComponent', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    act(() => {
      jest.runOnlyPendingTimers();
    });
    jest.useRealTimers();
  });

  it('shows error when kibanaBaseUrl is not configured', () => {
    render(<DashboardComponent dashboardData={{ kibanaBaseUrl: null }} />);
    expect(
      screen.getByText(/Kibana base URL is not configured/i),
    ).toBeInTheDocument();
  });

  it('shows error when kibanaBaseUrl is empty string', () => {
    render(<DashboardComponent dashboardData={{ kibanaBaseUrl: '' }} />);
    expect(
      screen.getByText(/Kibana base URL is not configured/i),
    ).toBeInTheDocument();
  });

  it('renders loading state with a valid URL', () => {
    render(
      <DashboardComponent
        dashboardData={{ kibanaBaseUrl: 'https://kibana.example.com' }}
      />,
    );
    expect(screen.getByText('Loading dashboard...')).toBeInTheDocument();
  });

  it('applies dark theme classes', () => {
    const { container } = render(
      <DashboardComponent
        theme="dark"
        dashboardData={{ kibanaBaseUrl: null }}
      />,
    );
    expect(container.firstChild).toHaveClass('bg-[#1a1a1a]');
  });

  it('applies light theme classes by default', () => {
    const { container } = render(
      <DashboardComponent dashboardData={{ kibanaBaseUrl: null }} />,
    );
    expect(container.firstChild).toHaveClass('bg-white');
  });
});
