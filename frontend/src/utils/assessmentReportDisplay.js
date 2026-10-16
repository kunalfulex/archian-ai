/**
 * Helpers for connector assessment report display (legacy + v2 sections).
 */

export function buildEntityLabelMap(entities) {
  const map = {};
  for (const e of entities || []) {
    if (e?.entity_id && e?.label) {
      map[e.entity_id] = e.label;
    }
  }
  return map;
}

export function entityLabel(entityId, labelMap) {
  if (!entityId) return 'Unknown';
  return labelMap[entityId] || entityId;
}

function dupCount(qr, key) {
  const block = qr?.duplicates?.[key];
  return typeof block?.count === 'number' ? block.count : 0;
}

function keyFieldsChecked(qr) {
  const block = qr?.duplicates?.key_field_duplicates;
  const fromBlock = block?.fields_checked;
  if (Array.isArray(fromBlock) && fromBlock.length) {
    return fromBlock.map((f) => String(f));
  }
  const legacy = qr?.duplicates?.key_fields_checked;
  if (Array.isArray(legacy) && legacy.length) {
    return legacy.map((f) => String(f));
  }
  return [];
}

export function formatKeyFieldsLabel(fields) {
  if (!fields?.length) return '';
  return fields.join(', ');
}

function formatIssueCount(qr) {
  const fmt = qr?.format_issues || {};
  return Object.values(fmt).reduce(
    (sum, v) => sum + (Array.isArray(v) ? v.length : 0),
    0,
  );
}

export function aggregateQualityFromEntities(entities) {
  const totals = {
    exact_duplicates: 0,
    key_field_duplicates: 0,
    key_fields_checked: [],
    total_missing: 0,
    format_issue_count: 0,
    entities_with_issues: 0,
  };
  const keyFieldsUnion = new Set();
  const byEntity = [];

  for (const ep of entities || []) {
    if (ep?.error) {
      totals.entities_with_issues += 1;
      byEntity.push({
        entity_id: ep.entity_id,
        label: ep.label || ep.entity_id,
        records_sampled: ep.records_sampled || 0,
        score: 0,
        error: ep.error,
      });
      continue;
    }
    const qr = ep.quality_report || {};
    const summary = qr.summary || {};
    const missing = qr.missing_values || {};
    const exact = dupCount(qr, 'exact_duplicates');
    const keyDup = dupCount(qr, 'key_field_duplicates');
    const keyFields = keyFieldsChecked(qr);
    keyFields.forEach((f) => keyFieldsUnion.add(f));
    const miss = missing.total_missing || 0;
    const fmt = formatIssueCount(qr);
    const score = summary.data_quality_score;

    totals.exact_duplicates += exact;
    totals.key_field_duplicates += keyDup;
    totals.total_missing += miss;
    totals.format_issue_count += fmt;
    if (exact || keyDup || miss || fmt || (score != null && score < 80)) {
      totals.entities_with_issues += 1;
    }

    byEntity.push({
      entity_id: ep.entity_id,
      label: ep.label || ep.entity_id,
      records_sampled: ep.records_sampled || 0,
      score,
      duplicates: {
        exact_duplicates: exact,
        key_field_duplicates: keyDup,
        key_fields_checked: keyFields,
      },
      missing_values: {
        total_missing: miss,
        by_field: missing.by_field || {},
      },
      format_issue_count: fmt,
      recommendations: summary.recommendations || ep.issues_summary || [],
      summary_text: qr.summary_text || '',
    });
  }

  byEntity.sort((a, b) => {
    const issues = (e) =>
      (e.duplicates?.exact_duplicates || 0)
      + (e.duplicates?.key_field_duplicates || 0)
      + (e.missing_values?.total_missing || 0)
      + (e.format_issue_count || 0);
    return issues(b) - issues(a);
  });

  totals.key_fields_checked = [...keyFieldsUnion].sort();
  return { connector_totals: totals, by_entity: byEntity };
}

function enrichDataQualityWithEntityKeys(dq, fromEntities) {
  if (!dq?.connector_totals || !fromEntities?.connector_totals) return dq;
  const hasTotalsKeys = dq.connector_totals.key_fields_checked?.length > 0;
  const entityKeys = fromEntities.connector_totals.key_fields_checked || [];
  if (hasTotalsKeys && !entityKeys.length) return dq;

  const byEntityMap = new Map(
    (fromEntities.by_entity || []).map((row) => [row.entity_id, row]),
  );

  return {
    ...dq,
    connector_totals: {
      ...dq.connector_totals,
      key_fields_checked: hasTotalsKeys
        ? dq.connector_totals.key_fields_checked
        : entityKeys,
    },
    by_entity: (dq.by_entity || []).map((row) => {
      if (row.duplicates?.key_fields_checked?.length) return row;
      const enriched = byEntityMap.get(row.entity_id);
      const keyFields = enriched?.duplicates?.key_fields_checked;
      if (!keyFields?.length) return row;
      return {
        ...row,
        duplicates: { ...row.duplicates, key_fields_checked: keyFields },
      };
    }),
  };
}

export function resolveDataQualitySection(report) {
  const sections = report?.sections || {};
  const dq = sections.data_quality;
  const fromEntities = report?.entities?.length
    ? aggregateQualityFromEntities(report.entities)
    : null;

  if (dq?.connector_totals && dq?.by_entity) {
    return enrichDataQualityWithEntityKeys(dq, fromEntities) || dq;
  }
  if (fromEntities) return fromEntities;
  return dq || { connector_totals: null, by_entity: [], highlights: [] };
}

const GAP_CATEGORY_ORDER = ['relationship', 'metadata', 'schema_staleness', 'coverage', 'other'];

export function groupGapFindings(findings) {
  const groups = {};
  for (const g of findings || []) {
    const cat = g.category || 'other';
    if (!groups[cat]) groups[cat] = [];
    groups[cat].push(g);
  }
  const ordered = [];
  for (const cat of GAP_CATEGORY_ORDER) {
    if (groups[cat]?.length) {
      ordered.push({ category: cat, items: groups[cat] });
    }
  }
  for (const cat of Object.keys(groups)) {
    if (!GAP_CATEGORY_ORDER.includes(cat)) {
      ordered.push({ category: cat, items: groups[cat] });
    }
  }
  return ordered;
}

export const DIMENSION_LABELS = {
  completeness: 'Completeness — missing values in sampled rows',
  quality: 'Quality — duplicates, formats, and value reliability',
  consistency: 'Consistency — case and value patterns',
  structure: 'Structure — schema/catalog coverage for this connector',
  warehouse_readiness: 'Warehouse readiness — load suitability from sample quality',
};
