import {
  enrichPlannerPendingWithIntentState,
  readIntentStateFromClassification,
  readIntentStateFromPlannerPending,
} from './intentState';

describe('intentState planner helpers', () => {
  it('prefers intent_state on classification', () => {
    const snap = { turn_type: 'extract', fields: { scope: 'explicit' } };
    expect(
      readIntentStateFromClassification({
        migration_intent: { extract_field_scope: 'vague' },
        intent_state: snap,
      }),
    ).toEqual(snap);
  });

  it('enriches planner pending from nested classification', () => {
    const intent = { turn_type: 'migrate', source: { connector_type: 'salesforce' } };
    const pending = enrichPlannerPendingWithIntentState({
      original_prompt: 'migrate',
      classification: { intent_state: intent, source: 'salesforce' },
    });
    expect(readIntentStateFromPlannerPending(pending)).toEqual(intent);
    expect(pending.classification.intent_state).toEqual(intent);
  });
});
