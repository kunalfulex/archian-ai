import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { FiArrowLeft, FiCheck } from 'react-icons/fi';
import api from './api';
import EnterpriseGraphCanvasPicker from './components/EnterpriseGraphCanvasPicker';
import {
  connectorIdFromMember,
  fetchAvailableConnectors,
} from './utils/enterpriseGraphConnectors';
import './EnterpriseGraphsPage.css';

function normalizeMemberRole(role) {
  const r = (role || 'source').toLowerCase();
  if (r === 'warehouse' || r === 'lake') return r;
  return 'source';
}

export default function EnterpriseGraphWizard({ graphId: editGraphId }) {
  const navigate = useNavigate();
  const isEdit = Boolean(editGraphId);

  const [step, setStep] = useState(1);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [lakeEnabled, setLakeEnabled] = useState(false);
  const [connectorOptions, setConnectorOptions] = useState([]);
  const [canvasNodes, setCanvasNodes] = useState([]);
  const [canvasEdges, setCanvasEdges] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const savedFingerprintRef = useRef(null);

  const graphFingerprint = useMemo(
    () =>
      JSON.stringify({
        name: name.trim(),
        description: description.trim(),
        lakeEnabled,
        canvasNodes,
        canvasEdges,
      }),
    [name, description, lakeEnabled, canvasNodes, canvasEdges],
  );

  const isDirty = !isEdit || savedFingerprintRef.current !== graphFingerprint;

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const options = await fetchAvailableConnectors(api);
      setConnectorOptions(options);

      if (isEdit) {
        const { data } = await api.get(`/api/enterprise-graphs/${editGraphId}`);
        const graph = data.graph || {};
        setName(graph.name || '');
        setDescription(graph.description || '');
        setLakeEnabled(Boolean(graph.settings?.lake_enabled));
        const layout = graph.settings?.layout || {};
        const savedPos = new Map((layout.nodes || []).map((n) => [n.optionKey, n]));
        const nodes = [];
        (graph.members || []).forEach((m, idx) => {
          const cid = connectorIdFromMember(m);
          const key = `${m.connector_type}:${cid}`;
          const role = normalizeMemberRole(m.role);
          const saved = savedPos.get(key);
          const refAllowlist = m.connector_ref?.sheet_entity_allowlist;
          const hasRefAllowlist =
            m.connector_type === 'google_sheets'
            && m.connector_ref
            && Object.prototype.hasOwnProperty.call(m.connector_ref, 'sheet_entity_allowlist');
          nodes.push({
            id: saved?.id || `m-${m.id || key}`,
            optionKey: key,
            role,
            x: saved?.x ?? 48 + (idx % 3) * 236,
            y: saved?.y ?? 48 + Math.floor(idx / 3) * 100,
            sheetAllowlist: hasRefAllowlist
              ? refAllowlist
              : (saved?.sheetAllowlist ?? undefined),
          });
        });
        setCanvasNodes(nodes);
        setCanvasEdges(layout.edges || []);
        savedFingerprintRef.current = JSON.stringify({
          name: (graph.name || '').trim(),
          description: (graph.description || '').trim(),
          lakeEnabled: Boolean(graph.settings?.lake_enabled),
          canvasNodes: nodes,
          canvasEdges: layout.edges || [],
        });
      }
    } catch (e) {
      setError(e.response?.data?.detail || e.response?.data?.message || e.message || 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, [editGraphId, isEdit]);

  useEffect(() => {
    load();
  }, [load]);

  const sourceCount = useMemo(
    () => canvasNodes.filter((n) => n.role === 'source').length,
    [canvasNodes],
  );

  const hasLakeMember = useMemo(
    () => canvasNodes.some((n) => n.role === 'lake'),
    [canvasNodes],
  );

  const buildMembersPayload = () => {
    const optByKey = new Map(connectorOptions.map((o) => [o.key, o]));
    return canvasNodes
      .map((n) => {
        const o = optByKey.get(n.optionKey);
        if (!o || o.isVirtual) return null;
        const role = normalizeMemberRole(n.role);
        return {
          connector_type: o.connector_type,
          connector_id: o.connector_id,
          role,
          display_name: o.label,
          ...(o.connector_type === 'google_sheets'
            ? {
                sheet_entity_allowlist:
                  n.sheetAllowlist === undefined ? null : n.sheetAllowlist,
              }
            : {}),
        };
      })
      .filter(Boolean);
  };

  const buildGraphSettings = () => {
    const settings = {
      lake_enabled: lakeEnabled && hasLakeMember,
      layout: {
        version: 1,
        nodes: canvasNodes.map(({ id, optionKey, role, x, y, sheetAllowlist }) => ({
          id,
          optionKey,
          role,
          x,
          y,
          ...(sheetAllowlist !== undefined ? { sheetAllowlist } : {}),
        })),
        edges: canvasEdges,
      },
    };
    const whNode = canvasNodes.find((n) => n.role === 'warehouse');
    if (whNode) {
      const o = connectorOptions.find((x) => x.key === whNode.optionKey);
      if (o?.connector_type === 'sql') {
        const sub = (o.subtitle || '').toLowerCase();
        settings.warehouse_dialect = sub.includes('snowflake') ? 'snowflake' : 'postgres';
      }
    }
    const lakeNode = canvasNodes.find((n) => n.role === 'lake');
    if (lakeNode) {
      const o = connectorOptions.find((x) => x.key === lakeNode.optionKey);
      if (o?.connector_type === 'sql') {
        settings.lake_dialect = 'snowflake';
      }
    }
    return settings;
  };

  const handleSave = async () => {
    setError(null);
    if (!name.trim()) {
      setError('Enter a name for this enterprise graph.');
      setStep(1);
      return;
    }
    if (sourceCount < 2) {
      setError('Add at least two source connectors to the canvas.');
      setStep(2);
      return;
    }
    if (lakeEnabled && !hasLakeMember) {
      setError('Enable medallion lake requires one Snowflake connector set as Lake on the canvas.');
      setStep(2);
      return;
    }

    setSaving(true);
    try {
      let id = editGraphId;
      if (isEdit) {
        const settings = buildGraphSettings();
        await api.patch(`/api/enterprise-graphs/${editGraphId}`, {
          name: name.trim(),
          description: description.trim() || null,
          settings,
        });
      } else {
        const { data } = await api.post('/api/enterprise-graphs', {
          name: name.trim(),
          description: description.trim() || null,
          settings: buildGraphSettings(),
        });
        id = data.graph?.id;
        if (!id) throw new Error('No graph id returned');
      }

      await api.put(`/api/enterprise-graphs/${id}/members`, {
        members: buildMembersPayload(),
      });

      await api.patch(`/api/enterprise-graphs/${id}`, { status: 'active' });
      savedFingerprintRef.current = graphFingerprint;
      navigate(`/connections/enterprise-graphs/${id}`);
    } catch (e) {
      const detail = e.response?.data?.detail;
      setError(
        typeof detail === 'string'
          ? detail
          : detail?.message || e.response?.data?.message || e.message || 'Save failed',
      );
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="enterprise-graphs-page">
        <p className="eg-empty">Loading…</p>
      </div>
    );
  }

  return (
    <div className="enterprise-graphs-page">
      <button type="button" className="eg-back-link" onClick={() => navigate('/connections/enterprise-graphs')}>
        <FiArrowLeft aria-hidden />
        Enterprise graphs
      </button>

      <div className="eg-page-header">
        <div>
          <h1>{isEdit ? 'Edit enterprise graph' : 'Create enterprise graph'}</h1>
          <p>Connect Salesforce, databases, and Google Sheets into one data landscape.</p>
        </div>
      </div>

      {error && <div className="eg-error">{error}</div>}

      {step === 1 && (
        <div className="eg-wizard-card">
          <h2>Step 1 — Name</h2>
          <p className="eg-wizard-hint">Give this graph a name your team will recognize.</p>
          <div className="eg-form-field">
            <label htmlFor="eg-name">Graph name</label>
            <input
              id="eg-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. CRM + warehouse landscape"
              maxLength={255}
            />
          </div>
          <div className="eg-form-field">
            <label htmlFor="eg-desc">Description (optional)</label>
            <textarea
              id="eg-desc"
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What systems does this graph include?"
            />
          </div>
          <div className="eg-wizard-actions">
            <button type="button" className="eg-secondary-btn" onClick={() => navigate('/connections/enterprise-graphs')}>
              Cancel
            </button>
            <button
              type="button"
              className="eg-primary-btn"
              disabled={!name.trim()}
              onClick={() => setStep(2)}
            >
              Next — Select connectors
            </button>
          </div>
        </div>
      )}

      {step === 2 && (
        <div className="eg-wizard-card eg-wizard-card--wide">
          <h2>Step 2 — Build your landscape</h2>
          <p className="eg-wizard-hint">
            Drop connectors on the canvas. Snowflake can be assigned as <strong>Lake</strong> (bronze/silver)
            or <strong>Warehouse</strong> (gold). Draw cross-map links between sources or load lines into lake/warehouse.
          </p>

          <label className="eg-depth-option eg-lake-enable-option">
            <input
              type="checkbox"
              checked={lakeEnabled}
              onChange={(e) => setLakeEnabled(e.target.checked)}
            />
            <div>
              <strong>Enable medallion data lake</strong>
              <span>
                Assessment will propose bronze raw tables and silver views on your Lake Snowflake member.
              </span>
            </div>
          </label>

          {connectorOptions.length === 0 ? (
            <p className="eg-empty">
              No connectors found. Add Salesforce, SQL, or Google Sheets on the Connections page first.
            </p>
          ) : (
            <EnterpriseGraphCanvasPicker
              connectorOptions={connectorOptions}
              canvasNodes={canvasNodes}
              onCanvasNodesChange={setCanvasNodes}
              canvasEdges={canvasEdges}
              onCanvasEdgesChange={setCanvasEdges}
              lakeEnabled={lakeEnabled}
            />
          )}

          <p className="eg-wizard-hint" style={{ marginTop: 16 }}>
            {sourceCount} source{sourceCount === 1 ? '' : 's'} on canvas{' '}
            {sourceCount >= 2 ? '✓' : `(need ${2 - sourceCount} more)`}
            {canvasNodes.some((n) => n.role === 'lake') ? ' · lake set' : ''}
            {canvasNodes.some((n) => n.role === 'warehouse') ? ' · warehouse set' : ''}
            {canvasEdges.length > 0 ? ` · ${canvasEdges.length} connection${canvasEdges.length === 1 ? '' : 's'}` : ''}
          </p>

          <div className="eg-wizard-actions">
            <button type="button" className="eg-secondary-btn" onClick={() => setStep(1)}>
              Back
            </button>
            <button
              type="button"
              className="eg-primary-btn"
              disabled={sourceCount < 2 || saving || (isEdit && !isDirty)}
              onClick={handleSave}
            >
              <FiCheck aria-hidden />
              {saving ? 'Saving…' : isEdit ? 'Save graph' : 'Create graph'}
            </button>
            {isEdit && !isDirty && (
              <span className="eg-wizard-hint" style={{ alignSelf: 'center' }}>
                No unsaved changes
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
