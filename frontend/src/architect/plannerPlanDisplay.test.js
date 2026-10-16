import { render, screen } from '@testing-library/react';
import {
  buildPlannerPlanReadyMarkdown,
  buildPlannerPlanPanelProps,
  formatPlanEntityExecutionSide,
  isDataOpsOnlyPlan,
  planHasCreateStep,
  resolveEffectivePlanMode,
  resolvePlannerDestinationDisplay,
  resolvePlannerDestinationHeader,
  resolvePlannerPlanContextLines,
} from '../architect/plannerPlanDisplay';
import { PlannerPlanPanel } from '../components/PlannerPlanPanel';

describe('plannerPlanDisplay', () => {
  test('isDataOpsOnlyPlan for data_ops mode', () => {
    expect(
      isDataOpsOnlyPlan([{ tool_id: 'run_data_ops_plan', label: 'Transform columns' }], {
        mode: 'data_ops',
      }),
    ).toBe(true);
  });

  test('resolvePlannerDestinationHeader returns null for data ops only', () => {
    expect(
      resolvePlannerDestinationHeader([{ tool_id: 'run_data_ops_plan' }], {
        mode: 'data_ops',
      }),
    ).toBeNull();
  });

  test('formatPlanEntityExecutionSide shows tab not spreadsheet id for google_sheets', () => {
    const line = formatPlanEntityExecutionSide({
      connector_type: 'google_sheets',
      display_name: 'Archian Data',
      entity_key: '13w05fj34LJg2HlPqEvIHt77pH4JmD2DsFWtun-vImA8::Sheet1',
      catalog_label: 'Sheet1',
      sheet_name: 'Sheet1',
    });
    expect(line).toContain('Archian Data');
    expect(line).toContain('Sheet1');
    expect(line).not.toContain('13w05fj34');
  });

  test('resolvePlannerPlanContextLines strips spreadsheet id from source fallback', () => {
    const ctx = resolvePlannerPlanContextLines(
      [{ tool_id: 'extract_google_sheets', label: 'Extract from Sheet1 in Archian Data' }],
      {
        sourceName: 'Archian Data',
        sourceEntityKey: '13w05fj34LJg2HlPqEvIHt77pH4JmD2DsFWtun-vImA8::Sheet1',
        sourceConnectorType: 'google_sheets',
      },
    );
    expect(ctx.srcLine).toContain('Archian Data');
    expect(ctx.srcLine).toContain('Sheet1');
    expect(ctx.srcLine).not.toContain('13w05fj34');
  });

  test('resolveEffectivePlanMode downgrades stale create when steps omit create', () => {
    const steps = [
      { tool_id: 'extract_salesforce' },
      { tool_id: 'map_fields' },
      { tool_id: 'start_migration' },
    ];
    expect(resolveEffectivePlanMode(steps, 'create')).toBe('migrate');
    expect(planHasCreateStep(steps)).toBe(false);
  });

  test('resolvePlannerDestinationDisplay prefers plan_contract workbook title', () => {
    const display = resolvePlannerDestinationDisplay(
      { destination_name: 'Customer Pipeline' },
      {
        classification: {
          mode: 'create',
          destination_name: 'Customer Pipeline',
          destination_object_api_name: 'Customer Pipeline',
        },
        execution_plan: {
          metadata: {
            plan_contract: {
              destination: {
                connector_type: 'google_sheets',
                display_name: 'Contact Information',
                entity_key: 'abc123::Sheet1',
                sheet_name: 'Sheet1',
              },
            },
          },
        },
      },
      [{ tool_id: 'extract_salesforce' }, { tool_id: 'map_fields' }, { tool_id: 'start_migration' }],
    );
    expect(display.destinationName).toBe('Contact Information');
    expect(display.mode).toBe('migrate');
  });

  test('buildPlannerPlanPanelProps uses pending execution plan contract', () => {
    const props = buildPlannerPlanPanelProps({
      metadata: {
        plan_steps_summary: [{ tool_id: 'extract_salesforce', label: 'Extract' }],
        planner_pending: {
          classification: { mode: 'create', source_name: 'SF 1' },
          execution_plan: {
            metadata: {
              plan_contract: {
                destination: { display_name: 'Contact Information', sheet_name: 'Sheet1' },
              },
            },
          },
        },
      },
    });
    expect(props.destinationName).toBe('Contact Information');
    expect(props.mode).not.toBe('create');
  });

  test('buildPlannerPlanReadyMarkdown shows migrate header for existing sheet pipeline', () => {
    const md = buildPlannerPlanReadyMarkdown(
      [
        { tool_id: 'extract_salesforce', label: 'Extract contacts' },
        { tool_id: 'map_fields', label: 'Map fields' },
        { tool_id: 'start_migration', label: 'Migrate' },
      ],
      {
        planContract: {
          destination: {
            connector_type: 'google_sheets',
            display_name: 'Contact Information',
            entity_key: 'abc::Sheet1',
            sheet_name: 'Sheet1',
          },
        },
        mode: 'create',
        sourceName: 'SF 1',
      },
    );
    expect(md).toContain('**Destination:**');
    expect(md).toContain('Contact Information');
    expect(md).not.toContain('Migrate to:');
    expect(md).not.toContain('Create in:');
  });

  test('buildPlannerPlanReadyMarkdown omits migrate line for data ops', () => {
    const md = buildPlannerPlanReadyMarkdown(
      [{ tool_id: 'run_data_ops_plan', label: 'Transform columns' }],
      {
        mode: 'data_ops',
        sourceName: 'SF 1',
        sourceEntityKey: 'Contact',
        destinationName: 'SF 1',
      },
    );
    expect(md).toContain('Work on dataset');
    expect(md).toContain('SF 1');
    expect(md).not.toContain('Destination:');
    expect(md).toContain('Transform columns');
  });
});

describe('PlannerPlanPanel', () => {
  test('renders compound plan with data ops step labels', () => {
    render(
      <PlannerPlanPanel
        steps={[
          { tool_id: 'extract_google_sheets', label: 'Extract from contact list' },
          {
            tool_id: 'run_data_ops_plan',
            label: 'Process extracted data (Remove duplicates, Filter rows)',
          },
          { tool_id: 'map_fields', label: 'Map fields to contact list' },
          { tool_id: 'start_migration', label: 'Migrate to contact list' },
        ]}
        destinationName="contact list"
        onApprove={jest.fn()}
      />,
    );

    expect(screen.getByText(/Execution plan/i)).toBeInTheDocument();
    expect(screen.getByText('Extract from contact list')).toBeInTheDocument();
    expect(
      screen.getByText('Process extracted data (Remove duplicates, Filter rows)'),
    ).toBeInTheDocument();
    expect(screen.getByText('Map fields to contact list')).toBeInTheDocument();
    expect(screen.getByText('Migrate to contact list')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /run plan/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /dismiss plan/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /edit prompt/i })).not.toBeInTheDocument();
  });

  test('data ops only plan does not show migrate header', () => {
    render(
      <PlannerPlanPanel
        steps={[{ tool_id: 'run_data_ops_plan', label: 'Transform columns' }]}
        mode="data_ops"
        sourceName="SF 1"
        sourceEntityKey="Contact"
        destinationName="SF 1"
        goalSummary="Transform extracted data"
        onApprove={jest.fn()}
      />,
    );

    expect(screen.queryByText(/^Destination:$/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Work on dataset:/i)).toBeInTheDocument();
    expect(screen.getByText('Transform columns')).toBeInTheDocument();
  });

  test('dismiss callback fires', () => {
    const onDismiss = jest.fn();
    render(
      <PlannerPlanPanel
        steps={[{ tool_id: 'extract_google_sheets', label: 'Extract' }]}
        onApprove={jest.fn()}
        onDismiss={onDismiss}
      />,
    );
    screen.getByRole('button', { name: /dismiss plan/i }).click();
    expect(onDismiss).toHaveBeenCalled();
  });
});
