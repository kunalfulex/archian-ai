import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { FiArrowLeft, FiEdit2, FiGitBranch, FiPlus } from 'react-icons/fi';
import api from './api';
import EnterpriseGraphWizard from './EnterpriseGraphWizard';
import EnterpriseGraphAssessment from './components/EnterpriseGraphAssessment';
import {
  googleSheetsScopeLabel,
  loadGoogleSheetsScopeLabelsForMembers,
} from './utils/enterpriseGraphConnectors';
import './EnterpriseGraphsPage.css';

function formatDate(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
    });
  } catch {
    return iso;
  }
}

function GraphList() {
  const navigate = useNavigate();
  const [graphs, setGraphs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await api.get('/api/enterprise-graphs');
      setGraphs(data.graphs || []);
    } catch (e) {
      if (e.response?.status === 503) {
        setError('Enterprise Graph is not enabled on this server.');
      } else {
        setError(e.response?.data?.message || e.message || 'Failed to load graphs');
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="enterprise-graphs-page">
      <button type="button" className="eg-back-link" onClick={() => navigate('/connections')}>
        <FiArrowLeft aria-hidden />
        Connections
      </button>

      <div className="eg-page-header">
        <div>
          <h1>
            <FiGitBranch style={{ verticalAlign: 'middle', marginRight: 8 }} aria-hidden />
            Enterprise graphs
          </h1>
          <p>Map data across Salesforce, SQL, Google Sheets, and your warehouse in one place.</p>
        </div>
        <button type="button" className="eg-primary-btn" onClick={() => navigate('/connections/enterprise-graphs/new')}>
          <FiPlus aria-hidden />
          Create graph
        </button>
      </div>

      {error && <div className="eg-error">{error}</div>}

      {loading && <p className="eg-empty">Loading graphs…</p>}

      {!loading && !error && graphs.length === 0 && (
        <div className="eg-empty">
          <p>No enterprise graphs yet.</p>
          <button
            type="button"
            className="eg-primary-btn"
            style={{ marginTop: 16 }}
            onClick={() => navigate('/connections/enterprise-graphs/new')}
          >
            Create your first graph
          </button>
        </div>
      )}

      {!loading && !error && graphs.length > 0 && (
        <div className="eg-graph-list">
          {graphs.map((g) => (
            <div
              key={g.id}
              className="eg-graph-card"
              role="button"
              tabIndex={0}
              onClick={() => navigate(`/connections/enterprise-graphs/${g.id}`)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  navigate(`/connections/enterprise-graphs/${g.id}`);
                }
              }}
            >
              <h3>{g.name}</h3>
              <div className="eg-graph-card-meta">
                <span className={`eg-status-${g.status || 'draft'}`}>{g.status || 'draft'}</span>
                {' · '}
                Updated {formatDate(g.updated_at)}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function GraphDetail() {
  const { graphId } = useParams();
  const navigate = useNavigate();
  const [graph, setGraph] = useState(null);
  const [memberEntityLabels, setMemberEntityLabels] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const graphRes = await api.get(`/api/enterprise-graphs/${graphId}`);
      const graphData = graphRes.data.graph;
      setGraph(graphData);
      const members = graphData?.members || [];
      const catalogLabels = await loadGoogleSheetsScopeLabelsForMembers(api, members);
      const entitiesRes = await api
        .get(`/api/enterprise-graphs/${graphId}/member-entities`)
        .catch(() => ({ data: {} }));
      const labelsByMember = { ...catalogLabels };
      for (const m of entitiesRes.data?.members || []) {
        labelsByMember[m.member_id] = {
          ...(labelsByMember[m.member_id] || {}),
          ...Object.fromEntries(
            (m.entities || []).map((e) => [e.entity_id, e.label || e.entity_id]),
          ),
        };
      }
      setMemberEntityLabels(labelsByMember);
    } catch (e) {
      setError(e.response?.data?.message || e.message || 'Failed to load graph');
    } finally {
      setLoading(false);
    }
  }, [graphId]);

  useEffect(() => {
    load();
  }, [load]);

  if (loading) {
    return (
      <div className="enterprise-graphs-page">
        <p className="eg-empty">Loading…</p>
      </div>
    );
  }

  if (error || !graph) {
    return (
      <div className="enterprise-graphs-page">
        <div className="eg-error">{error || 'Graph not found'}</div>
        <button type="button" className="eg-back-link" onClick={() => navigate('/connections/enterprise-graphs')}>
          Back to list
        </button>
      </div>
    );
  }

  const members = graph.members || [];

  return (
    <div className="enterprise-graphs-page">
      <button type="button" className="eg-back-link" onClick={() => navigate('/connections/enterprise-graphs')}>
        <FiArrowLeft aria-hidden />
        Enterprise graphs
      </button>

      <div className="eg-page-header">
        <div>
          <h1>{graph.name}</h1>
          {graph.description && <p>{graph.description}</p>}
          <p className="eg-graph-card-meta">
            Status: <span className={`eg-status-${graph.status}`}>{graph.status}</span>
            {' · '}
            {members.length} connector{members.length === 1 ? '' : 's'}
          </p>
        </div>
        <button
          type="button"
          className="eg-secondary-btn"
          onClick={() => navigate(`/connections/enterprise-graphs/${graphId}/edit`)}
        >
          <FiEdit2 aria-hidden />
          Edit
        </button>
      </div>

      <div className="eg-wizard-card">
        <h2>Connected sources</h2>
        {members.length === 0 ? (
          <p className="eg-empty">No connectors linked. Edit this graph to add members.</p>
        ) : (
          <div className="eg-member-chips">
            {members.map((m) => {
              const gsScope = googleSheetsScopeLabel(m, memberEntityLabels[m.id]);
              return (
              <span key={m.id} className="eg-member-chip">
                {m.display_name || m.connector_type}
                <span style={{ color: '#9ca3af' }}> ({m.role})</span>
                {gsScope && (
                  <span className="eg-member-chip-scope"> · {gsScope}</span>
                )}
              </span>
              );
            })}
          </div>
        )}

      {graph.settings?.assessment_stale && (
        <div className="eg-stale-banner" role="status">
          Graph connectors changed since the last assessment. Run data assessment to refresh mappings,
          lake, and warehouse proposals.
        </div>
      )}

      <EnterpriseGraphAssessment
        graphId={graphId}
        memberCount={members.length}
        lakeEnabled={Boolean(graph.settings?.lake_enabled)}
        assessmentStale={Boolean(graph.settings?.assessment_stale)}
        onGraphRefresh={load}
      />
      </div>
    </div>
  );
}

export default function EnterpriseGraphsPage() {
  const { graphId } = useParams();
  const location = window.location.pathname;

  if (location.endsWith('/new')) {
    return <EnterpriseGraphWizard />;
  }
  if (location.endsWith('/edit') && graphId) {
    return <EnterpriseGraphWizard graphId={graphId} />;
  }
  if (graphId) {
    return <GraphDetail />;
  }
  return <GraphList />;
}
