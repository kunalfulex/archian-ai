import React, { useState } from 'react';
import './PlannerClarificationPanel.css';

/** GS workbook entity picks — source read vs destination write share the same chip UI. */
const WORKBOOK_PICK_TYPES = new Set(['pick_workbook', 'pick_source_workbook']);

const FIELD_MULTI_SELECT_TYPES = new Set(['pick_create_headers', 'pick_extract_fields']);

function isWorkbookPickType(ctype) {
  return WORKBOOK_PICK_TYPES.has(ctype);
}

function isFieldMultiSelect(ctype) {
  return FIELD_MULTI_SELECT_TYPES.has(ctype);
}

function optionClassName(selected) {
  return `planner-clarification-option${selected ? ' selected' : ''}`;
}

/**
 * Inline picker for planner v2 clarifications (e.g. choose Google Sheet).
 * Users can click an option or answer in the chat input instead.
 */
export function PlannerClarificationPanel({
  clarifications = [],
  onSubmit,
  disabled = false,
}) {
  const [selections, setSelections] = useState({});

  const openClarifications = (clarifications || []).filter(
    (c) => c?.required !== false && !c?.selected_option_id,
  );

  if (!openClarifications.length) return null;

  const isNameOnlyStep =
    openClarifications.length === 1 &&
    openClarifications[0].clarification_type === 'create_workbook_name' &&
    !(openClarifications[0].options || []).length;

  const isChatOnlyEntityStep =
    openClarifications.length === 1 &&
    openClarifications[0].clarification_type === 'pick_source_entity' &&
    !(openClarifications[0].options || []).length;

  const isChatOnlyStep = isNameOnlyStep || isChatOnlyEntityStep;

  const clarificationSatisfied = (c) => {
    if (!c.required) return true;
    if (c.clarification_type === 'create_workbook_name') return false;
    const sel = selections[c.id];
    if (isFieldMultiSelect(c.clarification_type)) {
      if (sel === 'field_all_descriptive') return true;
      if (sel === 'fields_custom') return false;
      if (Array.isArray(sel)) return sel.length > 0;
      return Boolean(sel);
    }
    return Boolean(sel);
  };

  const allRequiredChosen = openClarifications.every(clarificationSatisfied);

  const buildSubmitPayload = () => {
    const payload = [];
    for (const c of openClarifications) {
      const sel = selections[c.id];
      if (!sel) continue;
      if (isFieldMultiSelect(c.clarification_type) && Array.isArray(sel)) {
        for (const optionId of sel) {
          payload.push({ clarification_id: c.id, option_id: optionId });
        }
      } else {
        payload.push({ clarification_id: c.id, option_id: sel });
      }
    }
    return payload;
  };

  const handleContinue = () => {
    if (!allRequiredChosen || disabled) return;
    onSubmit?.(buildSubmitPayload());
  };

  const toggleFieldSelection = (clarId, optId) => {
    if (optId === 'field_all_descriptive') {
      setSelections((prev) => ({ ...prev, [clarId]: optId }));
      return;
    }
    if (optId === 'fields_custom') {
      setSelections((prev) => ({ ...prev, [clarId]: optId }));
      return;
    }
    setSelections((prev) => {
      const current = prev[clarId];
      if (current === 'field_all_descriptive') {
        return { ...prev, [clarId]: [optId] };
      }
      const list = Array.isArray(current) ? [...current] : current ? [current] : [];
      const next = list.includes(optId) ? list.filter((id) => id !== optId) : [...list, optId];
      return { ...prev, [clarId]: next };
    });
  };

  const isOptionSelected = (clar, opt) => {
    const sel = selections[clar.id];
    if (isFieldMultiSelect(clar.clarification_type)) {
      if (opt.id === 'field_all_descriptive' || opt.id === 'fields_custom') {
        return sel === opt.id;
      }
      if (Array.isArray(sel)) return sel.includes(opt.id);
      return sel === opt.id;
    }
    return sel === opt.id;
  };

  const handleOptionClick = (clar, opt) => {
    if (isFieldMultiSelect(clar.clarification_type)) {
      toggleFieldSelection(clar.id, opt.id);
      return;
    }
    if (opt.id === 'fields_custom') {
      setSelections((prev) => ({ ...prev, [clar.id]: opt.id }));
      return;
    }
    setSelections((prev) => ({ ...prev, [clar.id]: opt.id }));
    onSubmit?.([
      {
        clarification_id: clar.id,
        option_id: opt.id,
      },
    ]);
  };

  const firstPickWorkbookId = openClarifications.find((c) =>
    isWorkbookPickType(c.clarification_type),
  )?.id;

  const firstPickFieldsId = openClarifications.find(
    (c) => c.clarification_type === 'pick_extract_fields',
  )?.id;

  const firstPickHeadersId = openClarifications.find(
    (c) => c.clarification_type === 'pick_create_headers',
  )?.id;

  const workbookHint = (
    <p className="planner-clarification-hint">
      Click a sheet above, or reply in chat — e.g. <strong>contact list</strong> or{' '}
      <strong>create a sheet called My Export</strong>
    </p>
  );

  const nameOnlyHint = (
    <p className="planner-clarification-hint">
      Type the spreadsheet name in the chat box below — e.g.{' '}
      <strong>Lead Export</strong> or <strong>Sales Contact List</strong>
    </p>
  );

  const chatEntityHint = (
    <p className="planner-clarification-hint">
      Type the table or object name in the chat box below — e.g.{' '}
      <strong>users</strong> or <strong>contacts</strong>
    </p>
  );

  const fieldPickHint = (
    <p className="planner-clarification-hint">
      Select one or more columns, then click <strong>Continue</strong>. Or reply in chat — e.g.{' '}
      <strong>name, email, phone</strong> or <strong>all columns</strong>
    </p>
  );

  const showContinue = !isChatOnlyStep;

  return (
    <div className="planner-clarification-panel">
      {openClarifications.map((clar) => {
        const ctype = clar.clarification_type;
        const workbookOptions = (clar.options || []).filter((o) => !o.create_new);
        const createOption = (clar.options || []).find((o) => o.create_new);
        const showWorkbookHint =
          isWorkbookPickType(ctype) && clar.id === firstPickWorkbookId;
        const showFieldHint =
          (ctype === 'pick_create_headers' && clar.id === firstPickHeadersId) ||
          (ctype === 'pick_extract_fields' && clar.id === firstPickFieldsId);
        const optionList = isWorkbookPickType(ctype)
          ? workbookOptions
          : (clar.options || []).filter((o) => !o.create_new);
        const multiSelectFields = isFieldMultiSelect(ctype);

        if (ctype === 'create_workbook_name' && !(clar.options || []).length) {
          return (
            <div key={clar.id} className="planner-clarification-block">
              {clar.context_summary ? (
                <p className="planner-clarification-context">{clar.context_summary}</p>
              ) : null}
              {clar.prompt ? (
                <p className="planner-clarification-prompt">{clar.prompt}</p>
              ) : null}
              {isNameOnlyStep ? nameOnlyHint : null}
            </div>
          );
        }

        if (isChatOnlyEntityStep && clar.id === openClarifications[0].id) {
          return (
            <div key={clar.id} className="planner-clarification-block">
              {clar.prompt ? (
                <p className="planner-clarification-prompt">{clar.prompt}</p>
              ) : null}
              {chatEntityHint}
            </div>
          );
        }

        return (
          <div key={clar.id} className="planner-clarification-block">
            {clar.context_summary ? (
              <p className="planner-clarification-context">{clar.context_summary}</p>
            ) : null}
            {clar.prompt ? (
              <p className="planner-clarification-prompt">{clar.prompt}</p>
            ) : null}
            {optionList.length > 0 ? (
              <div
                className="planner-clarification-options"
                role={multiSelectFields ? 'group' : 'radiogroup'}
                aria-label={clar.prompt}
              >
                {optionList.map((opt, optIdx) => {
                  const selected = isOptionSelected(clar, opt);
                  return (
                    <button
                      key={`${clar.id}-${opt.id}-${optIdx}`}
                      type="button"
                      className={optionClassName(selected)}
                      disabled={disabled}
                      aria-pressed={multiSelectFields ? selected : undefined}
                      aria-checked={!multiSelectFields ? selected : undefined}
                      onClick={() => handleOptionClick(clar, opt)}
                    >
                      {opt.label}
                    </button>
                  );
                })}
                {createOption ? (
                  <button
                    type="button"
                    className={`planner-clarification-option planner-clarification-option--create${
                      selections[clar.id] === createOption.id ? ' selected' : ''
                    }`}
                    disabled={disabled}
                    onClick={() =>
                      onSubmit?.([
                        {
                          clarification_id: clar.id,
                          option_id: createOption.id,
                        },
                      ])
                    }
                  >
                    {createOption.label || 'Create new spreadsheet'}
                  </button>
                ) : null}
              </div>
            ) : null}
            {showWorkbookHint ? workbookHint : null}
            {showFieldHint ? fieldPickHint : null}
          </div>
        );
      })}
      {showContinue ? (
        <div className="planner-clarification-actions">
          <button
            type="button"
            className="planner-clarification-continue"
            disabled={!allRequiredChosen || disabled}
            onClick={handleContinue}
          >
            Continue
          </button>
        </div>
      ) : null}
    </div>
  );
}
