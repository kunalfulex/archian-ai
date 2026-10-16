import { useCallback, useEffect, useState } from 'react';
import api from '../api';
import { watchEnterpriseGraphRun } from '../assessment/assessmentSse';

export function useLakeProposal(graphId) {
  const [proposal, setProposal] = useState(null);
  const [versions, setVersions] = useState([]);
  const [selectedVersionId, setSelectedVersionId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState(null);
  const [approving, setApproving] = useState(false);
  const [redesigning, setRedesigning] = useState(false);
  const [executing, setExecuting] = useState(false);

  const loadVersions = useCallback(async () => {
    if (!graphId) return [];
    try {
      const { data } = await api.get(`/api/enterprise-graphs/${graphId}/lake-proposals`, {
        params: { summary: true, limit: 20 },
      });
      const list = data.proposals || [];
      setVersions(list);
      return list;
    } catch {
      setVersions([]);
      return [];
    }
  }, [graphId]);

  const loadProposalById = useCallback(
    async (proposalId) => {
      if (!graphId || !proposalId) return;
      setLoading(true);
      setError(null);
      setNotFound(false);
      try {
        const { data } = await api.get(
          `/api/enterprise-graphs/${graphId}/lake-proposals/${proposalId}`,
        );
        setProposal(data.proposal || null);
        setSelectedVersionId(proposalId);
      } catch (e) {
        if (e.response?.status === 404) {
          setProposal(null);
          setNotFound(true);
        } else {
          setError(e.response?.data?.detail || e.message || 'Failed to load lake proposal');
        }
      } finally {
        setLoading(false);
      }
    },
    [graphId],
  );

  const loadProposal = useCallback(async () => {
    if (!graphId) return;
    const list = await loadVersions();
    if (list.length > 0) {
      await loadProposalById(list[0].id);
    } else {
      setLoading(true);
      setError(null);
      setNotFound(false);
      try {
        const { data } = await api.get(`/api/enterprise-graphs/${graphId}/lake-proposals/latest`);
        setProposal(data.proposal || null);
        setSelectedVersionId(data.proposal?.id || null);
      } catch (e) {
        if (e.response?.status === 404) {
          setProposal(null);
          setNotFound(true);
        } else {
          setError(e.response?.data?.detail || e.message || 'Failed to load lake proposal');
        }
      } finally {
        setLoading(false);
      }
    }
  }, [graphId, loadProposalById, loadVersions]);

  useEffect(() => {
    loadProposal();
  }, [loadProposal]);

  const selectVersion = useCallback(
    (proposalId) => {
      if (proposalId && proposalId !== selectedVersionId) {
        loadProposalById(proposalId);
      }
    },
    [loadProposalById, selectedVersionId],
  );

  const approve = async () => {
    if (!proposal?.id) return;
    setApproving(true);
    setError(null);
    try {
      const { data } = await api.post(
        `/api/enterprise-graphs/${graphId}/lake-proposals/${proposal.id}/approve`,
      );
      setProposal(data.proposal);
      await loadVersions();
    } catch (e) {
      setError(e.response?.data?.detail || e.message || 'Approve failed');
    } finally {
      setApproving(false);
    }
  };

  const redesign = async () => {
    setRedesigning(true);
    setError(null);
    try {
      const { data } = await api.post(`/api/enterprise-graphs/${graphId}/runs`, {
        depth: 'partial',
        run_type: 'lake_redesign',
      });
      await watchEnterpriseGraphRun(data.run_id);
      await loadProposal();
    } catch (e) {
      setError(e.response?.data?.detail?.message || e.message || 'Redesign failed');
    } finally {
      setRedesigning(false);
    }
  };

  const executeDdl = async () => {
    if (!proposal?.id) return;
    setExecuting(true);
    setError(null);
    try {
      await api.post(`/api/enterprise-graphs/${graphId}/lake-proposals/${proposal.id}/execute`);
      await loadProposalById(proposal.id);
      await loadVersions();
    } catch (e) {
      setError(e.response?.data?.detail || e.message || 'Run DDL failed');
    } finally {
      setExecuting(false);
    }
  };

  const exportLineage = () => {
    if (!proposal) return;
    const payload = {
      source_to_lake_map: proposal.source_to_lake_map,
      bronze_tables: proposal.logical_model?.bronze_tables,
      silver_views: proposal.logical_model?.silver_views,
      zones: proposal.logical_model?.zones,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `lake-lineage-${graphId}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return {
    proposal,
    versions,
    selectedVersionId,
    selectVersion,
    loading,
    notFound,
    error,
    setError,
    approving,
    redesigning,
    executing,
    loadProposal,
    approve,
    redesign,
    executeDdl,
    exportLineage,
  };
}
