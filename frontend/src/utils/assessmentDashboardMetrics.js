/** Metrics derived from connector assessment reports for dashboard cards */

export function readinessTier(score) {
  if (score == null || Number.isNaN(score)) return { label: 'Unknown', className: 'neutral' };
  if (score >= 80) return { label: 'Good', className: 'good' };
  if (score >= 60) return { label: 'Fair', className: 'fair' };
  return { label: 'Needs attention', className: 'alert' };
}

export function buildDashboardMetrics(report) {
  const scores = report?.scores || {};
  const sections = report?.sections || {};
  const entities = report?.entities || [];
  const graph = sections.schema_graph || {};
  const nodes = graph.nodes || [];
  const edges = graph.edges || [];

  const entitiesAssessed = scores.entities_assessed ?? entities.length;
  const overall = scores.overall;

  let problemEntities = 0;
  for (const ep of entities) {
    if (ep?.error) {
      problemEntities += 1;
      continue;
    }
    const qr = ep.quality_report || {};
    const summary = qr.summary || {};
    const score = summary.data_quality_score;
    const dups = qr.duplicates || {};
    const exact = dups.exact_duplicates?.count ?? 0;
    const keyDup = dups.key_field_duplicates?.count ?? 0;
    const miss = qr.missing_values?.total_missing ?? 0;
    const fmt = Object.values(qr.format_issues || {}).reduce(
      (s, v) => s + (Array.isArray(v) ? v.length : 0),
      0,
    );
    if (exact || keyDup || miss || fmt || (score != null && score < 80)) {
      problemEntities += 1;
    }
  }

  const gapCount = (sections.data_gaps?.findings || []).length;
  const relationshipGaps = (sections.data_gaps?.findings || []).filter(
    (g) => g.category === 'relationship',
  ).length;

  const isEnterpriseGraph = scores.sources_included != null;
  const crossLinks = sections.connected_environment?.cross_links || [];
  const mappedFromScores = scores.mapped_entities;
  const canonGraph = sections.schema_map?.canonical_graph;
  const canonNodeCount = canonGraph?.nodes?.length ?? 0;

  return {
    overall,
    tier: readinessTier(overall),
    entitiesAssessed,
    problemEntities,
    mappedEntities: isEnterpriseGraph
      ? (mappedFromScores ?? canonNodeCount ?? crossLinks.length)
      : nodes.length || entitiesAssessed,
    relationshipCount: isEnterpriseGraph
      ? (canonGraph?.edges?.length ?? scores.cross_links_count ?? crossLinks.length)
      : edges.length,
    gapCount,
    relationshipGaps,
    dimensions: scores.dimensions || {},
  };
}

/** Pick up to 3 nodes for mini schema strip (CRM priority + degree) */
export function pickSchemaMapPreview(graph) {
  const nodes = graph?.nodes || [];
  const edges = graph?.edges || [];
  if (!nodes.length) return { chain: [], edges: [] };

  const priority = ['Account', 'Contact', 'Lead', 'Opportunity', 'Case'];
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const chain = [];

  for (const id of priority) {
    if (byId.has(id) && chain.length < 3) chain.push(byId.get(id));
  }

  if (chain.length < 3) {
    const degree = {};
    edges.forEach((e) => {
      degree[e.source_id] = (degree[e.source_id] || 0) + 1;
      degree[e.target_id] = (degree[e.target_id] || 0) + 1;
    });
    [...nodes]
      .sort((a, b) => (degree[b.id] || 0) - (degree[a.id] || 0))
      .forEach((n) => {
        if (chain.length < 3 && !chain.find((c) => c.id === n.id)) chain.push(n);
      });
  }

  const ids = new Set(chain.map((n) => n.id));
  const previewEdges = edges.filter((e) => ids.has(e.source_id) && ids.has(e.target_id));
  return { chain: chain.slice(0, 3), edges: previewEdges };
}
