import React from 'react';
import { buildFieldMappingDisplayGroups } from '../utils/fieldMappingDisplayGroups';

function destDisplay(row) {
  const o = (row.salesforce_object || '').trim();
  const f = (row.salesforce_field || '').trim();
  if (o && f) return `${o}.${f}`;
  if (f) return f;
  return o || '—';
}

/**
 * Read-only field mappings for flow detail: groups split (same source → many dest)
 * and combine (many sources → same dest) like the chat mapping panel.
 */
export default function FlowFieldMappingsGroupedView({ fieldMappings }) {
  const groups = React.useMemo(() => buildFieldMappingDisplayGroups(fieldMappings), [fieldMappings]);
  const hasGrouping = groups.some((g) => g.kind !== 'single');

  return (
    <>
      {hasGrouping && (
        <p className="field-mappings-group-hint">
          Rows are grouped when one source maps to multiple destinations{' '}
          <span className="field-mappings-hint-badge field-mappings-hint-split">split</span> or several sources map to
          the same destination{' '}
          <span className="field-mappings-hint-badge field-mappings-hint-combine">combine</span>, consistent with how
          mappings are saved from chat.
        </p>
      )}
      <div className="mappings-table mappings-table-grouped">
        <div className="mapping-header">
          <span>Source Field</span>
          <span>Destination Target</span>
          <span>Destination Field</span>
          <span>Lookup Of</span>
        </div>
        {groups.map((group, gi) => (
          <div
            key={gi}
            className={`mapping-group mapping-group-${group.kind}`}
            role="group"
            aria-label={
              group.kind === 'split'
                ? `Split mapping: ${(group.rows[0]?.extracted_field_name || '').trim()}`
                : group.kind === 'combine'
                  ? `Combined mapping: ${destDisplay(group.rows[0] || {})}`
                  : 'Field mapping'
            }
          >
            {group.rows.map((row, ri) => (
              <div
                key={row.id != null ? String(row.id) : `${gi}-${ri}`}
                className={`mapping-row mapping-row-${group.kind} ${ri > 0 ? 'mapping-row-nested' : ''}`}
              >
                <span className="field-name mapping-source-cell">
                  {group.kind === 'split' && (
                    <>
                      {ri === 0 ? (
                        <>
                          <span className="mapping-source-primary">
                            <strong>{(row.extracted_field_name || '').trim() || '—'}</strong>
                            <span className="mapping-badge mapping-badge-split">(split)</span>
                          </span>
                          <div className="mapping-source-subline">
                            <span className="mapping-tree-prefix">↳</span>
                            <span>{(row.salesforce_field || '').trim() || '—'}</span>
                          </div>
                        </>
                      ) : (
                        <div className="mapping-source-subline mapping-source-subline-indented">
                          <span className="mapping-tree-prefix">└</span>
                          <strong>{(row.salesforce_field || '').trim() || '—'}</strong>
                        </div>
                      )}
                    </>
                  )}
                  {group.kind === 'combine' && (
                    <>
                      {ri === 0 ? (
                        <span className="mapping-source-primary">
                          <strong>{(row.extracted_field_name || '').trim() || '—'}</strong>
                          <span className="mapping-badge mapping-badge-combine">(combined)</span>
                        </span>
                      ) : (
                        <div className="mapping-source-subline mapping-source-subline-indented">
                          <span className="mapping-tree-prefix">└</span>
                          <strong>{(row.extracted_field_name || '').trim() || '—'}</strong>
                        </div>
                      )}
                    </>
                  )}
                  {group.kind === 'single' && (
                    <strong>{(row.extracted_field_name || '').trim() || '—'}</strong>
                  )}
                </span>
                <span className="object-name">{(row.salesforce_object || '').trim() || '—'}</span>
                <span className="field-name">{(row.salesforce_field || '').trim() || '—'}</span>
                <span className="lookup-info">{(row.lookup_of || '').trim() || '-'}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </>
  );
}
