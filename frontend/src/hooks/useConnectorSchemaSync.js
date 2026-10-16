import { useCallback, useEffect, useState } from 'react';
import api from '../api';
import { useUserTimezone } from './useUserTimezone';
import { buildScheduleConfig, parseScheduleConfig } from '../utils/schemaMapLayout';

function apiBase(connectorType, connectorId) {
  return `/api/connectors/${connectorType}/${connectorId}`;
}

/** Default auto-sync when no schedule is saved yet (UI draft only until Save). */
export const DEFAULT_SYNC_FREQUENCY = 'daily';
export const DEFAULT_SYNC_TIME = '02:00';

/**
 * Loads schema sync schedule + graph summary for a connector (Salesforce org_id, etc.).
 */
export function useConnectorSchemaSync(connectorType, connectorId) {
  const { userTimezone } = useUserTimezone();
  const [schedule, setSchedule] = useState(null);
  const [graph, setGraph] = useState(null);
  const [loading, setLoading] = useState(Boolean(connectorId));
  const [unavailable, setUnavailable] = useState(false);
  const [error, setError] = useState(null);
  const [savingSchedule, setSavingSchedule] = useState(false);

  const [draftFreq, setDraftFreq] = useState(DEFAULT_SYNC_FREQUENCY);
  const [timeOfDay, setTimeOfDay] = useState(DEFAULT_SYNC_TIME);
  const [dayOfMonth, setDayOfMonth] = useState(1);
  const [month, setMonth] = useState(1);

  const applyScheduleToForm = useCallback((data) => {
    if (data?.schedule_enabled && data?.schedule_frequency) {
      const parsed = parseScheduleConfig(data);
      const freq =
        parsed.frequency === 'monthly' ? DEFAULT_SYNC_FREQUENCY : parsed.frequency;
      setDraftFreq(freq || DEFAULT_SYNC_FREQUENCY);
      setTimeOfDay(parsed.timeOfDay || DEFAULT_SYNC_TIME);
      setDayOfMonth(parsed.dayOfMonth ?? 1);
      setMonth(parsed.month ?? 1);
    } else {
      setDraftFreq(DEFAULT_SYNC_FREQUENCY);
      setTimeOfDay(DEFAULT_SYNC_TIME);
      setDayOfMonth(1);
      setMonth(1);
    }
  }, []);

  const refresh = useCallback(async () => {
    if (!connectorType || !connectorId) {
      setLoading(false);
      return;
    }
    const base = apiBase(connectorType, connectorId);
    setLoading(true);
    setError(null);
    setUnavailable(false);
    try {
      const [schedRes, graphRes] = await Promise.all([
        api.get(`${base}/schema-schedule`),
        api.get(`${base}/schema-graph`, { params: { scope: 'neighborhood' } }),
      ]);
      setSchedule(schedRes.data);
      applyScheduleToForm(schedRes.data);
      setGraph(graphRes.data?.graph ?? null);
    } catch (e) {
      if (e.response?.status === 503) {
        setUnavailable(true);
        setSchedule(null);
        setGraph(null);
      } else {
        setError(e.response?.data?.detail || 'Failed to load sync status');
      }
    } finally {
      setLoading(false);
    }
  }, [connectorType, connectorId, applyScheduleToForm]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const saveSchedule = useCallback(async () => {
    if (!connectorType || !connectorId) return false;
    const base = apiBase(connectorType, connectorId);
    setError(null);
    setSavingSchedule(true);
    const enabled = Boolean(draftFreq);
    try {
      const scheduleConfig = enabled
        ? buildScheduleConfig({
            frequency: draftFreq,
            timeOfDay,
            dayOfMonth,
            month,
          })
        : {};
      const { data } = await api.put(`${base}/schema-schedule`, {
        schedule_enabled: enabled,
        schedule_frequency: enabled ? draftFreq : null,
        schedule_config: scheduleConfig,
        schedule_timezone: userTimezone || 'UTC',
        sync_mode: 'schema_and_categorize',
        graph_scope: 'neighborhood',
      });
      setSchedule((s) => ({
        ...s,
        ...data,
        schedule_enabled: enabled,
        schedule_frequency: draftFreq,
        schedule_config: scheduleConfig,
        last_success_at: s?.last_success_at,
      }));
      await refresh();
      return true;
    } catch (e) {
      const detail = e.response?.data?.detail;
      setError(typeof detail === 'string' ? detail : detail || 'Failed to save auto-sync');
      return false;
    } finally {
      setSavingSchedule(false);
    }
  }, [
    connectorType,
    connectorId,
    draftFreq,
    timeOfDay,
    dayOfMonth,
    month,
    userTimezone,
    refresh,
  ]);

  const graphSummary = graph?.summary;
  const nodeCount = graphSummary?.node_count ?? graph?.nodes?.length;
  const edgeCount = graphSummary?.edge_count ?? graph?.edges?.length;
  const scheduleEnabled = Boolean(schedule?.schedule_enabled && schedule?.schedule_frequency);

  return {
    schedule,
    graph,
    graphSummary,
    nodeCount,
    edgeCount,
    loading,
    unavailable,
    error,
    savingSchedule,
    draftFreq,
    setDraftFreq,
    timeOfDay,
    setTimeOfDay,
    dayOfMonth,
    setDayOfMonth,
    month,
    setMonth,
    scheduleEnabled,
    saveSchedule,
    refresh,
  };
}
