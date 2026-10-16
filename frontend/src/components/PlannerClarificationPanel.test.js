import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { PlannerClarificationPanel } from './PlannerClarificationPanel';

const HEADER_CLAR = {
  id: 'c-headers',
  clarification_type: 'pick_create_headers',
  prompt: 'What column headers would you like?',
  required: true,
  options: [
    { id: 'header:First Name', label: 'First Name' },
    { id: 'header:Last Name', label: 'Last Name' },
    { id: 'header:Email', label: 'Email' },
    { id: 'fields_custom', label: 'Type a custom list in chat' },
  ],
};

describe('PlannerClarificationPanel', () => {
  test('pick_create_headers allows multi-select with visible selected state', () => {
    const onSubmit = jest.fn();
    render(
      <PlannerClarificationPanel clarifications={[HEADER_CLAR]} onSubmit={onSubmit} />,
    );

    const firstName = screen.getByRole('button', { name: 'First Name' });
    const lastName = screen.getByRole('button', { name: 'Last Name' });
    const email = screen.getByRole('button', { name: 'Email' });
    const continueBtn = screen.getByRole('button', { name: 'Continue' });

    expect(continueBtn).toBeDisabled();

    fireEvent.click(firstName);
    expect(firstName).toHaveClass('selected');
    expect(lastName).not.toHaveClass('selected');
    expect(continueBtn).not.toBeDisabled();

    fireEvent.click(lastName);
    expect(firstName).toHaveClass('selected');
    expect(lastName).toHaveClass('selected');
    expect(email).not.toHaveClass('selected');

    fireEvent.click(email);
    expect(email).toHaveClass('selected');

    fireEvent.click(continueBtn);
    expect(onSubmit).toHaveBeenCalledWith([
      { clarification_id: 'c-headers', option_id: 'header:First Name' },
      { clarification_id: 'c-headers', option_id: 'header:Last Name' },
      { clarification_id: 'c-headers', option_id: 'header:Email' },
    ]);
  });

  test('pick_create_headers toggles selection off on second click', () => {
    render(<PlannerClarificationPanel clarifications={[HEADER_CLAR]} onSubmit={jest.fn()} />);

    const firstName = screen.getByRole('button', { name: 'First Name' });
    fireEvent.click(firstName);
    expect(firstName).toHaveClass('selected');

    fireEvent.click(firstName);
    expect(firstName).not.toHaveClass('selected');
    expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled();
  });

  test('create_workbook_name with options uses selected class on pick', () => {
    const onSubmit = jest.fn();
    const clar = {
      id: 'c-title',
      clarification_type: 'create_workbook_name',
      prompt: 'Name your sheet',
      required: true,
      options: [
        { id: 'text:Lead Export', label: 'Lead Export' },
        { id: 'text:Contact List', label: 'Contact List' },
      ],
    };

    render(<PlannerClarificationPanel clarifications={[clar]} onSubmit={onSubmit} />);

    const option = screen.getByRole('button', { name: 'Lead Export' });
    fireEvent.click(option);
    expect(option).toHaveClass('selected');
    expect(onSubmit).toHaveBeenCalledWith([
      { clarification_id: 'c-title', option_id: 'text:Lead Export' },
    ]);
  });
});
