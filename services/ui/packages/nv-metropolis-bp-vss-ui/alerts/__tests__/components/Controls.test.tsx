// SPDX-License-Identifier: MIT
import React from 'react';
import { render, screen } from '@testing-library/react';
import { Controls } from '../../lib-src/components/Controls';

describe('Alerts Controls', () => {
  it('hides Manage Alerts when every rule kind is disabled', () => {
    render(
      <Controls
        isDark={false}
        alertsView="view"
        onAlertsViewChange={jest.fn()}
        onAddNewAlertRule={jest.fn()}
        manageAlertsEnabled={false}
      />,
    );

    expect(screen.getByRole('tab', { name: 'View Alerts' })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Manage Alerts' })).not.toBeInTheDocument();
    expect(screen.queryByText('Create alert rule')).not.toBeInTheDocument();
  });
});
