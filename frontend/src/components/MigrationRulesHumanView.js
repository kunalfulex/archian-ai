import React from 'react';
import { isDeepEmptyValue } from '../utils/flowDisplayUtils';
import {
  formatTransformationActionLabel,
  formatOperationTypeLabel,
  formatSourceFieldDisplay,
  formatOutputFieldDisplay,
  formatTransformationDetailLine,
  sortTransformationFieldsByOrder,
  formatFilterRulesBlock,
  formatFilterLogicDescription,
  formatDeduplicationRulesBlock,
  formatDataCleanupRulesLines,
} from '../utils/migrationRulesDisplay';

export function MissingFieldPreferenceHuman({ value }) {
  const option = value?.option;
  const placeholderValue = value?.placeholderValue ?? 'Unknown';

  if (option === 'skip') {
    return (
      <div className="migration-rule-human">
        <p className="migration-rule-lead">
          <strong>Skip records with missing required fields.</strong> If any required mapped field is empty, null, or
          treated as missing (including empty values where a value is required), the entire record is skipped and not
          migrated.
        </p>
      </div>
    );
  }

  if (option === 'placeholder') {
    return (
      <div className="migration-rule-human">
        <p className="migration-rule-lead">
          <strong>Fill missing required fields with a placeholder.</strong> When a required field has no value, it is set
          to{' '}
          <code className="migration-inline-code">{String(placeholderValue)}</code> before migration. Records are not
          dropped solely because a required field was empty.
        </p>
      </div>
    );
  }

  return (
    <div className="migration-rule-fallback">
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </div>
  );
}

function TransformationFieldsTable({ fields }) {
  if (!Array.isArray(fields) || fields.length === 0) return null;
  const sorted = sortTransformationFieldsByOrder(fields);

  return (
    <div className="migration-rules-table-wrap">
      <table className="migration-rules-table">
        <thead>
          <tr>
            <th scope="col">#</th>
            <th scope="col">Source field</th>
            <th scope="col">Output</th>
            <th scope="col">Action</th>
            <th scope="col">Stage</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((row, idx) => {
            const detail = formatTransformationDetailLine(row);
            const ruleNumber =
              row.order != null && row.order !== ''
                ? Number(row.order) + 1
                : idx + 1;
            return (
              <tr key={row.rule_id || `row-${idx}`}>
                <td>{ruleNumber}</td>
                <td>{formatSourceFieldDisplay(row)}</td>
                <td>
                  <div>{formatOutputFieldDisplay(row)}</div>
                  {detail ? <div className="migration-rule-cell-detail">{detail}</div> : null}
                </td>
                <td>{formatTransformationActionLabel(row.action)}</td>
                <td>{formatOperationTypeLabel(row.operation_type)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function FilterRulesHuman({ filterRules }) {
  const block = formatFilterRulesBlock(filterRules);
  if (!block) {
    return (
      <pre className="migration-rule-json-lite">{JSON.stringify(filterRules, null, 2)}</pre>
    );
  }
  const { logic, lines } = block;
  return (
    <div className="migration-rule-filter-human">
      <p className="migration-rule-filter-lead">{formatFilterLogicDescription(logic)}</p>
      <p className="migration-rule-filter-logic">
        Conditions are combined with <strong>{logic}</strong>.
      </p>
      <ul className="migration-rule-bullet-list">
        {lines.map((line, i) => (
          <li key={i}>{line}</li>
        ))}
      </ul>
    </div>
  );
}

function DeduplicationRulesHuman({ dedup }) {
  const block = formatDeduplicationRulesBlock(dedup);
  if (!block) {
    return <pre className="migration-rule-json-lite">{JSON.stringify(dedup, null, 2)}</pre>;
  }
  return (
    <div className="migration-rule-dedup-human">
      <ul className="migration-rule-bullet-list">
        {block.summaryLines.map((line, i) => (
          <li key={i}>{line}</li>
        ))}
      </ul>
    </div>
  );
}

function DataCleanupRulesHuman({ cleanup }) {
  const lines = formatDataCleanupRulesLines(cleanup);
  if (!lines || lines.length === 0) {
    return <pre className="migration-rule-json-lite">{JSON.stringify(cleanup, null, 2)}</pre>;
  }
  return (
    <div className="migration-rule-cleanup-human">
      <p className="migration-rule-subhead migration-rule-subhead-inline">Value cleanup (per field)</p>
      <ul className="migration-rule-bullet-list">
        {lines.map((line, i) => (
          <li key={i}>{line}</li>
        ))}
      </ul>
    </div>
  );
}

function TransformationRulesBlock({ value }) {
  if (value?.fields && Array.isArray(value.fields)) {
    return <TransformationFieldsTable fields={value.fields} />;
  }
  if (isDeepEmptyValue(value)) return null;
  return (
    <div className="migration-rule-fallback">
      <pre className="migration-rule-json-lite">{JSON.stringify(value, null, 2)}</pre>
    </div>
  );
}

export function ExtractionRulesHuman({ value }) {
  if (!value || typeof value !== 'object') return null;

  const tr = value.transformation_rules;
  const fields = Array.isArray(tr) ? tr : tr?.fields;
  const hasFields = Array.isArray(fields) && fields.length > 0;
  const filterRules = value.filter_rules;
  const dedup = value.deduplication_rules;
  const dataCleanup = value.data_cleanup_rules;

  const showFilter = filterRules != null && !isDeepEmptyValue(filterRules);
  const showDedup = dedup != null && !isDeepEmptyValue(dedup);
  const showCleanup = dataCleanup != null && !isDeepEmptyValue(dataCleanup);

  return (
    <div className="migration-rule-human migration-extraction-rules">
      {hasFields && (
        <>
          <p className="migration-rule-subhead">Field rules (extraction)</p>
          <TransformationFieldsTable fields={fields} />
        </>
      )}
      {showFilter && (
        <div className="migration-rule-nested">
          <h6 className="migration-rule-nested-title">Filter rules</h6>
          <FilterRulesHuman filterRules={filterRules} />
        </div>
      )}
      {showDedup && (
        <div className="migration-rule-nested">
          <h6 className="migration-rule-nested-title">Deduplication</h6>
          <DeduplicationRulesHuman dedup={dedup} />
        </div>
      )}
      {showCleanup && (
        <div className="migration-rule-nested">
          <h6 className="migration-rule-nested-title">Data cleanup</h6>
          <DataCleanupRulesHuman cleanup={dataCleanup} />
        </div>
      )}
      {!hasFields && !showFilter && !showDedup && !showCleanup && (
        <div className="migration-rule-fallback">
          <pre>{JSON.stringify(value, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}

function SourceSchemaHuman({ value }) {
  if (value == null) return null;
  if (typeof value === 'object' && !Array.isArray(value)) {
    const keys = Object.keys(value);
    if (keys.length > 0 && keys.length <= 16) {
      return (
        <ul className="migration-source-schema-list">
          {keys.map((k) => (
            <li key={k}>
              <span className="migration-source-schema-key">{k}</span>
              {typeof value[k] === 'object' ? (
                <pre className="migration-rule-json-lite migration-rule-json-inline">{JSON.stringify(value[k], null, 2)}</pre>
              ) : (
                <span className="migration-source-schema-val">{String(value[k])}</span>
              )}
            </li>
          ))}
        </ul>
      );
    }
  }
  return <pre className="migration-rule-json-lite">{JSON.stringify(value, null, 2)}</pre>;
}

export function MigrationRuleHumanContent({ ruleKey, value }) {
  switch (ruleKey) {
    case 'missing_field_preference':
      return <MissingFieldPreferenceHuman value={value} />;
    case 'extraction_rules':
      return <ExtractionRulesHuman value={value} />;
    case 'mapping_transformation':
    case 'transformation_rules':
      return <TransformationRulesBlock value={value} />;
    case 'source_schema':
      return <SourceSchemaHuman value={value} />;
    default:
      return (
        <div className="migration-rule-fallback">
          <pre className="migration-rule-json-lite">{JSON.stringify(value, null, 2)}</pre>
        </div>
      );
  }
}
