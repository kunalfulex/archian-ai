import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import api from './api';
import { useFeatures } from './hooks/useFeatures';
import SuperAdminLlmTab from './components/SuperAdminLlmTab';
import SuperAdminLearningTab from './components/SuperAdminLearningTab';
import './SuperAdminPage.css';

const DEBOUNCE_MS = 300;

const TAB_CREATE = 'create';
const TAB_LINKS = 'links';
const TAB_PLANS = 'plans';
const TAB_RETENTION = 'retention';
const TAB_LLM = 'llm';
const TAB_LEARNING = 'learning';
const BILLING_TABS = [TAB_CREATE, TAB_LINKS, TAB_PLANS];

function formatLinkDate(value) {
  if (value == null || value === '') return '—';
  // Normalize API dates that have both offset and "Z" (e.g. "...+00:00Z") so JS can parse
  const str = typeof value === 'string' ? value.replace(/([+-]\d{2}:\d{2})Z$/i, '$1') : value;
  const d = new Date(str);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString();
}

const SuperAdminPage = () => {
  const navigate = useNavigate();
  const { billing, loading: featuresLoading } = useFeatures();
  const [activeTab, setActiveTab] = useState(TAB_CREATE);
  const [allowed, setAllowed] = useState(null);
  const [loading, setLoading] = useState(true);
  const [customPlans, setCustomPlans] = useState([]);
  const [users, setUsers] = useState([]);
  const [userSearch, setUserSearch] = useState('');
  const [selectedUser, setSelectedUser] = useState(null);
  const [selectedPlanType, setSelectedPlanType] = useState('');
  const [expiresInDays, setExpiresInDays] = useState(7);
  const [generating, setGenerating] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [usersLoading, setUsersLoading] = useState(false);
  const [showUserDropdown, setShowUserDropdown] = useState(false);
  const [links, setLinks] = useState([]);
  const [linksLoading, setLinksLoading] = useState(false);
  const [linksError, setLinksError] = useState(null);
  const [newPlanType, setNewPlanType] = useState('');
  const [newPlanCredits, setNewPlanCredits] = useState(-1);
  const [newPlanPriceMonthly, setNewPlanPriceMonthly] = useState('');
  const [newPlanPriceYearly, setNewPlanPriceYearly] = useState('');
  const [newPlanFileSizeMb, setNewPlanFileSizeMb] = useState('');
  const [newPlanFeatures, setNewPlanFeatures] = useState(
    'Chat assistant\nTeam\nCustom Integration\nExport Results\nEmail & Call support'
  );
  const [createPlanLoading, setCreatePlanLoading] = useState(false);
  const [createPlanError, setCreatePlanError] = useState(null);
  const [createPlanSuccess, setCreatePlanSuccess] = useState(null);
  const [editingPlan, setEditingPlan] = useState(null);
  const [editPlanCredits, setEditPlanCredits] = useState(-1);
  const [editPlanPriceMonthly, setEditPlanPriceMonthly] = useState('');
  const [editPlanPriceYearly, setEditPlanPriceYearly] = useState('');
  const [editPlanFileSizeMb, setEditPlanFileSizeMb] = useState('');
  const [editPlanFeatures, setEditPlanFeatures] = useState('');
  const [editPlanSaving, setEditPlanSaving] = useState(false);
  const [editPlanError, setEditPlanError] = useState(null);
  const [deletingPlanType, setDeletingPlanType] = useState(null);
  const [retentionLoading, setRetentionLoading] = useState(false);
  const [retentionResult, setRetentionResult] = useState(null);
  const [retentionError, setRetentionError] = useState(null);
  const [retentionUserSearch, setRetentionUserSearch] = useState('');
  const [retentionUsers, setRetentionUsers] = useState([]);
  const [retentionSelectedUser, setRetentionSelectedUser] = useState(null);
  const [retentionUsersLoading, setRetentionUsersLoading] = useState(false);
  const [retentionShowUserDropdown, setRetentionShowUserDropdown] = useState(false);
  const [retentionSearchError, setRetentionSearchError] = useState(null);
  const searchTimeoutRef = useRef(null);
  const retentionSearchTimeoutRef = useRef(null);
  const dropdownRef = useRef(null);
  const retentionDropdownRef = useRef(null);

  const fetchCustomPlans = useCallback(async () => {
    try {
      const plansRes = await api.get('/admin/custom-plans');
      if (plansRes.data?.plans) setCustomPlans(plansRes.data.plans);
    } catch (e) {
      console.error('Failed to fetch custom plans:', e);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const profileRes = await api.get('/profile');
        let isSuperAdmin = profileRes.data?.data?.is_super_admin;
        if (isSuperAdmin === undefined) {
          const planRes = await api.get('/user-plan');
          isSuperAdmin = planRes.data?.is_super_admin;
        }
        isSuperAdmin = isSuperAdmin === true;
        if (!cancelled) {
          setAllowed(isSuperAdmin);
          if (!isSuperAdmin) {
            navigate('/chat', { replace: true });
            return;
          }
        }
      } catch (e) {
        if (!cancelled) {
          setAllowed(false);
          navigate('/chat', { replace: true });
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [navigate]);

  useEffect(() => {
    if (allowed && !featuresLoading && billing) fetchCustomPlans();
  }, [allowed, billing, featuresLoading, fetchCustomPlans]);

  useEffect(() => {
    if (!featuresLoading && !billing && BILLING_TABS.includes(activeTab)) {
      setActiveTab(TAB_RETENTION);
    }
  }, [billing, featuresLoading, activeTab]);

  useEffect(() => {
    if (!loading && allowed === false) {
      navigate('/chat', { replace: true });
    }
  }, [loading, allowed, navigate]);

  const searchUsers = useCallback(async (q) => {
    if (!q || q.trim().length < 2) {
      setUsers([]);
      return;
    }
    setUsersLoading(true);
    setError(null);
    try {
      const res = await api.get('/admin/users', {
        params: { q: q.trim(), limit: 20 },
      });
      setUsers(res.data?.users || []);
      setShowUserDropdown(true);
    } catch (e) {
      setUsers([]);
      const status = e.response?.status;
      const detail = e.response?.data?.detail || e.message;
      if (status === 403) {
        setError('Access denied. Super admin only.');
      } else if (status === 404) {
        setError('Endpoint not found. Check API URL.');
      } else {
        setError(detail || 'Search failed. Try again.');
      }
      console.error('Admin user search failed:', status, detail, e);
    } finally {
      setUsersLoading(false);
    }
  }, []);

  useEffect(() => {
    if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
    if (!userSearch.trim()) {
      setUsers([]);
      setShowUserDropdown(false);
      return;
    }
    searchTimeoutRef.current = setTimeout(() => searchUsers(userSearch), DEBOUNCE_MS);
    return () => { if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current); };
  }, [userSearch, searchUsers]);

  const searchRetentionUsers = useCallback(async (q) => {
    if (!q || q.trim().length < 2) {
      setRetentionUsers([]);
      return;
    }
    setRetentionUsersLoading(true);
    setRetentionSearchError(null);
    try {
      const res = await api.get('/admin/users', {
        params: { q: q.trim(), limit: 20 },
      });
      setRetentionUsers(res.data?.users || []);
      setRetentionShowUserDropdown(true);
    } catch (e) {
      setRetentionUsers([]);
      const status = e.response?.status;
      const detail = e.response?.data?.detail || e.message;
      if (status === 403) {
        setRetentionSearchError('Access denied. Super admin only.');
      } else if (status === 404) {
        setRetentionSearchError('Endpoint not found. Check API URL.');
      } else {
        setRetentionSearchError(detail || 'Search failed. Try again.');
      }
      console.error('Retention user search failed:', status, detail, e);
    } finally {
      setRetentionUsersLoading(false);
    }
  }, []);

  useEffect(() => {
    if (retentionSearchTimeoutRef.current) clearTimeout(retentionSearchTimeoutRef.current);
    if (!retentionUserSearch.trim()) {
      setRetentionUsers([]);
      setRetentionShowUserDropdown(false);
      return;
    }
    retentionSearchTimeoutRef.current = setTimeout(
      () => searchRetentionUsers(retentionUserSearch),
      DEBOUNCE_MS
    );
    return () => {
      if (retentionSearchTimeoutRef.current) clearTimeout(retentionSearchTimeoutRef.current);
    };
  }, [retentionUserSearch, searchRetentionUsers]);

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) setShowUserDropdown(false);
      if (retentionDropdownRef.current && !retentionDropdownRef.current.contains(e.target)) {
        setRetentionShowUserDropdown(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const openEditPlan = (p) => {
    setEditingPlan(p);
    setEditPlanCredits(p.credits_per_month ?? -1);
    setEditPlanPriceMonthly(p.price_monthly != null ? String(p.price_monthly) : '');
    setEditPlanPriceYearly(p.price_yearly != null ? String(p.price_yearly) : '');
    setEditPlanFileSizeMb(p.max_file_size_mb != null ? String(p.max_file_size_mb) : '');
    let featuresStr = '';
    if (Array.isArray(p.features)) {
      featuresStr = p.features.join('\n');
    } else if (typeof p.features === 'string' && p.features.trim()) {
      try {
        const parsed = JSON.parse(p.features);
        featuresStr = Array.isArray(parsed) ? parsed.join('\n') : p.features;
      } catch {
        featuresStr = p.features;
      }
    }
    setEditPlanFeatures(featuresStr);
    setEditPlanError(null);
  };

  const closeEditPlan = () => {
    setEditingPlan(null);
    setEditPlanError(null);
  };

  const handleDeletePlan = async (planType) => {
    if (!planType || !window.confirm(`Delete custom plan "${planType}"? This cannot be undone.`)) return;
    setDeletingPlanType(planType);
    try {
      await api.delete(`/admin/custom-plans/${encodeURIComponent(planType)}`);
      await fetchCustomPlans();
    } catch (e) {
      window.alert(e.response?.data?.detail || e.message || 'Failed to delete plan.');
    } finally {
      setDeletingPlanType(null);
    }
  };

  const handleSaveEditPlan = async () => {
    if (!editingPlan?.plan_type) return;
    setEditPlanError(null);
    setEditPlanSaving(true);
    try {
      const featuresList = editPlanFeatures.trim()
        ? editPlanFeatures.split('\n').map((s) => s.trim()).filter(Boolean)
        : null;
      const credits = (editPlanCredits === '' || editPlanCredits == null || editPlanCredits === -1) ? -1 : Number(editPlanCredits);
      await api.post('/admin/custom-plans', {
        plan_type: editingPlan.plan_type,
        credits_per_month: credits,
        price_monthly: editPlanPriceMonthly === '' ? null : Number(editPlanPriceMonthly),
        price_yearly: editPlanPriceYearly === '' ? null : Number(editPlanPriceYearly),
        max_file_size_mb: editPlanFileSizeMb === '' ? null : Number(editPlanFileSizeMb),
        features: featuresList,
      });
      closeEditPlan();
      await fetchCustomPlans();
    } catch (e) {
      setEditPlanError(e.response?.data?.detail || e.message || 'Failed to update plan.');
    } finally {
      setEditPlanSaving(false);
    }
  };

  const handleCreateCustomPlan = async () => {
    const planType = (newPlanType || '').trim();
    if (!planType) {
      setCreatePlanError('Plan type is required (e.g. Enterprise - Acme).');
      return;
    }
    setCreatePlanError(null);
    setCreatePlanSuccess(null);
    setCreatePlanLoading(true);
    try {
      const featuresList = newPlanFeatures.trim()
        ? newPlanFeatures.split('\n').map((s) => s.trim()).filter(Boolean)
        : null;
      const credits = (newPlanCredits === '' || newPlanCredits == null || newPlanCredits === -1) ? -1 : Number(newPlanCredits);
      await api.post('/admin/custom-plans', {
        plan_type: planType,
        credits_per_month: credits,
        price_monthly: newPlanPriceMonthly === '' ? null : Number(newPlanPriceMonthly),
        price_yearly: newPlanPriceYearly === '' ? null : Number(newPlanPriceYearly),
        max_file_size_mb: newPlanFileSizeMb === '' ? null : Number(newPlanFileSizeMb),
        features: featuresList,
      });
      setCreatePlanSuccess(`Custom plan "${planType}" created. You can use it in "Create link" to generate payment links.`);
      setNewPlanType('');
      setNewPlanCredits(-1);
      setNewPlanPriceMonthly('');
      setNewPlanPriceYearly('');
      setNewPlanFileSizeMb('');
      setNewPlanFeatures('');
      await fetchCustomPlans();
    } catch (e) {
      setCreatePlanError(e.response?.data?.detail || e.message || 'Failed to create custom plan.');
    } finally {
      setCreatePlanLoading(false);
    }
  };

  const handleGenerateLink = async () => {
    if (!selectedUser?.id || !selectedPlanType) {
      setError('Please select a user and a plan.');
      return;
    }
    setError(null);
    setResult(null);
    setGenerating(true);
    try {
      const res = await api.post('/admin/create-custom-plan-link', {
        user_id: selectedUser.id,
        plan_type: selectedPlanType,
        expires_in_days: expiresInDays,
      });
      setResult({
        url: res.data.url,
        expires_at: res.data.expires_at,
      });
    } catch (e) {
      setError(e.response?.data?.detail || e.message || 'Failed to create link.');
    } finally {
      setGenerating(false);
    }
  };

  const copyUrl = () => {
    if (!result?.url) return;
    navigator.clipboard.writeText(result.url).then(() => {
      const btn = document.getElementById('copy-link-btn');
      if (btn) {
        const orig = btn.textContent;
        btn.textContent = 'Copied!';
        setTimeout(() => { btn.textContent = orig; }, 2000);
      }
    });
  };

  const fetchLinks = useCallback(async () => {
    setLinksLoading(true);
    setLinksError(null);
    try {
      const res = await api.get('/admin/custom-plan-links');
      setLinks(res.data?.links || []);
    } catch (e) {
      setLinks([]);
      setLinksError(e.response?.data?.detail || e.message || 'Failed to load links.');
    } finally {
      setLinksLoading(false);
    }
  }, []);

  useEffect(() => {
    if (activeTab === TAB_LINKS && allowed) fetchLinks();
  }, [activeTab, allowed, fetchLinks]);

  const getLinkStatus = (link) => {
    if (link.used_at) return { label: 'Used', className: 'super-admin-status-used' };
    const expStr = link.expires_at && (typeof link.expires_at === 'string' ? link.expires_at.replace(/([+-]\d{2}:\d{2})Z$/i, '$1') : link.expires_at);
    const exp = expStr ? new Date(expStr) : null;
    if (exp && !Number.isNaN(exp.getTime()) && exp.getTime() < Date.now()) return { label: 'Expired', className: 'super-admin-status-expired' };
    return { label: 'Pending', className: 'super-admin-status-pending' };
  };

  const copyLinkUrl = (url, btnId) => {
    if (!url) return;
    navigator.clipboard.writeText(url).then(() => {
      const btn = document.getElementById(btnId);
      if (btn) {
        const orig = btn.textContent;
        btn.textContent = 'Copied!';
        setTimeout(() => { btn.textContent = orig; }, 2000);
      }
    });
  };

  const [deletingId, setDeletingId] = useState(null);
  const runDataRetention = async (scopeAll) => {
    if (scopeAll) {
      if (
        !window.confirm(
          'Run data retention for ALL users now? This removes eligible row-level extraction and migration data (and file uploads) per each account’s policy. This cannot be undone.'
        )
      ) {
        return;
      }
    } else {
      if (!retentionSelectedUser?.id) return;
      if (
        !window.confirm(
          `Run data retention only for ${retentionSelectedUser.email}? This removes eligible old data for that user only.`
        )
      ) {
        return;
      }
    }
    setRetentionLoading(true);
    setRetentionError(null);
    setRetentionResult(null);
    try {
      const res = await api.post(
        '/admin/data-retention/run',
        {},
        scopeAll ? {} : { params: { user_id: retentionSelectedUser.id } }
      );
      setRetentionResult(res.data);
    } catch (e) {
      const detail = e.response?.data?.detail;
      setRetentionError(
        typeof detail === 'string'
          ? detail
          : detail
            ? JSON.stringify(detail)
            : e.message || 'Request failed'
      );
    } finally {
      setRetentionLoading(false);
    }
  };

  const handleDeleteLink = async (linkId) => {
    if (!window.confirm('Delete this payment link? This cannot be undone.')) return;
    setDeletingId(linkId);
    setLinksError(null);
    try {
      await api.delete(`/admin/custom-plan-links/${linkId}`);
      setLinks((prev) => prev.filter((l) => l.id !== linkId));
    } catch (e) {
      setLinksError(e.response?.data?.detail || e.message || 'Failed to delete link.');
    } finally {
      setDeletingId(null);
    }
  };

  if (loading) {
    return (
      <div className="super-admin-page">
        <div className="super-admin-loading">
          <div className="super-admin-spinner" />
          <p>Checking access...</p>
        </div>
      </div>
    );
  }

  if (allowed !== true) {
    return null;
  }

  return (
    <div className="super-admin-page">
      <div className="super-admin-header">
        <h1>Super admin</h1>
        <p>
          {billing
            ? 'Custom plan payment links, plans, data retention, and AI model routing. Use the tabs below.'
            : 'Data retention, AI model routing, and learning. Use the tabs below.'}
        </p>
      </div>

      <div className="super-admin-tabs">
        {billing && (
          <>
            <button
              type="button"
              className={`super-admin-tab ${activeTab === TAB_CREATE ? 'active' : ''}`}
              onClick={() => setActiveTab(TAB_CREATE)}
            >
              Create link
            </button>
            <button
              type="button"
              className={`super-admin-tab ${activeTab === TAB_LINKS ? 'active' : ''}`}
              onClick={() => setActiveTab(TAB_LINKS)}
            >
              All links
            </button>
            <button
              type="button"
              className={`super-admin-tab ${activeTab === TAB_PLANS ? 'active' : ''}`}
              onClick={() => setActiveTab(TAB_PLANS)}
            >
              Custom plans
            </button>
          </>
        )}
        <button
          type="button"
          className={`super-admin-tab ${activeTab === TAB_RETENTION ? 'active' : ''}`}
          onClick={() => setActiveTab(TAB_RETENTION)}
        >
          Data retention
        </button>
        <button
          type="button"
          className={`super-admin-tab ${activeTab === TAB_LLM ? 'active' : ''}`}
          onClick={() => setActiveTab(TAB_LLM)}
        >
          AI models
        </button>
        <button
          type="button"
          className={`super-admin-tab ${activeTab === TAB_LEARNING ? 'active' : ''}`}
          onClick={() => setActiveTab(TAB_LEARNING)}
        >
          Learning &amp; RAG
        </button>
      </div>

      {activeTab === TAB_LLM && <SuperAdminLlmTab />}
      {activeTab === TAB_LEARNING && <SuperAdminLearningTab />}

      {activeTab === TAB_CREATE && (
      <div className="super-admin-form">
        <div className="super-admin-field">
          <label>User</label>
          <div className="super-admin-user-search" ref={dropdownRef}>
            <input
              type="text"
              placeholder="Search by email or name (min 2 chars)"
              value={selectedUser ? `${selectedUser.email}${(selectedUser.first_name || selectedUser.last_name) ? ` (${[selectedUser.first_name, selectedUser.last_name].filter(Boolean).join(' ')})` : ''}` : userSearch}
              onChange={(e) => {
                setSelectedUser(null);
                setUserSearch(e.target.value);
              }}
              onFocus={() => users.length > 0 && setShowUserDropdown(true)}
            />
            {selectedUser && (
              <button type="button" className="super-admin-clear-user" onClick={() => { setSelectedUser(null); setUserSearch(''); setUsers([]); }}>Clear</button>
            )}
            {usersLoading && <span className="super-admin-search-loading">Searching...</span>}
            {showUserDropdown && users.length > 0 && (
              <ul className="super-admin-user-dropdown">
                {users.map((u) => (
                  <li
                    key={u.id}
                    onClick={() => {
                      setSelectedUser(u);
                      setUserSearch('');
                      setUsers([]);
                      setShowUserDropdown(false);
                    }}
                  >
                    {u.email} {[u.first_name, u.last_name].filter(Boolean).join(' ') && `(${[u.first_name, u.last_name].filter(Boolean).join(' ')})`}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        <div className="super-admin-field">
          <label>Plan</label>
          <select
            value={selectedPlanType}
            onChange={(e) => setSelectedPlanType(e.target.value)}
          >
            <option value="">Select a custom plan</option>
            {customPlans.map((p) => (
              <option key={p.plan_type} value={p.plan_type}>
                {p.plan_type} {p.price_monthly != null ? `– $${p.price_monthly}` : ''}
              </option>
            ))}
          </select>
        </div>

        <div className="super-admin-field">
          <label>Link expires in (days)</label>
          <input
            type="number"
            min={1}
            max={90}
            value={expiresInDays}
            onChange={(e) => setExpiresInDays(Number(e.target.value) || 7)}
          />
        </div>

        {error && <div className="super-admin-error">{error}</div>}

        <button
          type="button"
          className="super-admin-generate-btn"
          onClick={handleGenerateLink}
          disabled={generating || !selectedUser?.id || !selectedPlanType}
        >
          {generating ? 'Generating...' : 'Generate link'}
        </button>

        {result && (
          <div className="super-admin-result">
            <label>Payment URL</label>
            <div className="super-admin-result-url">
              <input type="text" readOnly value={result.url} />
              <button type="button" id="copy-link-btn" onClick={copyUrl}>Copy</button>
            </div>
            <p className="super-admin-result-expires">Expires: {formatLinkDate(result.expires_at)}</p>
          </div>
        )}
      </div>
      )}

      {activeTab === TAB_LINKS && (
        <div className="super-admin-links-panel">
          <div className="super-admin-links-toolbar">
            <span className="super-admin-links-count">{links.length} link{links.length !== 1 ? 's' : ''}</span>
            <button type="button" className="super-admin-refresh-btn" onClick={fetchLinks} disabled={linksLoading}>
              {linksLoading ? 'Loading...' : 'Refresh'}
            </button>
          </div>
          {linksError && <div className="super-admin-error">{linksError}</div>}
          {linksLoading && links.length === 0 ? (
            <div className="super-admin-loading small">
              <div className="super-admin-spinner" />
              <p>Loading links...</p>
            </div>
          ) : links.length === 0 ? (
            <p className="super-admin-empty">No custom plan links yet. Create one in the &quot;Create link&quot; tab.</p>
          ) : (
            <div className="super-admin-links-table-wrap">
              <table className="super-admin-links-table">
                <thead>
                  <tr>
                    <th>User</th>
                    <th>Plan</th>
                    <th>Created</th>
                    <th>Expires</th>
                    <th>Status</th>
                    <th className="super-admin-actions-col">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {links.map((link) => {
                    const status = getLinkStatus(link);
                    const isDeleting = deletingId === link.id;
                    return (
                      <tr key={link.id}>
                        <td
                          className="super-admin-td-user"
                          style={{
                            width: '220px',
                            minWidth: '220px',
                            maxWidth: '320px',
                          }}
                        >
                          <div
                            className="super-admin-user-cell"
                            style={{
                              display: 'block',
                              minWidth: '220px',
                              maxWidth: '320px',
                              wordBreak: 'break-word',
                              overflow: 'visible',
                            }}
                          >
                            <div
                              style={{
                                display: 'block',
                                lineHeight: 1.4,
                                marginBottom: '4px',
                                wordBreak: 'break-all',
                                fontWeight: 500,
                                color: '#1f2937',
                              }}
                            >
                              {link.user_email}
                            </div>
                            {link.user_name && (
                              <div
                                style={{
                                  display: 'block',
                                  lineHeight: 1.4,
                                  color: '#6b7280',
                                  fontSize: '0.85rem',
                                  wordBreak: 'break-word',
                                }}
                              >
                                {link.user_name}
                              </div>
                            )}
                          </div>
                        </td>
                        <td className="super-admin-td-plan">{link.plan_type}</td>
                        <td>{formatLinkDate(link.created_at)}</td>
                        <td>{formatLinkDate(link.expires_at)}</td>
                        <td><span className={`super-admin-status-badge ${status.className}`}>{status.label}</span></td>
                        <td className="super-admin-actions-cell">
                          {link.url && (
                            <button
                              type="button"
                              className="super-admin-copy-row-btn"
                              id={`copy-link-${link.id}`}
                              onClick={() => copyLinkUrl(link.url, `copy-link-${link.id}`)}
                            >
                              Copy link
                            </button>
                          )}
                          <button
                            type="button"
                            className="super-admin-delete-row-btn"
                            onClick={() => handleDeleteLink(link.id)}
                            disabled={isDeleting}
                            title="Delete this link"
                          >
                            {isDeleting ? 'Deleting...' : 'Delete'}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {activeTab === TAB_PLANS && (
        <div className="super-admin-plans-panel">
          <div className="super-admin-create-plan-form">
            <h4 className="super-admin-create-plan-title">Create custom plan (enterprise)</h4>
            <p className="super-admin-create-plan-hint">Add a new plan to plan_limits so you can use it when generating payment links.</p>
            <div className="super-admin-field">
              <label>Plan type (e.g. Enterprise - Acme)</label>
              <input
                type="text"
                placeholder="e.g. enterprise-acme or Enterprise - Acme"
                value={newPlanType}
                onChange={(e) => setNewPlanType(e.target.value)}
                className="super-admin-input"
              />
            </div>
            <div className="super-admin-field">
              <label>Credits per month (-1 = Custom/Unlimited)</label>
              <input
                type="number"
                min={-1}
                value={newPlanCredits === -1 ? '' : newPlanCredits}
                onChange={(e) => setNewPlanCredits(e.target.value === '' ? -1 : Number(e.target.value))}
                placeholder="-1 for custom/unlimited"
                className="super-admin-input"
              />
            </div>
            <div className="super-admin-field">
              <label>Price monthly ($)</label>
              <input
                type="number"
                min={0}
                step={0.01}
                placeholder="Contact for pricing = leave empty"
                value={newPlanPriceMonthly}
                onChange={(e) => setNewPlanPriceMonthly(e.target.value)}
                className="super-admin-input"
              />
            </div>
            <div className="super-admin-field">
              <label>Price yearly ($)</label>
              <input
                type="number"
                min={0}
                step={0.01}
                placeholder="Optional"
                value={newPlanPriceYearly}
                onChange={(e) => setNewPlanPriceYearly(e.target.value)}
                className="super-admin-input"
              />
            </div>
            <div className="super-admin-field">
              <label>Max file size (MB)</label>
              <input
                type="number"
                min={1}
                placeholder="e.g. 10"
                value={newPlanFileSizeMb}
                onChange={(e) => setNewPlanFileSizeMb(e.target.value)}
                className="super-admin-input"
              />
            </div>
            <div className="super-admin-field">
              <label>Features (one per line)</label>
              <textarea
                placeholder="Chat assistant&#10;Team&#10;Custom Integration&#10;Export Results"
                value={newPlanFeatures}
                onChange={(e) => setNewPlanFeatures(e.target.value)}
                rows={4}
                className="super-admin-input super-admin-textarea"
              />
            </div>
            {createPlanError && <div className="super-admin-error">{createPlanError}</div>}
            {createPlanSuccess && <div className="super-admin-success">{createPlanSuccess}</div>}
            <button
              type="button"
              className="super-admin-generate-btn"
              onClick={handleCreateCustomPlan}
              disabled={createPlanLoading}
            >
              {createPlanLoading ? 'Creating...' : 'Create custom plan'}
            </button>
          </div>

          <div className="super-admin-links-toolbar">
            <span className="super-admin-links-count">{customPlans.length} custom plan{customPlans.length !== 1 ? 's' : ''}</span>
          </div>
          {customPlans.length === 0 ? (
            <p className="super-admin-empty">No custom plans defined. Create one above, or add plans in plan_limits with is_standard_plan = false.</p>
          ) : (
            <div className="super-admin-links-table-wrap">
              <table className="super-admin-links-table">
                <thead>
                  <tr>
                    <th>Plan type</th>
                    <th>Credits/month</th>
                    <th>File size</th>
                    <th>Price (monthly)</th>
                    <th>Price (yearly)</th>
                    <th>Features</th>
                    <th className="super-admin-actions-col">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {customPlans.map((p) => (
                    <tr key={p.plan_type}>
                      <td className="super-admin-td-plan">{p.plan_type}</td>
                      <td>{p.credits_per_month == null ? '—' : p.credits_per_month === -1 ? (p.plan_type?.toLowerCase() === 'enterprise' ? 'Custom' : 'Unlimited') : Number(p.credits_per_month).toLocaleString()}</td>
                      <td>{p.max_file_size_mb != null ? `${p.max_file_size_mb}MB` : '—'}</td>
                      <td>{p.price_monthly != null ? `$${p.price_monthly}` : '—'}</td>
                      <td>{p.price_yearly != null ? `$${p.price_yearly}` : '—'}</td>
                      <td>
                        {Array.isArray(p.features) && p.features.length > 0
                          ? p.features.slice(0, 3).map((f, i) => String(f).replace(/records\/run/g, 'records/month').replace(/per run/g, 'per month')).join(', ') + (p.features.length > 3 ? '…' : '')
                          : '—'}
                      </td>
                      <td className="super-admin-actions-cell">
                        <button
                          type="button"
                          className="super-admin-edit-row-btn"
                          onClick={() => openEditPlan(p)}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          className="super-admin-delete-row-btn"
                          onClick={() => handleDeletePlan(p.plan_type)}
                          disabled={deletingPlanType === p.plan_type}
                          title="Delete this custom plan"
                        >
                          {deletingPlanType === p.plan_type ? 'Deleting...' : 'Delete'}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {editingPlan && (
            <div className="super-admin-edit-modal-overlay" onClick={closeEditPlan}>
              <div className="super-admin-edit-modal" onClick={(e) => e.stopPropagation()}>
                <h4 className="super-admin-create-plan-title">Edit custom plan</h4>
                <p className="super-admin-create-plan-hint">Plan type: <strong>{editingPlan.plan_type}</strong> (cannot be changed)</p>
                <div className="super-admin-field">
                  <label>Credits per month (-1 = Custom/Unlimited)</label>
                  <input
                    type="number"
                    min={-1}
                    value={editPlanCredits === -1 ? '' : editPlanCredits}
                    onChange={(e) => setEditPlanCredits(e.target.value === '' ? -1 : Number(e.target.value))}
                    placeholder="-1 for custom/unlimited"
                    className="super-admin-input"
                  />
                </div>
                <div className="super-admin-field">
                  <label>Price monthly ($)</label>
                  <input
                    type="number"
                    min={0}
                    step={0.01}
                    value={editPlanPriceMonthly}
                    onChange={(e) => setEditPlanPriceMonthly(e.target.value)}
                    className="super-admin-input"
                  />
                </div>
                <div className="super-admin-field">
                  <label>Price yearly ($)</label>
                  <input
                    type="number"
                    min={0}
                    step={0.01}
                    value={editPlanPriceYearly}
                    onChange={(e) => setEditPlanPriceYearly(e.target.value)}
                    className="super-admin-input"
                  />
                </div>
                <div className="super-admin-field">
                  <label>Max file size (MB)</label>
                  <input
                    type="number"
                    min={1}
                    value={editPlanFileSizeMb}
                    onChange={(e) => setEditPlanFileSizeMb(e.target.value)}
                    className="super-admin-input"
                  />
                </div>
                <div className="super-admin-field">
                  <label>Features (one per line)</label>
                  <textarea
                    value={editPlanFeatures}
                    onChange={(e) => setEditPlanFeatures(e.target.value)}
                    rows={4}
                    className="super-admin-input super-admin-textarea"
                  />
                </div>
                {editPlanError && <div className="super-admin-error">{editPlanError}</div>}
                <div className="super-admin-edit-modal-actions">
                  <button type="button" className="super-admin-cancel-btn" onClick={closeEditPlan}>Cancel</button>
                  <button type="button" className="super-admin-generate-btn" onClick={handleSaveEditPlan} disabled={editPlanSaving}>
                    {editPlanSaving ? 'Saving...' : 'Save changes'}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {activeTab === TAB_RETENTION && (
        <div className="super-admin-retention-panel">
          <div className="super-admin-retention-warning">
            <strong>Destructive operation.</strong> This runs the same job as the scheduled retention task: it deletes
            old row-level extraction and migration records, clears eligible detail, and may remove uploaded files from
            storage according to each user’s retention window.
          </div>
          <p className="super-admin-retention-hint">
            Search and select a user below to run retention for a single account, or use &quot;Run for all users&quot;
            without selecting anyone.
          </p>
          <div className="super-admin-field">
            <label htmlFor="super-admin-retention-user-search">User (optional — for single-account run)</label>
            <div className="super-admin-user-search" ref={retentionDropdownRef}>
              <input
                id="super-admin-retention-user-search"
                type="text"
                autoComplete="off"
                placeholder="Search by email or name (min 2 chars)"
                value={
                  retentionSelectedUser
                    ? `${retentionSelectedUser.email}${
                        retentionSelectedUser.first_name || retentionSelectedUser.last_name
                          ? ` (${[retentionSelectedUser.first_name, retentionSelectedUser.last_name]
                              .filter(Boolean)
                              .join(' ')})`
                          : ''
                      }`
                    : retentionUserSearch
                }
                onChange={(e) => {
                  setRetentionSelectedUser(null);
                  setRetentionUserSearch(e.target.value);
                }}
                onFocus={() => retentionUsers.length > 0 && setRetentionShowUserDropdown(true)}
              />
              {retentionSelectedUser && (
                <button
                  type="button"
                  className="super-admin-clear-user"
                  onClick={() => {
                    setRetentionSelectedUser(null);
                    setRetentionUserSearch('');
                    setRetentionUsers([]);
                  }}
                >
                  Clear
                </button>
              )}
              {retentionUsersLoading && <span className="super-admin-search-loading">Searching...</span>}
              {retentionShowUserDropdown && retentionUsers.length > 0 && (
                <ul className="super-admin-user-dropdown">
                  {retentionUsers.map((u) => (
                    <li
                      key={u.id}
                      onClick={() => {
                        setRetentionSelectedUser(u);
                        setRetentionUserSearch('');
                        setRetentionUsers([]);
                        setRetentionShowUserDropdown(false);
                      }}
                    >
                      {u.email}{' '}
                      {[u.first_name, u.last_name].filter(Boolean).join(' ')
                        ? `(${[u.first_name, u.last_name].filter(Boolean).join(' ')})`
                        : ''}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            {retentionSearchError && <div className="super-admin-error">{retentionSearchError}</div>}
          </div>
          <div className="super-admin-retention-actions">
            <button
              type="button"
              className="super-admin-retention-btn super-admin-retention-btn--user"
              onClick={() => runDataRetention(false)}
              disabled={retentionLoading || !retentionSelectedUser?.id}
              title={!retentionSelectedUser?.id ? 'Search and select a user above' : undefined}
            >
              {retentionLoading ? 'Running…' : 'Run for selected user only'}
            </button>
            <button
              type="button"
              className="super-admin-retention-btn super-admin-retention-btn--all"
              onClick={() => runDataRetention(true)}
              disabled={retentionLoading}
            >
              {retentionLoading ? 'Running…' : 'Run for all users'}
            </button>
          </div>
          {retentionSelectedUser?.id && (
            <p className="super-admin-retention-selected">
              Selected user: <code>{retentionSelectedUser.email}</code> ({retentionSelectedUser.id})
            </p>
          )}
          {retentionError && <div className="super-admin-error">{retentionError}</div>}
          {retentionResult && (
            <div className="super-admin-retention-result">
              <label>Response</label>
              <pre className="super-admin-retention-json">{JSON.stringify(retentionResult, null, 2)}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default SuperAdminPage;
