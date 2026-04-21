// SPDX-License-Identifier: MIT
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { FilterTag } from '../../lib-src/components/FilterTag';

jest.mock('@nemo-agent-toolkit/ui');

const defaultColors = {
  bg: 'bg-blue-100',
  border: 'border-blue-200',
  text: 'text-blue-800',
  hover: 'hover:text-blue-600',
};

describe('FilterTag', () => {
  it('renders the filter text', () => {
    render(
      <FilterTag type="sensors" filter="Cam-A" colors={defaultColors} onRemove={jest.fn()} />
    );
    expect(screen.getByText('Cam-A')).toBeInTheDocument();
  });

  it('calls onRemove with type and filter when close button is clicked', () => {
    const onRemove = jest.fn();
    render(
      <FilterTag type="alertTypes" filter="Tailgating" colors={defaultColors} onRemove={onRemove} />
    );

    const button = screen.getByRole('button');
    fireEvent.click(button);

    expect(onRemove).toHaveBeenCalledWith('alertTypes', 'Tailgating');
  });

  it('applies color classes', () => {
    const { container } = render(
      <FilterTag type="sensors" filter="Cam-B" colors={defaultColors} onRemove={jest.fn()} />
    );

    const tag = container.firstChild as HTMLElement;
    expect(tag.className).toContain('bg-blue-100');
    expect(tag.className).toContain('border-blue-200');
    expect(tag.className).toContain('text-blue-800');
  });

  it('renders different filter types', () => {
    const { rerender } = render(
      <FilterTag type="sensors" filter="Cam-A" colors={defaultColors} onRemove={jest.fn()} />
    );
    expect(screen.getByText('Cam-A')).toBeInTheDocument();

    rerender(
      <FilterTag type="alertTriggered" filter="Motion" colors={defaultColors} onRemove={jest.fn()} />
    );
    expect(screen.getByText('Motion')).toBeInTheDocument();
  });
});
