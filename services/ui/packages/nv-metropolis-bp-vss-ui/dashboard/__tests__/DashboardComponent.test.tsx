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
    expect(container.firstChild).toHaveClass('bg-black');
  });

  it('does not remount the iframe when isActive toggles off and on after first load', () => {
    const dashboardData = {
      kibanaBaseUrl: 'https://kibana.example.com',
      dashboards: [] as const,
    };
    const { rerender } = render(
      <DashboardComponent isActive={false} dashboardData={dashboardData} />,
    );
    expect(screen.queryByTitle('Kibana Dashboard')).not.toBeInTheDocument();

    rerender(<DashboardComponent isActive dashboardData={dashboardData} />);
    const iframe = screen.getByTitle('Kibana Dashboard');
    act(() => {
      iframe.dispatchEvent(new Event('load'));
    });

    rerender(<DashboardComponent isActive={false} dashboardData={dashboardData} />);
    rerender(<DashboardComponent isActive dashboardData={dashboardData} />);

    expect(screen.getByTitle('Kibana Dashboard')).toBe(iframe);
  });
