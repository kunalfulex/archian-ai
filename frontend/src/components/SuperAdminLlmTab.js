import React, { useState, useEffect, useCallback } from 'react';
import api from '../api';

const METRICS_HOURS_OPTIONS = [
  { value: 24, label: 'Last 24 hours' },
  { value: 168, label: 'Last 7 days' },
  { value: 720, label: 'Last 30 days' },
];

const SuperAdminLlmTab = () => {
  const [tasks, setTasks] = useState([]);
  const [providers, setProviders] = useState([]);
  const [models, setModels] = useState([]);
  const [metrics, setMetrics] = useState([]);
  const [timeseries, setTimeseries] = useState([]);
  const [metricsHours, setMetricsHours] = useState(24);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [tasksRes, provRes, modelRes, metricsRes] = await Promise.all([
        api.get('/admin/llm/tasks'),
        api.get('/admin/llm/providers'),
        api.get('/admin/llm/models'),
        api.get('/admin/llm/metrics', { params: { hours: metricsHours } }),
      ]);
      setTasks(tasksRes.data?.tasks || []);
      setProviders(provRes.data?.providers || []);
      setModels(modelRes.data?.models || []);
      setMetrics(metricsRes.data?.summary || []);
      setTimeseries(metricsRes.data?.timeseries || []);
    } catch (e) {
      setError(e.response?.data?.detail || e.message || 'Failed to load LLM settings');
    } finally {
      setLoading(false);
    }
  }, [metricsHours]);

  useEffect(() => {
    load();
  }, [load]);

  const modelsForProvider = (providerId) =>
    models.filter((m) => m.provider_id === providerId);

  const startEdit = (task) => {
    setEditing({
      task_key: task.task_key,
      display_name: task.display_name,
      provider_id: task.provider_id,
      model_id: task.model_id,
      fallback_provider_id: task.fallback_provider_id || '',
      fallback_model_id: task.fallback_model_id || '',
      temperature: task.temperature ?? 0,
      max_tokens: task.max_tokens ?? '',
      max_concurrent: task.max_concurrent ?? 5,
      batch_size: task.batch_size ?? 1,
      notes: task.notes || '',
    });
    setSaveMsg(null);
  };

  const handleSave = async () => {
    if (!editing) return;
    setSaving(true);
    setSaveMsg(null);
    try {
      await api.patch(`/admin/llm/tasks/${editing.task_key}`, {
        provider_id: editing.provider_id,
        model_id: editing.model_id,
        fallback_provider_id: editing.fallback_provider_id || null,
        fallback_model_id: editing.fallback_model_id || null,
        temperature: Number(editing.temperature),
        max_tokens: editing.max_tokens ? Number(editing.max_tokens) : null,
        max_concurrent: Number(editing.max_concurrent),
        batch_size: Number(editing.batch_size),
        notes: editing.notes || null,
      });
      setSaveMsg('Saved. Changes apply within ~60 seconds (config cache).');
      setEditing(null);
      await load();
    } catch (e) {
      setSaveMsg(e.response?.data?.detail || 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  if (loading && tasks.length === 0) {
    return (
      <div className="super-admin-llm-loading">
        <div className="super-admin-spinner" />
        <p>Loading AI model settings...</p>
      </div>
    );
  }

  return (
    <div className="super-admin-llm">
      {error && <div className="super-admin-error">{error}</div>}

      <section className="super-admin-llm-section">
        <h2>Providers</h2>
        <p className="super-admin-llm-hint">
          API keys are set in server environment (OPENAI_API_KEY, ANTHROPIC_API_KEY), not stored here.
        </p>
        <ul className="super-admin-llm-providers">
          {providers.map((p) => (
            <li key={p.id}>
              <strong>{p.display_name}</strong>
              <span className={p.api_key_configured ? 'llm-key-ok' : 'llm-key-missing'}>
                {p.api_key_configured ? 'Key configured' : 'Key missing'}
              </span>
            </li>
          ))}
        </ul>
      </section>

      <section className="super-admin-llm-section">
        <h2>Task routing</h2>
        <p className="super-admin-llm-hint">
          Each Edison AI task uses the provider and model below. Click Edit to change.
        </p>
        <div className="super-admin-llm-table-wrap">
          <table className="super-admin-llm-table">
            <thead>
              <tr>
                <th>Task</th>
                <th>Provider</th>
                <th>Model</th>
                <th>Concurrency</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((t) => (
                <tr key={t.task_key}>
                  <td>
                    <div className="llm-task-name">{t.display_name}</div>
                    <code className="llm-task-key">{t.task_key}</code>
                  </td>
                  <td>{t.provider_id}</td>
                  <td>{t.model_id}</td>
                  <td>
                    {t.max_concurrent} / batch {t.batch_size}
                  </td>
                  <td>
                    <button type="button" className="super-admin-btn-secondary" onClick={() => startEdit(t)}>
                      Edit
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {editing && (
        <div className="super-admin-llm-modal-backdrop" onClick={() => setEditing(null)}>
          <div className="super-admin-llm-modal" onClick={(e) => e.stopPropagation()}>
            <h3>Edit: {editing.display_name}</h3>
            <div className="super-admin-field">
              <label>Provider</label>
              <select
                value={editing.provider_id}
                onChange={(e) =>
                  setEditing({
                    ...editing,
                    provider_id: e.target.value,
                    model_id: modelsForProvider(e.target.value)[0]?.model_id || '',
                  })
                }
              >
                {providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.display_name}
                  </option>
                ))}
              </select>
            </div>
            <div className="super-admin-field">
              <label>Model</label>
              <select
                value={editing.model_id}
                onChange={(e) => setEditing({ ...editing, model_id: e.target.value })}
              >
                {modelsForProvider(editing.provider_id).map((m) => (
                  <option key={m.model_id} value={m.model_id}>
                    {m.display_name} ({m.model_id})
                  </option>
                ))}
              </select>
            </div>
            <div className="super-admin-field">
              <label>Fallback provider (optional)</label>
              <select
                value={editing.fallback_provider_id}
                onChange={(e) =>
                  setEditing({
                    ...editing,
                    fallback_provider_id: e.target.value,
                    fallback_model_id: e.target.value
                      ? modelsForProvider(e.target.value)[0]?.model_id || ''
                      : '',
                  })
                }
              >
                <option value="">None</option>
                {providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.display_name}
                  </option>
                ))}
              </select>
            </div>
            {editing.fallback_provider_id && (
              <div className="super-admin-field">
                <label>Fallback model</label>
                <select
                  value={editing.fallback_model_id}
                  onChange={(e) => setEditing({ ...editing, fallback_model_id: e.target.value })}
                >
                  {modelsForProvider(editing.fallback_provider_id).map((m) => (
                    <option key={m.model_id} value={m.model_id}>
                      {m.display_name}
                    </option>
                  ))}
                </select>
              </div>
            )}
            <div className="super-admin-llm-row-fields">
              <div className="super-admin-field">
                <label>Temperature</label>
                <input
                  type="number"
                  step="0.1"
                  min="0"
                  max="2"
                  value={editing.temperature}
                  onChange={(e) => setEditing({ ...editing, temperature: e.target.value })}
                />
              </div>
              <div className="super-admin-field">
                <label>Max tokens</label>
                <input
                  type="number"
                  placeholder="default"
                  value={editing.max_tokens}
                  onChange={(e) => setEditing({ ...editing, max_tokens: e.target.value })}
                />
              </div>
              <div className="super-admin-field">
                <label>Max concurrent</label>
                <input
                  type="number"
                  min="1"
                  max="50"
                  value={editing.max_concurrent}
                  onChange={(e) => setEditing({ ...editing, max_concurrent: e.target.value })}
                />
              </div>
              <div className="super-admin-field">
                <label>Batch size</label>
                <input
                  type="number"
                  min="1"
                  max="100"
                  value={editing.batch_size}
                  onChange={(e) => setEditing({ ...editing, batch_size: e.target.value })}
                />
              </div>
            </div>
            <div className="super-admin-field">
              <label>Notes</label>
              <input
                type="text"
                value={editing.notes}
                onChange={(e) => setEditing({ ...editing, notes: e.target.value })}
              />
            </div>
            {saveMsg && <p className="super-admin-llm-save-msg">{saveMsg}</p>}
            <div className="super-admin-llm-modal-actions">
              <button type="button" className="super-admin-btn-secondary" onClick={() => setEditing(null)}>
                Cancel
              </button>
              <button type="button" className="super-admin-btn-primary" onClick={handleSave} disabled={saving}>
                {saving ? 'Saving...' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      )}

      <section className="super-admin-llm-section">
        <div className="super-admin-llm-metrics-header">
          <h2>Performance</h2>
          <select value={metricsHours} onChange={(e) => setMetricsHours(Number(e.target.value))}>
            {METRICS_HOURS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <button type="button" className="super-admin-btn-secondary" onClick={load}>
            Refresh
          </button>
        </div>
        {metrics.length === 0 ? (
          <p className="super-admin-llm-hint">No invocations logged yet. Metrics appear after LLM calls run.</p>
        ) : (
          <div className="super-admin-llm-table-wrap">
            <table className="super-admin-llm-table">
              <thead>
                <tr>
                  <th>Task</th>
                  <th>Provider</th>
                  <th>Model</th>
                  <th>Calls</th>
                  <th>Success %</th>
                  <th>Avg ms</th>
                  <th>P95 ms</th>
                  <th>Tokens in/out</th>
                </tr>
              </thead>
              <tbody>
                {metrics.map((m, i) => (
                  <tr key={`${m.task_key}-${m.provider_id}-${m.model_id}-${i}`}>
                    <td><code>{m.task_key}</code></td>
                    <td>{m.provider_id}</td>
                    <td>{m.model_id}</td>
                    <td>{m.call_count}</td>
                    <td>{m.success_rate_pct}%</td>
                    <td>{m.avg_latency_ms}</td>
                    <td>{m.p95_latency_ms}</td>
                    <td>
                      {m.tokens_in} / {m.tokens_out}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {timeseries.length > 0 && (
          <div className="super-admin-llm-timeseries">
            <h3>Hourly volume</h3>
            <ul>
              {timeseries.slice(-12).map((b) => (
                <li key={b.bucket}>
                  {b.bucket}: {b.call_count} calls, avg {b.avg_latency_ms} ms
                  {b.error_count > 0 ? ` (${b.error_count} errors)` : ''}
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>
    </div>
  );
};

export default SuperAdminLlmTab;
