import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import api from './api';
import { formatHumanReadableDate } from './utils/dateUtils';
import { FiSettings, FiSave, FiArrowLeft, FiMoreHorizontal, FiDatabase, FiTrash2, FiLogIn, FiGitBranch, FiRefreshCw } from 'react-icons/fi';
import SQLConnectorForm from './components/SQLConnectorForm';
import SalesforceOrgConnectorTabs from './components/SalesforceOrgConnectorTabs';
import ConnectorMetadataSyncInline from './components/ConnectorMetadataSyncInline';
import GoogleSheetsConnectorTabs from './components/GoogleSheetsConnectorTabs';
import GoogleSheetsSpreadsheetsList from './components/GoogleSheetsSpreadsheetsList';
import SqlConnectorTabs from './components/SqlConnectorTabs';
import SqlConnectorTablesList from './components/SqlConnectorTablesList';
import SuccessModal from './components/SuccessModal';
import WarningModal from './components/WarningModal';
import './ConnectionsPage.css';
import { SUPPORT_EMAIL } from './config/site';

const ConnectionsPage = () => {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [environment, setEnvironment] = useState('production');
  const [connectionName, setConnectionName] = useState('');
  const [orgDescription, setOrgDescription] = useState('');
  const [needsOrgSetup, setNeedsOrgSetup] = useState({ name: false, description: false });
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [showSuccessModal, setShowSuccessModal] = useState(false);
  const [modalMessage, setModalMessage] = useState('');
  const [modalType, setModalType] = useState('success');
  const [modalContext, setModalContext] = useState('create');
  const [showDeleteWarning, setShowDeleteWarning] = useState(false);
  const [connectorToDelete, setConnectorToDelete] = useState(null);
  const [salesforceApps, setSalesforceApps] = useState([]);
  const [connectedApp, setConnectedApp] = useState(null);
  const [salesforceConnectionsByApp, setSalesforceConnectionsByApp] = useState({});
  const [googleSheetsConnections, setGoogleSheetsConnections] = useState([]);
  const [selectedGoogleSheetsConnection, setSelectedGoogleSheetsConnection] = useState(null);
  const [showConfiguration, setShowConfiguration] = useState(false);
  const [sqlConnectors, setSqlConnectors] = useState([]);
  const [showSQLConnectorForm, setShowSQLConnectorForm] = useState(false);
  const [selectedConnectorType, setSelectedConnectorType] = useState(null);
  const [editingConnector, setEditingConnector] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [showGoogleSheetsConfig, setShowGoogleSheetsConfig] = useState(false);
  const [googleSheetsSpreadsheets, setGoogleSheetsSpreadsheets] = useState([]);
  const [showGoogleSheetsDeleteWarning, setShowGoogleSheetsDeleteWarning] = useState(false);
  const [showSpreadsheetDeleteWarning, setShowSpreadsheetDeleteWarning] = useState(false);
  const [spreadsheetToDelete, setSpreadsheetToDelete] = useState(null);
  const [syncingSpreadsheetId, setSyncingSpreadsheetId] = useState(null);
  const hasInitialized = useRef(false); // Use ref to prevent duplicate calls
  const [editingSalesforceApp, setEditingSalesforceApp] = useState(null);
  const [isAddingSalesforceApp, setIsAddingSalesforceApp] = useState(false);
  const [showSalesforceDeleteWarning, setShowSalesforceDeleteWarning] = useState(false);
  const [salesforceAppToDelete, setSalesforceAppToDelete] = useState(null);
  const [editingSalesforceConnection, setEditingSalesforceConnection] = useState(null);
  const [isDefault, setIsDefault] = useState(false);
  const [ssoAvailable, setSsoAvailable] = useState(false);
  const [ssoConnecting, setSsoConnecting] = useState(false);
  const sqlConnectionCardRef = useRef(null);
  const [sqlTablesPanelHeight, setSqlTablesPanelHeight] = useState(null);
  const gsAccountCardRef = useRef(null);
  const [gsWorkbookPanelHeight, setGsWorkbookPanelHeight] = useState(null);
  const [gsConnectionDescription, setGsConnectionDescription] = useState('');
  const [gsConnectionName, setGsConnectionName] = useState('');
  const [gsSaving, setGsSaving] = useState(false);
  const [gsReconnecting, setGsReconnecting] = useState(false);
  const [gsMessage, setGsMessage] = useState('');
  const [sqlConnectionDescription, setSqlConnectionDescription] = useState('');
  const [setupHighlightConnectorId, setSetupHighlightConnectorId] = useState(null);
  const handledSetupDeepLink = useRef(false);
  const handledGsDeepLink = useRef(false);

  const formatConnectionDate = formatHumanReadableDate;

  // Match SQL tables panel height to the connection form card (scroll list inside).
  useEffect(() => {
    if (!showSQLConnectorForm || !editingConnector?.id) {
      setSqlTablesPanelHeight(null);
      return undefined;
    }
    const el = sqlConnectionCardRef.current;
    if (!el) return undefined;

    const measure = () => {
      setSqlTablesPanelHeight(el.getBoundingClientRect().height);
    };

    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    window.addEventListener('resize', measure);
    return () => {
      ro.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [showSQLConnectorForm, editingConnector?.id, isLoading, selectedConnectorType]);

  // Match Connected workbook card height to Connected Google Account card.
  useEffect(() => {
    if (!showGoogleSheetsConfig) {
      setGsWorkbookPanelHeight(null);
      return undefined;
    }
    const el = gsAccountCardRef.current;
    if (!el) return undefined;

    const measure = () => {
      setGsWorkbookPanelHeight(el.getBoundingClientRect().height);
    };

    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    window.addEventListener('resize', measure);
    return () => {
      ro.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [
    showGoogleSheetsConfig,
    googleSheetsConnections,
    selectedGoogleSheetsConnection,
    googleSheetsSpreadsheets,
    isLoading,
  ]);

  const resetSalesforceForm = useCallback(() => {
    setClientId('');
    setClientSecret('');
    setEnvironment('production');
    setConnectionName('');
    setOrgDescription('');
    setNeedsOrgSetup({ name: false, description: false });
    setMessage('');
    setEditingSalesforceApp(null);
    setConnectedApp(null);
    setIsDefault(false);
  }, []);

  const fetchConnectedApp = useCallback(async (selectedId = null) => {
    try {
      const response = await api.get('/api/connected-app');
      const apps = response.data?.connected_apps || [];
      setSalesforceApps(apps);
      if (apps.length > 0) {
        const primaryApp = selectedId
          ? apps.find((app) => app.id === selectedId) || apps[0]
          : apps[0];
        setConnectedApp(primaryApp);
        setEditingSalesforceApp(primaryApp);
        setClientId(primaryApp.client_id || '');
        setClientSecret(primaryApp.client_secret || '');
        setEnvironment(primaryApp.environment_type || 'production');
        setConnectionName(primaryApp.connection_name || '');
        setIsDefault(primaryApp.is_default ?? false);
        setIsAddingSalesforceApp(false);
      } else {
        setConnectedApp(null);
        resetSalesforceForm();
        setIsDefault(true);
        setIsAddingSalesforceApp(true);
      }
      return {
        apps,
        selectedApp:
          apps.length > 0
            ? (selectedId ? apps.find((app) => app.id === selectedId) || apps[0] : apps[0])
            : null,
      };
    } catch (err) {
      return { apps: [], selectedApp: null };
    }
  }, [resetSalesforceForm]);

  const fetchSalesforceConnection = useCallback(async () => {
    try {
      const response = await api.get('/api/salesforce-connection');
      const connectionMap = {};
      const connections = response.data?.connections || [];
      connections.forEach((connection) => {
        const appId = connection.connected_app_id || connection.salesforce_connected_app_id;
        const tokenId = connection.token_id;
        const conn = { ...connection, connected: true };
        if (appId) {
          if (!connectionMap[appId]) connectionMap[appId] = [];
          connectionMap[appId].push(conn);
        }
        // Token-centric: also key by token_id so lookups work when activeApp.id is token_id (SSO)
        if (tokenId) {
          connectionMap[tokenId] = [conn];
        }
      });
      setSalesforceConnectionsByApp(connectionMap);
    } catch (err) {
      setSalesforceConnectionsByApp({});
    }
  }, []);

  const fetchSQLConnectors = useCallback(async () => {
    try {
      const response = await api.get('/api/sql-connectors/');
      if (response.data.status === 'success') {
        setSqlConnectors(response.data.connectors);
      }
    } catch (err) {
      console.error('Error fetching SQL connectors:', err);
    }
  }, []);

  const gsConnectionLabel = useCallback((conn) => {
    if (!conn) return 'Google Sheets';
    const email = (conn.google_account_email || '').trim();
    const custom = (conn.connection_name || '').trim();
    if (
      custom &&
      custom.toLowerCase() !== 'google sheets' &&
      custom !== email
    ) {
      return custom;
    }
    return email || 'Google Sheets';
  }, []);

  const gsHasCustomConnectionName = useCallback(
    (conn) => {
      if (!conn) return false;
      const email = (conn.google_account_email || '').trim();
      const custom = (conn.connection_name || '').trim();
      return Boolean(
        custom &&
          custom.toLowerCase() !== 'google sheets' &&
          custom !== email
      );
    },
    []
  );

  const fetchGoogleSheetsConnections = useCallback(async () => {
    try {
      const response = await api.get('/api/google-sheets-connections');
      const list = response.data?.connections || [];
      setGoogleSheetsConnections(list);
    } catch (err) {
      console.error('Error fetching Google Sheets connections:', err);
      setGoogleSheetsConnections([]);
    }
  }, []);

  const fetchSsoAvailability = useCallback(async () => {
    try {
      const response = await api.get('/api/salesforce-sso-available');
      setSsoAvailable(response.data?.available ?? false);
    } catch (err) {
      setSsoAvailable(false);
    }
  }, []);

  useEffect(() => {
    // Prevent multiple executions using ref
    if (hasInitialized.current) {
      return;
    }
    hasInitialized.current = true;

    fetchConnectedApp();
    fetchSalesforceConnection();
    fetchSQLConnectors();
    fetchGoogleSheetsConnections();
    fetchSsoAvailability();
  }, [
    fetchConnectedApp,
    fetchSalesforceConnection,
    fetchSQLConnectors,
    fetchGoogleSheetsConnections,
    fetchSsoAvailability,
  ]);

  const findSalesforceConnectionByRef = useCallback((ref) => {
    const target = String(ref || '');
    if (!target) return null;

    const direct = salesforceConnectionsByApp[target];
    if (Array.isArray(direct) && direct[0]) {
      const conn = direct[0];
      const appId = conn.connected_app_id || conn.salesforce_connected_app_id;
      const app =
        salesforceApps.find((a) => a.id === appId) ||
        { id: appId || target, is_sso_connection: true };
      return { app, connection: conn };
    }

    for (const app of salesforceApps) {
      const connections = salesforceConnectionsByApp[app.id];
      const list = Array.isArray(connections)
        ? connections
        : connections
          ? [connections]
          : [];
      const match = list.find(
        (c) =>
          String(c.token_id) === target || String(c.org_id) === target
      );
      if (match) {
        return { app, connection: match };
      }
    }
    return null;
  }, [salesforceApps, salesforceConnectionsByApp]);

  const fetchGoogleSheetsSpreadsheets = async (connectionId) => {
    if (!connectionId) {
      setGoogleSheetsSpreadsheets([]);
      return;
    }
    try {
      const response = await api.get('/google-sheets/categorized-spreadsheets', {
        params: { connection_id: connectionId },
      });
      if (response.data.status === 'success') {
        setGoogleSheetsSpreadsheets(response.data.spreadsheets);
      }
    } catch (err) {
      console.error('Error fetching Google Sheets spreadsheets:', err);
    }
  };

  const handleSave = async () => {
    const isSSO = editingSalesforceApp?.is_sso_connection;
    if (!isSSO && (!clientId || !clientSecret)) {
      setMessage("❌ Please fill in Consumer Key and Consumer Secret.");
      return;
    }

    setSaving(true);
    setMessage('');

    try {
      const payload = {
        client_id: isSSO ? undefined : clientId,
        client_secret: isSSO ? undefined : clientSecret,
        environment_type: environment,
        connection_name: connectionName || (environment === 'production' ? 'Production Org' : 'Sandbox Org'),
        org_display_name: connectionName?.trim() || null,
        org_id: editingSalesforceConnection?.org_id || editingSalesforceApp?.org_id || null,
        is_default: isDefault,
      };
      if (payload.client_id === undefined) delete payload.client_id;
      if (payload.client_secret === undefined) delete payload.client_secret;

      if (editingSalesforceApp?.id && typeof editingSalesforceApp.id === 'number') {
        payload.id = editingSalesforceApp.id;
      } else if (editingSalesforceApp?.org_id) {
        payload.org_id = editingSalesforceApp.org_id;
      }

      const response = await api.post('/api/salesforce-connected-app', payload);

      const savedId = response.data?.id ?? editingSalesforceApp?.id ?? null;

      const connectorRef =
        editingSalesforceConnection?.token_id || editingSalesforceConnection?.org_id;
      if (connectorRef) {
        await api.patch(`/api/labeling/connectors/salesforce/${connectorRef}/setup`, {
          user_description: orgDescription,
        });
        try {
          const { data: profileData } = await api.get(
            `/api/labeling/connectors/salesforce/${connectorRef}/profile`
          );
          setNeedsOrgSetup({
            name: Boolean(profileData.needs_org_name),
            description: Boolean(profileData.needs_org_description),
          });
          if (!profileData.needs_org_name && !profileData.needs_org_description) {
            setSetupHighlightConnectorId(null);
          }
        } catch {
          setNeedsOrgSetup({ name: false, description: !orgDescription.trim() });
        }
      }

      setMessage('✅ Org settings saved successfully.');
      const { selectedApp } = await fetchConnectedApp(savedId);
      if (selectedApp) {
        setEditingSalesforceApp(selectedApp);
        setIsAddingSalesforceApp(false);
      setIsDefault(selectedApp.is_default ?? false);
      }
    } catch (err) {
      console.error(err);
      setMessage('❌ Failed to save app credentials.');
    } finally {
      setSaving(false);
    }
  };

  const handleConnectViaSSO = async (environment = 'production', orgId = null) => {
    if (!ssoAvailable) return;
    setSsoConnecting(true);
    setMessage('');
    try {
      const params = { environment };
      if (orgId) {
        params.org_id = orgId;
      }
      const response = await api.get('/oauth/start-sso', { params });
      const { authorization_url } = response.data;
      window.location.href = authorization_url;
    } catch (error) {
      console.error('Connect via SSO failed', error);
      setMessage(error.response?.data?.detail || '❌ Failed to start Salesforce SSO. Please try again.');
      setSsoConnecting(false);
    }
  };

  const handleReconnectSalesforceSSO = async () => {
    const conn = editingSalesforceConnection;
    if (!conn?.org_id) return;
    const env = conn.environment_type || environment || 'production';
    await handleConnectViaSSO(env, conn.org_id);
  };

  const handleConnectGoogleSheets = async () => {
    try {
      const response = await api.get('/oauth/google-sheets/start');
      const { authorization_url } = response.data;
      window.location.href = authorization_url;
    } catch (error) {
      console.error('Google Sheets OAuth initiation failed', error);
      setMessage('❌ Failed to start Google Sheets connection. Please try again.');
    }
  };

  const handleReconnectGoogleSheets = async () => {
    const connectionId = selectedGoogleSheetsConnection?.id;
    if (!connectionId) return;

    setGsReconnecting(true);
    setGsMessage('');
    try {
      const response = await api.get('/oauth/google-sheets/start', {
        params: { connection_id: connectionId },
      });
      const { authorization_url } = response.data;
      window.location.href = authorization_url;
    } catch (error) {
      console.error('Google Sheets reconnect failed', error);
      setGsMessage(
        error.response?.data?.detail
          ? `❌ ${error.response.data.detail}`
          : '❌ Failed to start Google reconnect. Please try again.',
      );
      setGsReconnecting(false);
    }
  };

  const openGoogleSheetsConfiguration = (connection) => {
    if (!connection?.id) return;
    setSelectedGoogleSheetsConnection(connection);
    setShowGoogleSheetsConfig(true);
    setGsMessage('');
    fetchGoogleSheetsSpreadsheets(connection.id);
  };

  const handleGoogleSheetsCardClick = (connection) => {
    openGoogleSheetsConfiguration(connection);
  };

  const handleBackFromGoogleSheets = () => {
    setShowGoogleSheetsConfig(false);
    setSelectedGoogleSheetsConnection(null);
    setGsConnectionDescription('');
    setGsConnectionName('');
    setGsMessage('');
    setGoogleSheetsSpreadsheets([]);
  };

  const handleGoogleSheetsSettingsSave = async () => {
    const connectionId = selectedGoogleSheetsConnection?.id;
    if (!connectionId) return;

    setGsSaving(true);
    setGsMessage('');
    try {
      const patchRes = await api.patch(
        `/api/google-sheets-connections/${connectionId}`,
        {
          connection_name:
            gsConnectionName.trim() ||
            selectedGoogleSheetsConnection.google_account_email,
        }
      );
      if (patchRes.data?.connection) {
        setSelectedGoogleSheetsConnection(patchRes.data.connection);
      }
      await api.patch(
        `/api/labeling/connectors/google_sheets/${connectionId}/description`,
        { user_description: gsConnectionDescription }
      );
      await fetchGoogleSheetsConnections();
      setGsMessage('✅ Connection settings saved successfully.');
    } catch (err) {
      console.error(err);
      setGsMessage('❌ Failed to save connection settings.');
    } finally {
      setGsSaving(false);
    }
  };

  const handleDeleteGoogleSheetsConnection = () => {
    setShowGoogleSheetsDeleteWarning(true);
  };

  const handleConfirmGoogleSheetsDelete = async () => {
    const connectionId = selectedGoogleSheetsConnection?.id;
    setShowGoogleSheetsDeleteWarning(false);
    if (!connectionId) return;

    setIsLoading(true);

    try {
      const response = await api.delete(`/api/google-sheets-connections/${connectionId}`);
      if (response.data.success) {
        await fetchGoogleSheetsConnections();
        setSelectedGoogleSheetsConnection(null);
        setGoogleSheetsSpreadsheets([]);
        setShowGoogleSheetsConfig(false);
        setModalMessage('✅ Google Sheets connection deleted successfully!');
        setModalType('success');
        setModalContext('delete');
        setShowSuccessModal(true);
      } else {
        setModalMessage(`❌ ${response.data.message}`);
        setModalType('error');
        setShowSuccessModal(true);
      }
    } catch (error) {
      console.error('Error deleting Google Sheets connection:', error);
      setModalMessage('❌ Failed to delete Google Sheets connection. Please try again.');
      setModalType('error');
      setShowSuccessModal(true);
    } finally {
      setIsLoading(false);
    }
  };

  const handleCancelGoogleSheetsDelete = () => {
    setShowGoogleSheetsDeleteWarning(false);
  };

  const handleDeleteSpreadsheet = (spreadsheetId, spreadsheetName) => {
    setSpreadsheetToDelete({ id: spreadsheetId, name: spreadsheetName });
    setShowSpreadsheetDeleteWarning(true);
  };

  const handleConfirmSpreadsheetDelete = async () => {
    if (!spreadsheetToDelete) return;
    
    setShowSpreadsheetDeleteWarning(false);
    setIsLoading(true);
    
    try {
      const response = await api.delete(`/google-sheets/spreadsheets/${spreadsheetToDelete.id}`);
      if (response.data.status === 'success') {
        // Refresh the spreadsheets list
        await fetchGoogleSheetsSpreadsheets();
        setModalMessage(`✅ ${response.data.message}`);
        setModalType('success');
        setModalContext('delete');
        setShowSuccessModal(true);
      } else {
        setModalMessage(`❌ ${response.data.message}`);
        setModalType('error');
        setShowSuccessModal(true);
      }
    } catch (error) {
      console.error('Error deleting spreadsheet:', error);
      setModalMessage('❌ Failed to delete spreadsheet. Please try again.');
      setModalType('error');
      setShowSuccessModal(true);
    } finally {
      setIsLoading(false);
      setSpreadsheetToDelete(null);
    }
  };

  const handleCancelSpreadsheetDelete = () => {
    setShowSpreadsheetDeleteWarning(false);
    setSpreadsheetToDelete(null);
  };

  const handleResyncSpreadsheet = async (spreadsheetId, spreadsheetName) => {
    setSyncingSpreadsheetId(spreadsheetId);
    try {
      const response = await api.post(`/google-sheets/spreadsheets/${spreadsheetId}/sync`);
      if (response.data.status === 'success') {
        setModalMessage(response.data.message || `Re-sync started for "${spreadsheetName}". You will receive an email when complete.`);
        setModalType('success');
        setModalContext('create');
        setShowSuccessModal(true);
      } else {
        setModalMessage(response.data.message || 'Failed to start re-sync.');
        setModalType('error');
        setShowSuccessModal(true);
      }
    } catch (error) {
      console.error('Error starting spreadsheet re-sync:', error);
      const detail = error.response?.data?.detail;
      let msg = 'Failed to start re-sync. Please try again.';
      if (typeof detail === 'string') {
        msg = detail;
      } else if (detail && typeof detail === 'object' && detail.message) {
        msg = detail.message;
      }
      setModalMessage(msg);
      setModalType('error');
      setShowSuccessModal(true);
    } finally {
      setSyncingSpreadsheetId(null);
    }
  };


  const openSalesforceConfiguration = (app = null, connection = null) => {
    if (app) {
      setConnectedApp(app);
      setEditingSalesforceApp(app);
      setEditingSalesforceConnection(connection);
      setClientId(app.client_id || '');
      setClientSecret(app.client_secret || '');
      setEnvironment(connection?.environment_type ?? app.environment_type ?? 'production');
      const orgName = connection?.org_display_name ?? app.connection_name ?? connection?.salesforce_username ?? '';
      setConnectionName(orgName);
      setIsDefault(connection?.is_default ?? app.is_default ?? false);
      setIsAddingSalesforceApp(false);
    } else {
      resetSalesforceForm();
      setEditingSalesforceConnection(null);
      setIsDefault(salesforceApps.length === 0);
      setIsAddingSalesforceApp(true);
    }
    setMessage('');
    setShowConfiguration(true);
  };

  useEffect(() => {
    const sfConnector = searchParams.get('sfConnector');
    const setup = searchParams.get('setup');
    if (!sfConnector || handledSetupDeepLink.current) return;
    if (salesforceApps.length === 0 && !salesforceConnectionsByApp[sfConnector]) return;

    const match = findSalesforceConnectionByRef(sfConnector);
    if (!match) return;

    handledSetupDeepLink.current = true;
    openSalesforceConfiguration(match.app, match.connection);
    if (setup === '1') {
      setSetupHighlightConnectorId(
        match.connection.token_id || match.connection.org_id || sfConnector
      );
    }
    setSearchParams({}, { replace: true });
  }, [
    searchParams,
    salesforceApps,
    salesforceConnectionsByApp,
    findSalesforceConnectionByRef,
    setSearchParams,
  ]);

  useEffect(() => {
    const gsConnection = searchParams.get('gsConnection');
    const reconnected = searchParams.get('reconnected');
    if (!gsConnection || handledGsDeepLink.current) return;
    if (googleSheetsConnections.length === 0) return;

    const conn = googleSheetsConnections.find(
      (c) => String(c.id) === String(gsConnection),
    );
    if (!conn) return;

    handledGsDeepLink.current = true;
    openGoogleSheetsConfiguration(conn);
    if (reconnected === '1') {
      setGsMessage('✅ Google account reconnected successfully.');
    }
    setSearchParams({}, { replace: true });
  }, [searchParams, googleSheetsConnections, setSearchParams]);

  useEffect(() => {
    let cancelled = false;

    const loadOrgProfile = async () => {
      const conn = editingSalesforceConnection;
      if (!showConfiguration || !conn) {
        setOrgDescription('');
        setNeedsOrgSetup({ name: false, description: false });
        return;
      }

      const connectorRef = conn.token_id || conn.org_id;
      if (!connectorRef) return;

      try {
        const { data } = await api.get(
          `/api/labeling/connectors/salesforce/${connectorRef}/profile`
        );
        if (cancelled) return;
        setOrgDescription(data.user_description || '');
        setNeedsOrgSetup({
          name: Boolean(data.needs_org_name),
          description: Boolean(data.needs_org_description),
        });
      } catch {
        if (!cancelled) {
          setOrgDescription('');
          setNeedsOrgSetup({ name: false, description: false });
        }
      }
    };

    loadOrgProfile();
    return () => {
      cancelled = true;
    };
  }, [showConfiguration, editingSalesforceConnection]);

  useEffect(() => {
    let cancelled = false;

    const loadGsProfile = async () => {
      const connectionId = selectedGoogleSheetsConnection?.id;
      if (!showGoogleSheetsConfig || !connectionId) {
        setGsConnectionDescription('');
        setGsConnectionName('');
        return;
      }

      try {
        const { data } = await api.get(
          `/api/labeling/connectors/google_sheets/${connectionId}/profile`
        );
        if (!cancelled) {
          setGsConnectionDescription(data.user_description || '');
          const conn = googleSheetsConnections.find((c) => c.id === connectionId);
          setGsConnectionName(
            gsHasCustomConnectionName(conn) ? (conn?.connection_name || '').trim() : ''
          );
        }
      } catch {
        if (!cancelled) {
          setGsConnectionDescription('');
          setGsConnectionName('');
        }
      }
    };

    loadGsProfile();
    return () => {
      cancelled = true;
    };
  }, [showGoogleSheetsConfig, selectedGoogleSheetsConnection?.id, googleSheetsConnections, gsHasCustomConnectionName]);

  useEffect(() => {
    let cancelled = false;

    const loadSqlProfile = async () => {
      const connectorId = editingConnector?.id;
      if (!showSQLConnectorForm || !connectorId) {
        setSqlConnectionDescription('');
        return;
      }

      try {
        const { data } = await api.get(
          `/api/labeling/connectors/sql/${connectorId}/profile`
        );
        if (!cancelled) {
          setSqlConnectionDescription(data.user_description || '');
        }
      } catch {
        if (!cancelled) setSqlConnectionDescription('');
      }
    };

    loadSqlProfile();
    return () => {
      cancelled = true;
    };
  }, [showSQLConnectorForm, editingConnector?.id]);

  const handleAddSalesforceConnectionClick = () => {
    openSalesforceConfiguration(null);
  };

  const handleBackToConnections = () => {
    setShowConfiguration(false);
    setSalesforceAppToDelete(null);
    setShowSalesforceDeleteWarning(false);
    setIsAddingSalesforceApp(false);
    setEditingSalesforceConnection(null);
    setSetupHighlightConnectorId(null);
    resetSalesforceForm();
  };

  const handleSQLConnectorClick = (connectorType) => {
    setSelectedConnectorType(connectorType);
    setEditingConnector(null);
    setShowSQLConnectorForm(true);
  };

  const handleEditSQLConnector = (connector) => {
    setEditingConnector(connector);
    setSelectedConnectorType(connector.connector_type);
    setShowSQLConnectorForm(true);
  };

  const handleSQLConnectorSave = async (connectorData) => {
    setIsLoading(true);
    try {
      let response;
      if (editingConnector) {
        // Update existing connector
        response = await api.put(`/api/sql-connectors/${editingConnector.id}`, connectorData);
        if (response.data.status === 'success') {
          setModalMessage('✅ SQL connector updated successfully! Archian AI is preparing your agent — you will receive an email when it is ready to work.');
          setModalType('success');
          setShowSuccessModal(true);
          
          // Store pending sync info in sessionStorage for ChatUploader to check
          sessionStorage.setItem('pending_sync', JSON.stringify({
            type: 'sql',
            connectorType: 'sql',
            connectorInstanceId: editingConnector.id,
            connectorId: editingConnector.id,
            name: connectorData.name || editingConnector.name || 'Database',
            displayName: connectorData.name || editingConnector.name || 'Database',
            trigger: 'resync',
          }));
        }
      } else {
        // Create new connector
        response = await api.post('/api/sql-connectors/', connectorData);
        if (response.data.status === 'success') {
          setModalMessage('✅ SQL connector created successfully! Archian AI is preparing your agent — you will receive an email when it is ready to work.');
          setModalType('success');
          setShowSuccessModal(true);
          
          // Store pending sync info in sessionStorage for ChatUploader to check
          if (response.data.connector_id) {
            sessionStorage.setItem('pending_sync', JSON.stringify({
              type: 'sql',
              connectorType: 'sql',
              connectorInstanceId: response.data.connector_id,
              connectorId: response.data.connector_id,
              name: connectorData.name || 'Database',
              displayName: connectorData.name || 'Database',
              trigger: 'create',
            }));
          }
        }
      }
      
      if (response.data.status === 'success') {
        const connectorId = editingConnector?.id || response.data.connector_id;
        if (connectorId) {
          try {
            await api.patch(`/api/labeling/connectors/sql/${connectorId}/description`, {
              user_description: sqlConnectionDescription || '',
            });
          } catch (descErr) {
            console.error('Failed to save SQL connection description:', descErr);
          }
        }

        setShowSQLConnectorForm(false);
        setSelectedConnectorType(null);
        setEditingConnector(null);
        // Refresh connectors list
        const connectorsResponse = await api.get('/api/sql-connectors/');
        if (connectorsResponse.data.status === 'success') {
          setSqlConnectors(connectorsResponse.data.connectors);
        }
      } else {
        setModalMessage(`❌ ${response.data.message}`);
        setModalType('error');
        setShowSuccessModal(true);
      }
    } catch (error) {
      console.error('Error saving SQL connector:', error);
      const detail = error.response?.data?.detail;
      const detailMsg = Array.isArray(detail)
        ? detail.map((d) => d.msg || JSON.stringify(d)).join('; ')
        : typeof detail === 'string'
          ? detail
          : null;
      const apiMessage = error.response?.data?.message || detailMsg;
      setModalMessage(
        apiMessage
          ? `❌ ${apiMessage}`
          : `❌ Failed to ${editingConnector ? 'update' : 'create'} SQL connector. Please try again.`
      );
      setModalType('error');
      setShowSuccessModal(true);
    } finally {
      setIsLoading(false);
    }
  };

  const handleSQLConnectorCancel = () => {
    setShowSQLConnectorForm(false);
    setSelectedConnectorType(null);
    setEditingConnector(null);
    setSqlConnectionDescription('');
  };

  const handleCloseSuccessModal = () => {
    setShowSuccessModal(false);
    setModalMessage('');
    setModalContext('create');
  };

  const handleSQLConnectorDelete = () => {
    if (!editingConnector) return;
    
    // Store the connector to delete and show warning modal
    setConnectorToDelete(editingConnector);
    setShowDeleteWarning(true);
  };

  const handleConfirmDelete = async () => {
    if (!connectorToDelete) return;
    
    setIsLoading(true);
    setShowDeleteWarning(false);
    
    try {
      const response = await api.delete(`/api/sql-connectors/${connectorToDelete.id}`);
      
      if (response.data.status === 'success') {
        setModalMessage(`✅ ${response.data.message}`);
        setModalType('success');
        setModalContext('delete');
        setShowSuccessModal(true);
        
        // Close the form and refresh connectors list
        setShowSQLConnectorForm(false);
        setSelectedConnectorType(null);
        setEditingConnector(null);
        
        // Refresh connectors list
        const connectorsResponse = await api.get('/api/sql-connectors/');
        if (connectorsResponse.data.status === 'success') {
          setSqlConnectors(connectorsResponse.data.connectors);
        }
      } else {
        setModalMessage(`❌ ${response.data.message}`);
        setModalType('error');
        setShowSuccessModal(true);
      }
    } catch (error) {
      console.error('Error deleting SQL connector:', error);
      setModalMessage(`❌ Failed to delete SQL connector. Please try again.`);
      setModalType('error');
      setShowSuccessModal(true);
    } finally {
      setIsLoading(false);
      setConnectorToDelete(null);
    }
  };

  const handleCancelDelete = () => {
    setShowDeleteWarning(false);
    setConnectorToDelete(null);
  };

  // Connection Cards View
  const renderConnectionCards = () => (
    <div className="connections-cards-container">
      <div className="eg-connections-promo">
        <div className="eg-connections-promo-text">
          <FiGitBranch className="eg-connections-promo-icon" aria-hidden />
          <div>
            <strong>Enterprise graphs</strong>
            <p>Combine Salesforce, SQL, and Google Sheets into one data landscape.</p>
          </div>
        </div>
        <button
          type="button"
          className="eg-connections-promo-btn"
          onClick={() => navigate('/connections/enterprise-graphs')}
        >
          View graphs
        </button>
      </div>

      {/* Add Connection - displayed above connection list */}
      <div className="available-connectors-section">
        <div className="add-connection-header">
          <h3 className="section-title">Add Connection</h3>
          <button 
            className="request-connector-btn"
            onClick={() => window.location.href = `mailto:${SUPPORT_EMAIL}?subject=Request New Connector`}
          >
            <span className="plus-icon">+</span>
            Request a Connector
          </button>
        </div>
        <div className="connector-grid">
          <div className="connector-option" onClick={handleAddSalesforceConnectionClick}>
            <img src="/assets/connectors/Salesforce.com_logo.svg (1).png" alt="Salesforce" className="connector-icon" />
            <span>Salesforce</span>
          </div>
          <div className="connector-option" onClick={handleConnectGoogleSheets}>
            <img src="/assets/connectors/google-sheets.png" alt="Google Sheets" className="connector-icon" />
            <span>Google Sheets</span>
          </div>
          <div className="connector-option" onClick={() => handleSQLConnectorClick('mysql')}>
            <img src="/assets/connectors/MySQL-Logo.wine.png" alt="MySQL" className="connector-icon" />
            <span>MySQL</span>
          </div>
          <div className="connector-option" onClick={() => handleSQLConnectorClick('postgresql')}>
            <img src="/assets/connectors/postgresql-logo.png" alt="PostgreSQL" className="connector-icon" />
            <span>PostgreSQL</span>
          </div>
          <div className="connector-option" onClick={() => handleSQLConnectorClick('mssql')}>
            <img src="/assets/connectors/mssql-logo.png" alt="Microsoft SQL Server" className="connector-icon" />
            <span>Microsoft SQL Server</span>
          </div>
          <div className="connector-option" onClick={() => handleSQLConnectorClick('snowflake')}>
            <img src="/assets/connectors/snowflake-logo.png" alt="Snowflake" className="connector-icon" />
            <span>Snowflake</span>
          </div>
        </div>
      </div>

      <div className="salesforce-connections-section">
        <h3 className="section-title">Salesforce Connections</h3>
        <div className="connection-card-grid">
          {(() => {
            const displayItems = [];
            salesforceApps.forEach((app) => {
              const connections = salesforceConnectionsByApp[app.id];
              const connList = Array.isArray(connections) ? connections : connections ? [connections] : [];
              if (connList.length === 0) {
                displayItems.push({ app, connection: null, key: `app-${app.id}` });
              } else {
                connList.forEach((conn, idx) => {
                  displayItems.push({ app, connection: conn, key: `app-${app.id}-org-${conn.org_id || conn.token_id || idx}` });
                });
              }
            });
            return displayItems.length > 0 ? (
              displayItems.map(({ app, connection, key }) => {
                const isConnected = !!connection;
                return (
                  <div
                    key={key}
                    className={`connection-card salesforce-card ${isConnected ? 'connected' : 'disconnected'}`}
                    onClick={() => openSalesforceConfiguration(app, connection)}
                  >
                    <div className="connection-card-content">
                      <div className="connection-logo">
                        <div className="salesforce-logo">
                          <img src="/assets/connectors/Salesforce.com_logo.svg (1).png" alt="Salesforce" className="salesforce-icon" />
                        </div>
                      </div>
                      <div className="connection-info">
                        <div className="connection-name">
                          {isConnected && connection
                            ? (connection.org_display_name || connection.salesforce_username || app.connection_name || 'Salesforce')
                            : (app.connection_name || 'Salesforce Org')}
                          {(connection?.is_default ?? app.is_default) && (
                            <span className="default-badge">Default</span>
                          )}
                        </div>
                        <div className="connection-status">
                          <div className={`status-dot ${isConnected ? 'connected' : 'disconnected'}`} />
                          <span>{isConnected ? 'Connected' : 'Not Connected'}</span>
                          {isConnected && connection?.issued_at && (
                            <>
                              <span className="status-separator">•</span>
                              <span className="connection-date">
                                {formatConnectionDate(connection.issued_at)}
                              </span>
                            </>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })
            ) : null;
          })()}
          {salesforceApps.length === 0 && (
            <div className="connection-card empty-card" onClick={() => openSalesforceConfiguration(null)}>
              <div className="connection-card-content">
                <div className="connection-logo">
                  <div className="salesforce-logo">
                    <img src="/assets/connectors/Salesforce.com_logo.svg (1).png" alt="Salesforce" className="salesforce-icon" />
                  </div>
                </div>
                <div className="connection-info">
                  <div className="connection-name">
                    Connect your first Salesforce org
                  </div>
                  <div className="connection-status">
                    <div className="status-dot disconnected"></div>
                    <span>Not Connected</span>
                  </div>
                </div>
                <div className="connection-actions">
                  <FiMoreHorizontal className="more-icon" />
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Google Sheets Connections */}
      {googleSheetsConnections.length > 0 && (
        <div className="google-sheets-connections-section sql-connectors-section">
          <h3 className="section-title">Google Sheets Connections</h3>
          <div className="connection-card-grid">
            {googleSheetsConnections.map((connection) => (
              <div
                key={connection.id}
                className="connection-card google-sheets-card connected"
                onClick={() => handleGoogleSheetsCardClick(connection)}
              >
                <div className="connection-card-content">
                  <div className="connection-logo">
                    <div className="google-sheets-logo">
                      <img
                        src="/assets/connectors/google-sheets.png"
                        alt="Google Sheets"
                        className="google-sheets-icon"
                      />
                    </div>
                  </div>
                  <div className="connection-info">
                    <div className="connection-name">{gsConnectionLabel(connection)}</div>
                    <div className="connection-status gs-list-connection-status">
                      <div className="status-dot connected" />
                      <span>Connected</span>
                      {connection.google_account_email &&
                        gsHasCustomConnectionName(connection) && (
                          <>
                            <span className="status-separator">·</span>
                            <span className="connection-email">
                              {connection.google_account_email}
                            </span>
                          </>
                        )}
                    </div>
                    <div className="connection-date-line">
                      {formatConnectionDate(connection.created_at)}
                    </div>
                  </div>
                  <div className="connection-actions">
                    <FiMoreHorizontal className="more-icon" />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* SQL Connectors Section */}
      {sqlConnectors.length > 0 && (
        <div className="sql-connectors-section">
          <h3 className="section-title">Database Connections</h3>
          <div className="connection-card-grid">
          {sqlConnectors.map((connector) => (
            <div key={connector.id} className="connection-card sql-connector-card" onClick={() => handleEditSQLConnector(connector)}>
              <div className="connection-card-content">
                <div className="connection-logo">
                  <div className="sql-connector-logo">
                    <img 
                      src={`/assets/connectors/${connector.connector_type === 'postgresql' ? 'postgresql-logo.png' : 
                             connector.connector_type === 'mysql' ? 'MySQL-Logo.wine.png' :
                             connector.connector_type === 'mssql' ? 'mssql-logo.png' :
                             connector.connector_type === 'oracle' ? 'Oracle.png' :
                             connector.connector_type === 'bigquery' ? 'BigQuery.png' :
                             connector.connector_type === 'snowflake' ? 'snowflake-logo.png' : 'Database.png'}`} 
                      alt={connector.connector_type}
                      className={`connector-icon${connector.connector_type === 'snowflake' ? ' connector-icon--snowflake' : ''}`}
                    />
                  </div>
                </div>
                <div className="connection-info">
                  <div className="connection-name">
                    {connector.name}
                    <span className="connector-type">({connector.connector_type})</span>
                  </div>
                  <div className="connection-status">
                    <div className={`status-dot ${connector.is_active ? 'connected' : 'disconnected'}`}></div>
                    <span>{connector.is_active ? 'Connected' : 'Inactive'}</span>
                    <span className="status-separator">•</span>
                    <span className="connection-date">
                      {formatConnectionDate(connector.created_at)}
                    </span>
                  </div>
                </div>
                <div className="connection-actions">
                  <FiMoreHorizontal className="more-icon" />
                </div>
              </div>
            </div>
          ))}
          </div>
        </div>
      )}
    </div>
  );

  // SQL Connector Form View
  const renderSQLConnectorForm = () => {
    const isEdit = Boolean(editingConnector?.id);
    const sqlTableNames = isEdit
      ? (sqlConnectors.find((c) => c.id === editingConnector.id)?.tables || []).map(
          (t) => (typeof t === 'string' ? t : t.table_name),
        )
      : [];

    if (!isEdit) {
      return (
        <div className="sql-connector-view sql-connector-view--add">
          <div className="configuration-header">
            <button type="button" className="back-button" onClick={handleSQLConnectorCancel}>
              <FiArrowLeft />
            </button>
            <h2>Add Database Connection</h2>
          </div>
          <SQLConnectorForm
            connectorType={selectedConnectorType}
            editingConnector={editingConnector}
            onSave={handleSQLConnectorSave}
            onDelete={handleSQLConnectorDelete}
            isLoading={isLoading}
          />
        </div>
      );
    }

    return (
      <div className="configuration-view sql-config-view gs-config-view">
        <div className="configuration-header">
          <button type="button" className="back-button" onClick={handleSQLConnectorCancel}>
            <FiArrowLeft />
          </button>
          <h2>Database Connection</h2>
        </div>

        <div className="connections-content sql-config-content gs-config-content">
          <div className="gs-config-top-row sql-config-top-row">
            <div
              ref={sqlConnectionCardRef}
              className="connection-status-card sql-connection-card gs-connection-card"
            >
              <SQLConnectorForm
                connectorType={selectedConnectorType}
                editingConnector={editingConnector}
                onSave={handleSQLConnectorSave}
                onDelete={handleSQLConnectorDelete}
                isLoading={isLoading}
                embedded
                connectionDescription={sqlConnectionDescription}
                onConnectionDescriptionChange={setSqlConnectionDescription}
              />
            </div>

            <div
              className="connection-status-card sql-tables-card gs-spreadsheets-card"
              style={
                sqlTablesPanelHeight
                  ? { height: sqlTablesPanelHeight, maxHeight: sqlTablesPanelHeight }
                  : undefined
              }
            >
              <SqlConnectorTablesList connectorId={editingConnector.id} />
            </div>
          </div>

          <div className="connection-status-card gs-tabs-section">
            <SqlConnectorTabs
              connectorId={editingConnector.id}
              connectorName={editingConnector.name}
              tableNames={sqlTableNames}
            />
          </div>
        </div>
      </div>
    );
  };

  // Configuration View
  const renderConfigurationView = () => {
    const activeApp = editingSalesforceApp || connectedApp;
    const rawConnections = activeApp ? salesforceConnectionsByApp[activeApp.id] : null;
    const activeConnections = Array.isArray(rawConnections)
      ? rawConnections
      : rawConnections
        ? [rawConnections]
        : [];
    const hasActiveConnection = activeConnections.some((c) => c?.connected);
    // When editing a specific org, show only that org's details
    const displayedConnections = editingSalesforceConnection
      ? activeConnections.filter(
          (c) =>
            (c.org_id && c.org_id === editingSalesforceConnection.org_id) ||
            (c.token_id && String(c.token_id) === String(editingSalesforceConnection.token_id))
        )
      : activeConnections;
    const showOrgSetupHighlight =
      setupHighlightConnectorId != null &&
      editingSalesforceConnection &&
      needsOrgSetup.name &&
      (String(editingSalesforceConnection.token_id) === String(setupHighlightConnectorId) ||
        String(editingSalesforceConnection.org_id) === String(setupHighlightConnectorId));
    const showOrgSetupPrompt = needsOrgSetup.name;

    return (
    <div className="configuration-view">
      <div className="configuration-header">
        <button 
          className="back-button"
          onClick={handleBackToConnections}
        >
          <FiArrowLeft />
        </button>
        <h2>Configure Salesforce Connection</h2>
      </div>

      <div className={`connections-content connections-content--sf-config${isAddingSalesforceApp ? ' connections-content--sf-config-centered' : ''}`}>
        {/* Two columns: left = SSO + connection status; right = org settings (stacks on small screens) */}
        <div className={`sf-config-two-col${isAddingSalesforceApp ? ' sf-config-two-col--single' : ''}`}>
          <div className="sf-config-left">
            {ssoAvailable && isAddingSalesforceApp && (
              <div className="sso-quick-connect-card">
                <p className="sso-quick-connect-label">Connect via SSO</p>
                <div className="sso-buttons">
                  <button
                    type="button"
                    className="btn btn-sso"
                    onClick={() => handleConnectViaSSO('production')}
                    disabled={ssoConnecting}
                  >
                    {ssoConnecting ? (
                      <>
                        <div className="spinner-small" />
                        Connecting...
                      </>
                    ) : (
                      <>
                        <FiLogIn />
                        Connect via SSO (Production)
                      </>
                    )}
                  </button>
                  <button
                    type="button"
                    className="btn btn-sso btn-sso-sandbox"
                    onClick={() => handleConnectViaSSO('sandbox')}
                    disabled={ssoConnecting}
                  >
                    <FiLogIn />
                    Connect via SSO (Sandbox)
                  </button>
                </div>
              </div>
            )}

            {(!isAddingSalesforceApp) && hasActiveConnection && (
              <div className="connection-status-card sf-config-connection-card">
                <div className="connection-status">
                  <div className="status-indicator" />
                  <h3 style={{
                    fontSize: '1.125rem',
                    fontWeight: '600',
                    color: '#065f46',
                    margin: '0'
                  }}>
                    Connected to Salesforce{displayedConnections.length > 1 ? ` (${displayedConnections.length} orgs)` : ''}
                  </h3>
                </div>

                <div className="connection-details sf-config-connection-details">
                  {displayedConnections.map((conn, idx) => (
                    <div
                      key={conn.org_id || conn.token_id || idx}
                      className="sf-config-connection-org-block"
                      style={{
                        marginBottom: idx < displayedConnections.length - 1 ? '16px' : 0,
                        paddingBottom: idx < displayedConnections.length - 1 ? '16px' : 0,
                        borderBottom: idx < displayedConnections.length - 1 ? '1px solid #e5e7eb' : 'none',
                      }}
                    >
                      <div className="sf-connection-meta-row">
                        <span className="sf-connection-meta-label">Username</span>
                        <span className="sf-connection-meta-value">{conn.salesforce_username}</span>
                      </div>
                      <div className="sf-connection-meta-row">
                        <span className="sf-connection-meta-label">Instance URL</span>
                        <a
                          href={conn.instance_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="sf-connection-url-link"
                        >
                          {conn.instance_url}
                        </a>
                      </div>
                      <div className="sf-connection-meta-dual-row">
                        <div className="sf-connection-meta-row">
                          <span className="sf-connection-meta-label">Connected at</span>
                          <span className="sf-connection-meta-value">
                            {formatConnectionDate(conn.issued_at)}
                          </span>
                        </div>
                        {conn.org_id ? (
                          <ConnectorMetadataSyncInline
                            connectorType="salesforce"
                            connectorId={conn.org_id}
                            variant="status"
                            inlineRow
                          />
                        ) : null}
                      </div>
                      {conn.org_id ? (
                        <div className="sf-config-autosync-row">
                          <ConnectorMetadataSyncInline
                            connectorType="salesforce"
                            connectorId={conn.org_id}
                            variant="compact-schedule"
                          />
                        </div>
                      ) : null}
                      {ssoAvailable && conn.is_sso_connection && conn.org_id ? (
                        <div className="sf-config-reconnect-row">
                          <button
                            type="button"
                            className="btn btn-secondary sf-reconnect-btn"
                            onClick={handleReconnectSalesforceSSO}
                            disabled={ssoConnecting}
                            title="Re-authorize this org via its Salesforce login page"
                          >
                            {ssoConnecting ? (
                              <>
                                <div className="spinner-small" />
                                Reconnecting...
                              </>
                            ) : (
                              <>
                                <FiRefreshCw />
                                Reconnect {connectionName || conn.org_display_name || 'org'}
                              </>
                            )}
                          </button>
                        </div>
                      ) : null}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {!isAddingSalesforceApp && (
            <div
              className={`configuration-card sf-config-org-card sf-config-right${
                showOrgSetupHighlight ? ' sf-config-org-card--setup-highlight' : ''
              }`}
            >
              <h3 className="section-header">
                <FiSettings className="configuration-icon" />
                Org Settings
              </h3>

              {showOrgSetupPrompt && editingSalesforceConnection && (
                <p className="sf-org-setup-prompt">
                  Give this org a friendly name so Archian AI knows what it is for.
                </p>
              )}

              <div className="form-group">
                <div className="sf-org-name-env-row">
                  <div className="sf-connection-meta-row">
                    <label className="sf-connection-meta-label" htmlFor="sf-org-name">
                      Org name
                    </label>
                    <input
                      id="sf-org-name"
                      type="text"
                      placeholder="e.g., Production Org, Sandbox, Dev Org"
                      value={connectionName}
                      onChange={(e) => setConnectionName(e.target.value)}
                      className="form-input"
                    />
                  </div>

                  <div className="sf-connection-meta-row">
                    <label className="sf-connection-meta-label" htmlFor="sf-org-environment">
                      Environment
                    </label>
                    <select
                      id="sf-org-environment"
                      value={environment}
                      onChange={(e) => {
                        setEnvironment(e.target.value);
                        if (!connectionName || connectionName === 'Production Org' || connectionName === 'Sandbox Org') {
                          setConnectionName(e.target.value === 'production' ? 'Production Org' : 'Sandbox Org');
                        }
                      }}
                      className="form-select"
                    >
                      <option value="production">Production</option>
                      <option value="sandbox">Sandbox</option>
                    </select>
                  </div>
                </div>

                {editingSalesforceConnection && (
                  <div className="sf-connection-meta-row">
                    <label className="sf-connection-meta-label" htmlFor="sf-org-description">
                      Description{' '}
                      <span className="sf-org-description-hint">
                        (optional — helps Archian AI understand this org)
                      </span>
                    </label>
                    <textarea
                      id="sf-org-description"
                      className="form-input sf-org-description-textarea"
                      rows={3}
                      value={orgDescription}
                      onChange={(e) => setOrgDescription(e.target.value)}
                      placeholder="e.g. Production field service org for UK operations"
                    />
                  </div>
                )}

                {editingSalesforceConnection?.is_sso_connection ? null : (
                  <>
                    <div className="sf-connection-meta-row">
                      <label className="sf-connection-meta-label">
                        Consumer Key
                      </label>
                      <input
                        type="password"
                        placeholder="Enter your Consumer Key"
                        value={clientId}
                        onChange={(e) => setClientId(e.target.value)}
                        className="form-input"
                      />
                    </div>

                    <div className="sf-connection-meta-row">
                      <label className="sf-connection-meta-label">
                        Consumer Secret
                      </label>
                      <input
                        type="password"
                        placeholder="Enter your Consumer Secret"
                        value={clientSecret}
                        onChange={(e) => setClientSecret(e.target.value)}
                        className="form-input"
                      />
                    </div>
                  </>
                )}
              </div>

              <div className="button-group two-column">
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className={`btn btn-primary ${saving ? 'opacity-60' : ''}`}
                >
                  {saving ? (
                    <>
                      <div className="spinner-small"></div>
                      Saving...
                    </>
                  ) : (
                    <>
                      <FiSave />
                      Save
                    </>
                  )}
                </button>

                {editingSalesforceApp?.id && (
                  <button
                    onClick={() => {
                      setSalesforceAppToDelete(editingSalesforceApp);
                      setShowSalesforceDeleteWarning(true);
                    }}
                    className="btn btn-danger"
                    style={{ backgroundColor: '#dc2626', color: '#ffffff' }}
                    disabled={isLoading}
                  >
                    <FiTrash2 />
                    Delete
                  </button>
                )}
              </div>

              {message && (
                <div style={{
                  marginTop: '10px',
                  padding: '6px 8px',
                  borderRadius: '8px',
                  backgroundColor: message.startsWith('❌') ? '#fef2f2' : '#f0fdf4',
                  border: `1px solid ${message.startsWith('❌') ? '#fecaca' : '#bbf7d0'}`,
                  color: message.startsWith('❌') ? '#dc2626' : '#16a34a',
                  fontSize: '0.875rem',
                  fontWeight: '500'
                }}>
                  {message}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Schema browser — full width below the two-column row */}
        {(!isAddingSalesforceApp) && hasActiveConnection && displayedConnections.map((conn, idx) => (
          conn.org_id ? (
            <div key={`sf-schema-${conn.org_id || idx}`} className="connection-status-card sf-schema-section">
              <SalesforceOrgConnectorTabs
                connectorId={conn.token_id}
                orgId={conn.org_id}
                orgDisplayName={conn.org_display_name || conn.salesforce_username || conn.org_id}
                showOrgInTitle={displayedConnections.length > 1}
              />
            </div>
          ) : null
        ))}
      </div>
    </div>
  );
  };

  // Google Sheets Configuration View
  const renderGoogleSheetsConfigurationView = () => {
    const connectionId = selectedGoogleSheetsConnection?.id;
    const accountEmail = selectedGoogleSheetsConnection?.google_account_email;
    const headerLabel = gsConnectionLabel(selectedGoogleSheetsConnection);
    const hasCustomName = gsHasCustomConnectionName(selectedGoogleSheetsConnection);

    return (
    <div className="configuration-view gs-config-view">
      <div className="configuration-header">
        <button
          type="button"
          className="back-button"
          onClick={handleBackFromGoogleSheets}
        >
          <FiArrowLeft />
        </button>
        <h2>{headerLabel}</h2>
      </div>

      <div className="connections-content gs-config-content">
        <div className="gs-config-top-row gs-config-top-row--workbooks">
          <div ref={gsAccountCardRef} className="connection-status-card gs-connection-card">
            <div className="gs-connection-card-top">
              <div className="connection-status gs-connection-card-header">
                <div className="status-indicator" />
                <div className="gs-connection-heading">
                  <h3 className="gs-connection-title">{headerLabel}</h3>
                  {hasCustomName && accountEmail ? (
                    <p className="gs-connection-subtitle">{accountEmail}</p>
                  ) : null}
                </div>
              </div>
            </div>
            <div className="gs-connection-meta">
              {!hasCustomName && (
                <div className="sf-connection-meta-row">
                  <span className="sf-connection-meta-label">Account</span>
                  <span className="sf-connection-meta-value">
                    {accountEmail || 'Unknown'}
                  </span>
                </div>
              )}
              <div className="sf-connection-meta-row">
                <span className="sf-connection-meta-label">Connected at</span>
                <span className="sf-connection-meta-value">
                  {formatConnectionDate(selectedGoogleSheetsConnection?.created_at)}
                </span>
              </div>
              {connectionId ? (
                <ConnectorMetadataSyncInline
                  connectorType="google_sheets"
                  connectorId={connectionId}
                  variant="status"
                />
              ) : null}
            </div>

            {connectionId && (
              <div className="gs-connection-settings">
                <h4 className="gs-connection-settings-title">Connection settings</h4>
                <div className="gs-connection-settings-fields">
                  <div className="gs-form-field">
                    <label className="form-label" htmlFor="gs-connection-name">
                      Connection name
                    </label>
                    <p className="gs-field-hint">
                      Optional — defaults to account email if left blank.
                    </p>
                    <input
                      id="gs-connection-name"
                      type="text"
                      className="form-input"
                      value={gsConnectionName}
                      onChange={(e) => setGsConnectionName(e.target.value)}
                      placeholder={accountEmail || 'e.g. Marketing team sheets'}
                    />
                  </div>
                  <div className="gs-form-field">
                    <label className="form-label" htmlFor="gs-connection-description">
                      Description
                    </label>
                    <p className="gs-field-hint">
                      Optional — helps Archian AI understand this connection.
                    </p>
                    <textarea
                      id="gs-connection-description"
                      className="form-input sf-org-description-textarea"
                      rows={3}
                      value={gsConnectionDescription}
                      onChange={(e) => setGsConnectionDescription(e.target.value)}
                      placeholder="e.g. Marketing budget trackers and campaign ROI sheets"
                    />
                  </div>
                  <ConnectorMetadataSyncInline
                    connectorType="google_sheets"
                    connectorId={connectionId}
                    variant="schedule"
                  />
                </div>
              </div>
            )}

            <div className="gs-connection-actions">
              {connectionId && (
                <>
                  <button
                    type="button"
                    onClick={handleReconnectGoogleSheets}
                    disabled={gsReconnecting || gsSaving || isLoading}
                    className={`btn btn-secondary gs-reconnect-btn ${gsReconnecting ? 'opacity-60' : ''}`}
                    title="Re-authorize Google to refresh expired or revoked access"
                  >
                    {gsReconnecting ? (
                      <>
                        <div className="spinner-small" />
                        Reconnecting…
                      </>
                    ) : (
                      <>
                        <FiRefreshCw />
                        Reconnect
                      </>
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={handleGoogleSheetsSettingsSave}
                    disabled={gsSaving || gsReconnecting}
                    className={`btn btn-primary gs-save-settings-btn ${gsSaving ? 'opacity-60' : ''}`}
                  >
                    {gsSaving ? (
                      <>
                        <div className="spinner-small" />
                        Saving…
                      </>
                    ) : (
                      <>
                        <FiSave />
                        Save
                      </>
                    )}
                  </button>
                </>
              )}
              <button
                type="button"
                onClick={handleDeleteGoogleSheetsConnection}
                disabled={isLoading || gsReconnecting}
                className="btn btn-danger gs-delete-connection-btn"
              >
                {isLoading ? (
                  <>
                    <div className="spinner-small" />
                    Deleting…
                  </>
                ) : (
                  <>
                    <FiTrash2 />
                    Delete connection
                  </>
                )}
              </button>
            </div>

            {gsMessage && (
              <div
                className={`gs-settings-message${
                  gsMessage.startsWith('❌') ? ' gs-settings-message--error' : ' gs-settings-message--success'
                }`}
              >
                {gsMessage}
              </div>
            )}
          </div>

          <div
            className="connection-status-card gs-spreadsheets-card"
            style={
              gsWorkbookPanelHeight
                ? { height: gsWorkbookPanelHeight, maxHeight: gsWorkbookPanelHeight }
                : undefined
            }
          >
            <GoogleSheetsSpreadsheetsList
              spreadsheets={googleSheetsSpreadsheets}
              isLoading={isLoading}
              syncingSpreadsheetId={syncingSpreadsheetId}
              onResync={handleResyncSpreadsheet}
              onDelete={handleDeleteSpreadsheet}
            />
          </div>
        </div>

        {connectionId && (
          <div className="connection-status-card gs-tabs-section">
            <GoogleSheetsConnectorTabs
              connectionId={connectionId}
              accountEmail={accountEmail}
            />
          </div>
        )}
      </div>
    </div>
    );
  };

  return (
    <div className="connections-page">
      {showSQLConnectorForm ? (
        /* SQL Connector Form View */
        renderSQLConnectorForm()
      ) : showGoogleSheetsConfig ? (
        /* Google Sheets Configuration View */
        renderGoogleSheetsConfigurationView()
      ) : !showConfiguration ? (
        <>
          {/* Header Section */}
          <div className="connections-header">
            <h1>
              <FiDatabase />
              Connections
            </h1>
            <p className="connections-description">
              Manage your connected services and integrations.
            </p>
          </div>

          {renderConnectionCards()}
        </>
      ) : (
        /* Salesforce Configuration View */
        renderConfigurationView()
      )}

      {/* Success Modal */}
      <SuccessModal
        isOpen={showSuccessModal}
        onClose={handleCloseSuccessModal}
        message={modalMessage}
        type={modalType}
        context={modalContext}
      />

      {/* Delete Warning Modal */}
      <WarningModal
        isOpen={showDeleteWarning}
        onClose={handleCancelDelete}
        errorMessage={connectorToDelete ? `Are you sure you want to delete the connection "${connectorToDelete.name}"?

This will permanently delete:
• The database connection
• All extracted schema data  
• All associated analysis results

This action cannot be undone.` : ''}
        onReconnect={handleConfirmDelete}
        customButtons={true}
        confirmText="Delete Connection"
        confirmIcon={FiTrash2}
        customTitle="Delete Database Connection"
        showTips={false}
      />

      {/* Google Sheets Connection Delete Warning Modal */}
      <WarningModal
        isOpen={showGoogleSheetsDeleteWarning}
        onClose={handleCancelGoogleSheetsDelete}
        errorMessage={`Are you sure you want to delete the Google Sheets connection for ${selectedGoogleSheetsConnection?.google_account_email || 'this account'}?

This will permanently delete:
• This Google account connection
• All workbooks linked to this account

This action cannot be undone.`}
        onReconnect={handleConfirmGoogleSheetsDelete}
        customButtons={true}
        confirmText="Delete Connection"
        confirmIcon={FiTrash2}
        customTitle="Delete Google Sheets Connection"
        showTips={false}
      />

      {/* Salesforce Connected App Delete Warning Modal */}
      <WarningModal
        isOpen={showSalesforceDeleteWarning}
        onClose={() => {
          setShowSalesforceDeleteWarning(false);
          setSalesforceAppToDelete(null);
        }}
        errorMessage={salesforceAppToDelete ? `Are you sure you want to delete the Salesforce connection "${salesforceAppToDelete.connection_name || 'Salesforce Org'}"?

This will remove the connected app credentials and revoke any active Salesforce tokens associated with this org.

This action cannot be undone.` : ''}
        onReconnect={async () => {
          if (!salesforceAppToDelete) return;
          setShowSalesforceDeleteWarning(false);
          setIsLoading(true);

          try {
            // Token-centric: delete single connection by token_id or org_id; delete whole app only for orphan apps (no token)
            const hasToken = salesforceAppToDelete.token_id || salesforceAppToDelete.org_id;
            const identifier = hasToken
              ? (salesforceAppToDelete.token_id || salesforceAppToDelete.org_id)
              : salesforceAppToDelete.id;
            const endpoint = hasToken
              ? `/api/salesforce-connection/${identifier}`
              : `/api/salesforce-connected-app/${salesforceAppToDelete.id}`;
            const response = await api.delete(endpoint);
            if (response.data?.success) {
              setModalMessage('✅ Salesforce connection deleted successfully!');
              setModalType('success');
              setModalContext('delete');
              setShowSuccessModal(true);
              if (editingSalesforceApp?.id === salesforceAppToDelete.id) {
                setShowConfiguration(false);
                resetSalesforceForm();
              }
              await fetchConnectedApp();
              await fetchSalesforceConnection();
            } else {
              setModalMessage('❌ Failed to delete Salesforce connection. Please try again.');
              setModalType('error');
              setShowSuccessModal(true);
            }
          } catch (error) {
            console.error('Error deleting Salesforce connection:', error);
            setModalMessage('❌ Failed to delete Salesforce connection. Please try again.');
            setModalType('error');
            setShowSuccessModal(true);
          } finally {
            setIsLoading(false);
            setSalesforceAppToDelete(null);
          }
        }}
        customButtons={true}
        confirmText="Delete Connection"
        confirmIcon={FiTrash2}
        customTitle="Delete Salesforce Connection"
        showTips={false}
      />

      {/* Individual Spreadsheet Delete Warning Modal */}
      <WarningModal
        isOpen={showSpreadsheetDeleteWarning}
        onClose={handleCancelSpreadsheetDelete}
        errorMessage={spreadsheetToDelete ? `Are you sure you want to delete the spreadsheet "${spreadsheetToDelete.name}"?

This will permanently delete:
• The spreadsheet and all its sheets

This action cannot be undone.` : ''}
        onReconnect={handleConfirmSpreadsheetDelete}
        customButtons={true}
        confirmText="Delete Spreadsheet"
        confirmIcon={FiTrash2}
        customTitle="Delete Spreadsheet"
        showTips={false}
      />

      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(20px); }
          to { opacity: 1; transform: translateY(0); }
        }
        
        @keyframes slideInLeft {
          from { opacity: 0; transform: translateX(-30px); }
          to { opacity: 1; transform: translateX(0); }
        }
        
        @keyframes slideInRight {
          from { opacity: 0; transform: translateX(30px); }
          to { opacity: 1; transform: translateX(0); }
        }
        
        @keyframes slideInUp {
          from { opacity: 0; transform: translateY(20px); }
          to { opacity: 1; transform: translateY(0); }
        }
        
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.5; }
        }

        /* Google Sheets Configuration Styles */
        .spreadsheets-list {
          display: flex;
          flex-direction: column;
          gap: 16px;
        }

        .spreadsheet-item {
          border: 1px solid #e5e7eb;
          border-radius: 8px;
          padding: 16px;
          background: #f9fafb;
        }

        .spreadsheet-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 12px;
        }

        .spreadsheet-info {
          display: flex;
          flex-direction: column;
          gap: 4px;
        }

        .delete-spreadsheet-btn {
          background: #dc2626;
          color: white;
          border: none;
          border-radius: 6px;
          padding: 8px 12px;
          cursor: pointer;
          display: flex;
          align-items: center;
          gap: 4px;
          font-size: 0.875rem;
          transition: background-color 0.2s;
        }

        .delete-spreadsheet-btn:hover:not(:disabled) {
          background: #b91c1c;
        }

        .delete-spreadsheet-btn:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }

        .spreadsheet-name {
          font-size: 1rem;
          font-weight: 600;
          color: #374151;
          margin: 0;
        }

        .spreadsheet-id {
          font-size: 0.75rem;
          color: #6b7280;
          font-family: monospace;
        }

        .spreadsheet-sheets {
          display: flex;
          flex-direction: column;
          gap: 8px;
        }

        .sheet-item {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 8px 12px;
          background: white;
          border-radius: 6px;
          border: 1px solid #e5e7eb;
        }

        .sheet-name {
          font-weight: 500;
          color: #374151;
        }

        .sheet-category {
          font-size: 0.875rem;
          color: #6b7280;
          background: #f3f4f6;
          padding: 4px 8px;
          border-radius: 4px;
        }

        .no-spreadsheets {
          text-align: center;
          padding: 40px 20px;
          color: #6b7280;
        }

        .no-spreadsheets p {
          margin: 8px 0;
        }
      `}</style>
    </div>
  );
};

export default ConnectionsPage;

