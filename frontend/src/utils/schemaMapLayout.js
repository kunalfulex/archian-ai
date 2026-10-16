/** Layout helpers for schema map visualization */

const CRM_PRIORITY = ['Account', 'Contact', 'Lead', 'Opportunity', 'Case', 'User', 'Campaign', 'Product2'];
const MAX_FOCUS_NODES = 18;
const NODE_W = 168;
const NODE_H = 76;
const GAP_X = 56;
const GAP_Y = 28;

export function selectFocusSubgraph(nodes, edges, maxNodes = MAX_FOCUS_NODES) {
  if (!nodes.length) return { nodes: [], edges: [] };
  const nodeIds = new Set(nodes.map((n) => n.id));
  const degree = {};
  edges.forEach((e) => {
    if (!nodeIds.has(e.source_id) || !nodeIds.has(e.target_id)) return;
    degree[e.source_id] = (degree[e.source_id] || 0) + 1;
    degree[e.target_id] = (degree[e.target_id] || 0) + 1;
  });

  const included = new Set();
  CRM_PRIORITY.forEach((id) => {
    if (nodeIds.has(id)) included.add(id);
  });

  const queue = [...included];
  while (queue.length && included.size < maxNodes) {
    const id = queue.shift();
    edges.forEach((e) => {
      const next = e.source_id === id ? e.target_id : e.target_id === id ? e.source_id : null;
      if (next && nodeIds.has(next) && !included.has(next)) {
        included.add(next);
        queue.push(next);
      }
    });
  }

  if (included.size < Math.min(8, maxNodes)) {
    [...nodes]
      .sort((a, b) => (degree[b.id] || 0) - (degree[a.id] || 0))
      .forEach((n) => {
        if (included.size < maxNodes) included.add(n.id);
      });
  }

  const focusNodes = nodes.filter((n) => included.has(n.id));
  const focusEdges = edges.filter((e) => included.has(e.source_id) && included.has(e.target_id));
  return { nodes: focusNodes, edges: focusEdges };
}

export function layoutSchemaMap(nodes, edges) {
  if (!nodes.length) {
    return { positions: {}, width: 400, height: 200, nodeWidth: NODE_W, nodeHeight: NODE_H };
  }

  const root =
    nodes.find((n) => n.id === 'Account')?.id ||
    nodes.find((n) => CRM_PRIORITY.includes(n.id))?.id ||
    [...nodes].sort((a, b) => {
      const da = edges.filter((e) => e.source_id === a.id || e.target_id === a.id).length;
      const db = edges.filter((e) => e.source_id === b.id || e.target_id === b.id).length;
      return db - da;
    })[0]?.id;

  const layers = new Map();
  const visited = new Set();

  if (root) {
    const queue = [[root, 0]];
    while (queue.length) {
      const [id, layer] = queue.shift();
      if (visited.has(id)) continue;
      visited.add(id);
      if (!layers.has(layer)) layers.set(layer, []);
      layers.get(layer).push(id);
      edges.forEach((e) => {
        if (e.source_id === id && !visited.has(e.target_id)) queue.push([e.target_id, layer + 1]);
      });
    }
  }

  nodes.forEach((n) => {
    if (!visited.has(n.id)) {
      const layer = layers.size;
      if (!layers.has(layer)) layers.set(layer, []);
      layers.get(layer).push(n.id);
    }
  });

  const positions = {};
  let maxLayerSize = 0;
  layers.forEach((ids, layer) => {
    maxLayerSize = Math.max(maxLayerSize, ids.length);
    ids.forEach((id, i) => {
      positions[id] = {
        x: layer * (NODE_W + GAP_X) + 32,
        y: i * (NODE_H + GAP_Y) + 32,
      };
    });
  });

  const width = Math.max(480, layers.size * (NODE_W + GAP_X) + 64);
  const height = Math.max(220, maxLayerSize * (NODE_H + GAP_Y) + 64);

  return { positions, width, height, nodeWidth: NODE_W, nodeHeight: NODE_H };
}

export function buildScheduleConfig({ frequency, timeOfDay, dayOfMonth, month }) {
  if (!frequency || frequency === 'every_15_minutes') return {};
  if (frequency === 'hourly') return { minute: 0 };
  const config = { time_of_day: timeOfDay || '02:00' };
  if (frequency === 'monthly' || frequency === 'yearly') {
    config.day_of_month = dayOfMonth || 1;
  }
  if (frequency === 'yearly') {
    config.month = month || 1;
  }
  return config;
}

export function parseScheduleConfig(schedule) {
  const config = schedule?.schedule_config || {};
  const freq = schedule?.schedule_enabled ? schedule.schedule_frequency : '';
  return {
    frequency: freq || '',
    timeOfDay: config.time_of_day || '02:00',
    dayOfMonth: config.day_of_month ?? 1,
    month: config.month ?? 1,
  };
}
