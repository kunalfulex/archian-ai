import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import api, { cancelJobs } from './api';
import {
  normalizePendingSync,
  streamConnectorReadiness,
  connectorTypeBannerLabel,
  readinessSetupCta,
} from './connector/connectorReadinessSse';
import ChatInputBar from './components/ChatInputBar';
import { createEmptyOperationPreview } from './architect/reduceOperationPreview';
import { createEmptyPlanningActivity } from './architect/reducePlanningActivity';
import { createEmptyRecoveryActivity } from './architect/reduceRecoveryActivity';
import { createInlineMappingOperationPreview } from './architect/operationPhaseRegistry';
import { migrationStatusPropsCache } from './architect/computeMigrationStatusProps';
import { formatFastApiDetail } from './architect/architectErrorMessages';
import ChatHistoryEntry from './components/ChatHistoryEntry';
import SyncNotificationBar from './components/SyncNotificationBar';
import ChatWelcomeSection, { rotatingExamples } from './components/ChatWelcomeSection';
import ChatStreamingStatus from './components/ChatStreamingStatus';
import { mapServerMessageToEntry as mapServerMessageToEntryImpl } from './architect/mapServerMessageToEntry';
import { getLoadingMessages as getLoadingMessagesImpl } from './architect/getLoadingMessages';
import { handleAgentMigrateDataPrompt as handleAgentMigrateDataPromptImpl } from './architect/handleAgentMigrateDataPrompt';
import { handlePreviewDestinationPick as handlePreviewDestinationPickImpl } from './architect/handlePreviewDestinationPick';
import { applyTaskJobStatusToUi as applyTaskJobStatusToUiImpl } from './architect/applyTaskJobStatusToUi';
import { loadConversationMessages as loadConversationMessagesImpl } from './architect/loadConversationMessages';
import { handleAgentMapFieldsReady as handleAgentMapFieldsReadyImpl } from './architect/handleAgentMapFieldsReady';
import { fetchMappingData as fetchMappingDataImpl } from './architect/fetchMappingData';
import { fetchExtractionData as fetchExtractionDataImpl } from './architect/fetchExtractionData';
import { createArchitectStreamResultHandlers } from './architect/architectStreamResultHandlers';
import { sendChatMessage } from './architect/sendChatMessage';
import { createMigrationStreamHandlers } from './architect/applyMigrationStreamToChat';
import { syncExtractionPreviewFromServer } from './architect/recoverExtractionPreviewFromServer';
import { useArchitectStream } from './hooks/useArchitectStream';
import { usePlannerResume } from './hooks/usePlannerResume';
import { createPlannerStreamHandlers } from './architect/plannerStreamHandlers';
import {
  latestActivePlannerEntryId,
  syncPlannerPendingRefFromEntry,
} from './architect/plannerState';
import './ChatUploader.css';

function takeStartNewChatIntent(routeRef) {
  if (routeRef.current) {
    routeRef.current = false;
    return true;
  }
  try {
    if (localStorage.getItem('startNewChat') === 'true') {
      localStorage.removeItem('startNewChat');
      return true;
    }
  } catch {
    // ignore storage errors
  }
  return false;
}

export default function ChatUploader() {
  const location = useLocation();
  const navigate = useNavigate();
  const textareaRef = useRef(null);
  const scrollRef = useRef(null); // 👈 new scroll anchor
  const loadingMessageIntervalRef = useRef(null);
  const fileInputRef = useRef(null);
  const mapButtonRef = useRef(null);
  const startMigrationButtonRef = useRef(null);

  const [selectedFile, setSelectedFile] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const [userMessage, setUserMessage] = useState('');
  const [planAutoRun, setPlanAutoRun] = useState(() => {
    try {
      const v = localStorage.getItem('archianai_plan_auto_run');
      return JSON.parse(v || 'false');
    } catch {
      return false;
    }
  });
  const planAutoRunRef = useRef(planAutoRun);
  useEffect(() => {
    planAutoRunRef.current = planAutoRun;
    try {
      localStorage.setItem('archianai_plan_auto_run', JSON.stringify(planAutoRun));
    } catch (_) {}
  }, [planAutoRun]);

  const migrationConfirmJobRef = useRef(null);
  const mappingLearningFinalizeTimersRef = useRef({});
  const mappingContextPersistTimersRef = useRef({});

  const [chatHistory, setChatHistory] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [loadingMessage, setLoadingMessage] = useState('thinking');
  const [exampleIndex, setExampleIndex] = useState(0);

  const [streamingText, setStreamingText] = useState(''); // New state for streaming display
  const [agentTurnSteps, setAgentTurnSteps] = useState([]);
  const [operationPreview, setOperationPreview] = useState(createEmptyOperationPreview);
  const [planningActivity, setPlanningActivity] = useState(createEmptyPlanningActivity);
  const [recoveryActivity, setRecoveryActivity] = useState(createEmptyRecoveryActivity);
  const operationPreviewRef = useRef(operationPreview);
  const agentTurnStepsRef = useRef([]);
  useEffect(() => {
    operationPreviewRef.current = operationPreview;
  }, [operationPreview]);
  useEffect(() => {
    agentTurnStepsRef.current = agentTurnSteps;
  }, [agentTurnSteps]);
  const [hasSalesforceConnection, setHasSalesforceConnection] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [, setConversations] = useState([]);
  const [activeConversationId, setActiveConversationId] = useState(null);
  const [isConversationsLoading, setIsConversationsLoading] = useState(true);
  const [isConversationLoading, setIsConversationLoading] = useState(false);

  // Dispatch event when active conversation changes (for Sidebar highlighting)
  useEffect(() => {
    activeConversationIdRef.current = activeConversationId;
    window.dispatchEvent(new CustomEvent('active-conversation-changed', {
      detail: { conversationId: activeConversationId ?? null },
    }));
  }, [activeConversationId]);
  const [, setConversationError] = useState(null);
  const [inlineDataPreviews, setInlineDataPreviews] = useState({}); // job_id -> preview data
  const [pollingJobs, setPollingJobs] = useState(new Set()); // Track which jobs we're polling
  const [mappingPanels, setMappingPanels] = useState({}); // mapping_id -> mapping data

  const [migrationStatuses, setMigrationStatuses] = useState({}); // job_id -> status data
  const [pendingReExtract, setPendingReExtract] = useState(null); // { jobId, sourceType } - UI state only, re-extraction handled via streaming API
  const [syncBanners, setSyncBanners] = useState([]); // Array of sync banners: [{ type: 'salesforce'|'sql'|'gsheets', name: string, orgId/connectorId: string, isComplete: boolean }]
  
  // Refs to access latest state values without causing dependency issues
  const chatHistoryRef = useRef([]);
  const mappingPanelsRef = useRef({});
  const inlineDataPreviewsRef = useRef({});
  const migrationStatusesRef = useRef({});
  const processingPreferenceUpdates = useRef(new Set()); // Track mappingIds currently being processed
  const conversationsInitializedRef = useRef(false); // Track if conversations have been initialized to prevent infinite loops
  const startNewChatFromRouteRef = useRef(false);
  const conversationLoadGenerationRef = useRef(0);
  const activeConversationIdRef = useRef(null);
  const abortControllerRef = useRef(null);
  const cancelledRef = useRef(false);
  const plannerPendingRef = useRef(null);
  const activePollIntervalsRef = useRef(new Set()); // Track poll intervals for abort/stop
  const activeJobIdsRef = useRef(new Set()); // Extraction/migration job ids for server-side cancel
  const appliedExtractionJobFinishIdsRef = useRef(new Set());

  // Keep refs in sync with state
  useEffect(() => {
    chatHistoryRef.current = chatHistory;
  }, [chatHistory]);

  const activePlannerEntryId = useMemo(
    () => latestActivePlannerEntryId(chatHistory),
    [chatHistory],
  );

  useEffect(() => {
    syncPlannerPendingRefFromEntry(plannerPendingRef, chatHistory, activePlannerEntryId);
  }, [activePlannerEntryId, chatHistory]);
  
  useEffect(() => {
    mappingPanelsRef.current = mappingPanels;
  }, [mappingPanels]);
  
  useEffect(() => {
    inlineDataPreviewsRef.current = inlineDataPreviews;
  }, [inlineDataPreviews]);
  
  useEffect(() => {
    migrationStatusesRef.current = migrationStatuses;
  }, [migrationStatuses]);

  useEffect(() => {
    const interval = setInterval(() => {
      setExampleIndex((prevIndex) => (prevIndex + 1) % rotatingExamples.length);
    }, 4000);
    return () => clearInterval(interval);
  }, []);

  // Mobile detection
  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth <= 768);
    };
    
    checkMobile();
    window.addEventListener('resize', checkMobile);
    
    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  // Sidebar detection
  useEffect(() => {
    const checkSidebar = () => {
      const sidebar = document.querySelector('.sidebar');
      if (sidebar) {
        setSidebarCollapsed(sidebar.classList.contains('collapsed'));
      }
    };

    // Check immediately and periodically
    checkSidebar();
    const interval = setInterval(checkSidebar, 500);
    
    return () => clearInterval(interval);
  }, []);


  // Connector agent readiness (SSE) for newly connected sources in sessionStorage
  useEffect(() => {
    let isMounted = true;
    const abort = new AbortController();
    let dismissTimer = null;

    const startReadinessStream = async () => {
      const pendingSyncStr = sessionStorage.getItem('pending_sync');
      if (!pendingSyncStr) {
        if (isMounted) setSyncBanners([]);
        return;
      }

      let pendingSync;
      try {
        pendingSync = normalizePendingSync(JSON.parse(pendingSyncStr));
      } catch {
        return;
      }
      if (!pendingSync) return;

      const { connectorType, connectorInstanceId, displayName, trigger } = pendingSync;
      const bannerType =
        connectorType === 'google_sheets' ? 'gsheets' : connectorType;

      const showBanner = (evt, isComplete) => {
        if (!isMounted) return;
        setSyncBanners([
          {
            type: bannerType,
            name: evt?.display_name || displayName,
            connectorType,
            connectorInstanceId,
            trigger,
            isComplete,
            phase: evt?.phase,
            entityStats: evt?.entity_labeling_stats,
            metadataSyncProgress: evt?.metadata_sync_progress,
            needsConnectorSetup: Boolean(evt?.needs_connector_setup),
            needsOrgName: Boolean(evt?.needs_org_name),
            needsOrgDescription: Boolean(evt?.needs_org_description),
            setupConnectionsPath: evt?.setup_connections_path || null,
          },
        ]);
      };

      showBanner({ display_name: displayName, phase: 'metadata_sync' }, false);

      try {
        await streamConnectorReadiness({
          connectorType,
          connectorInstanceId,
          signal: abort.signal,
          onProgress: (evt) => showBanner(evt, false),
          onFinished: (evt) => {
            showBanner(evt, true);
            const setupCta = readinessSetupCta({
              trigger,
              needsOrgName: evt?.needs_org_name,
              needsOrgDescription: evt?.needs_org_description,
            });
            const dismissMs = setupCta.show ? 20000 : 5000;
            dismissTimer = setTimeout(() => {
              if (isMounted) setSyncBanners([]);
              sessionStorage.removeItem('pending_sync');
            }, dismissMs);
          },
        });
      } catch (err) {
        if (err?.name === 'AbortError') return;
        // Keep banner visible; user can dismiss manually
      }
    };

    startReadinessStream();

    return () => {
      isMounted = false;
      abort.abort();
      if (dismissTimer) clearTimeout(dismissTimer);
    };
  }, []);

  // Check Salesforce connection status when component mounts
  useEffect(() => {
    let isMounted = true;
    
    const checkSalesforceConnection = async () => {
      try {
        const response = await api.get('/salesforce/connection-status');
        
        if (isMounted && response.data.status === 'success') {
          const connectionStatus = response.data.has_salesforce_connection;
          setHasSalesforceConnection(connectionStatus);
        }
              } catch (error) {
          // Default to false if there's an error
          if (isMounted) {
            setHasSalesforceConnection(false);
          }
        }
    };

    checkSalesforceConnection();
    
    // Cleanup function to prevent memory leaks
    return () => {
      isMounted = false;
    };
  }, []); // Empty dependency array - only run once on mount

  useEffect(() => {
    if (textareaRef.current) textareaRef.current.focus();
  }, []);


  // Helper function to add message directly to chat history
  const addMessage = useCallback((messageText, entryProps = {}) => {
    // Create full entry object with defaults
    const entry = {
      id: entryProps.id || `message-${Date.now()}`,
      type: entryProps.type || 'system',
      text: messageText,
      timestamp: entryProps.timestamp || Date.now(),
      ...entryProps // Allow overriding any properties (jobId, metadata, etc.)
    };
    
    // Clear streaming text if present
    setStreamingText('');
    
    // Add message directly to chat history
    setChatHistory((prev) => [...prev, entry]);
  }, []);

  // 👇 Auto-scroll on new message (but not on state updates like loading)
  const prevChatHistoryLength = useRef(0);
  useEffect(() => {
    // Only scroll if a new message was added (length increased), not on state updates
    const currentLength = chatHistory.length;
    const isNewMessage = currentLength > prevChatHistoryLength.current;
    prevChatHistoryLength.current = currentLength;
    
    // Don't auto-scroll for system/preference update messages - they're not user-facing messages
    const lastMessage = chatHistory[chatHistory.length - 1];
    const isPreferenceUpdate = lastMessage?.metadata?.event_type === 'mapping_preference_updated' || 
                                lastMessage?.id?.startsWith('preference-updated-');
    
    if (isNewMessage && !isPreferenceUpdate && scrollRef.current) {
      scrollRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [chatHistory]);

  const handleFileChange = (e) => {
    const file = e.target.files && e.target.files[0];
    if (file) {
      setSelectedFile(file);
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files.length > 0) {
      setSelectedFile(e.dataTransfer.files[0]);
    }
  };

  const handleDragOver = (e) => {
    e.preventDefault();
    setDragOver(true);
  };

  const handleDragLeave = () => {
    setDragOver(false);
  };

  const mapServerMessageToEntry = useCallback(
    (message) =>
      mapServerMessageToEntryImpl(
        message,
      ),
    [],
  );


  const upsertConversation = useCallback((conversation) => {
    if (!conversation) return;
    setConversations((prev) => {
      const existing = prev.filter((c) => c.id !== conversation.id);
      return [conversation, ...existing];
    });
  }, []);

  // Note: loadConversationMessages is defined after syncMigrationTerminalFromServer usage

  const handleStartNewConversation = useCallback(async (autoSelect = true) => {
    // Invalidate in-flight hydrate requests so they cannot overwrite a fresh chat.
    conversationLoadGenerationRef.current += 1;
    setIsConversationLoading(false);
    // Don't create conversation yet - just clear UI state
    // Conversation will be created automatically when first message is sent
    setConversationError(null);
    if (autoSelect) {
      // Generate a UUID for the new conversation - it will be created when first message is sent
      const generateUUID = () => {
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
          const r = Math.random() * 16 | 0;
          const v = c === 'x' ? r : ((r & 0x3) | 0x8);
          return v.toString(16);
        });
      };
      const newConversationId = generateUUID();
      setActiveConversationId(newConversationId);
      setChatHistory([]);
      setStreamingText('');
      setAgentTurnSteps([]);
      appliedExtractionJobFinishIdsRef.current.clear();
      return { id: newConversationId };
    }
    return { id: null };
  }, []);

  const ensureActiveConversation = useCallback(async () => {
    // If we have a valid conversation ID (not a temp one), use it
    if (activeConversationId && !activeConversationId.startsWith('temp-')) {
      return activeConversationId;
    }
    // Generate a UUID for the new conversation - it will be created when first message is sent
    // Using a simple UUID v4 generator (for client-side use)
    const generateUUID = () => {
      return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        const r = Math.random() * 16 | 0;
        const v = c === 'x' ? r : ((r & 0x3) | 0x8);
        return v.toString(16);
      });
    };
    const newConversationId = generateUUID();
    setActiveConversationId(newConversationId);
    setChatHistory([]);
    setStreamingText('');
    setAgentTurnSteps([]);
    appliedExtractionJobFinishIdsRef.current.clear();
    return newConversationId;
  }, [activeConversationId]);

  // Function to generate dynamic loading messages based on context
  const getLoadingMessages = useCallback(
    (userMessage, hasFile) =>
      getLoadingMessagesImpl(
        userMessage,
        hasFile,
      ),
    [],
  );

  // Function to start dynamic loading message rotation
  const startLoadingMessages = useCallback((userMessage, hasFile) => {
    const messages = getLoadingMessages(userMessage, hasFile);
    let currentIndex = 0;
    
    // Set initial message
    setLoadingMessage(messages[0]);
    
    // Clear any existing interval
    if (loadingMessageIntervalRef.current) {
      clearInterval(loadingMessageIntervalRef.current);
    }
    
    // Rotate messages every 2-3 seconds to show progress
    loadingMessageIntervalRef.current = setInterval(() => {
      currentIndex = (currentIndex + 1) % messages.length;
      setLoadingMessage(messages[currentIndex]);
    }, 2500); // Change message every 2.5 seconds
  }, [getLoadingMessages]);

  // Function to stop loading message rotation
  const stopLoadingMessages = useCallback(() => {
    if (loadingMessageIntervalRef.current) {
      clearInterval(loadingMessageIntervalRef.current);
      loadingMessageIntervalRef.current = null;
    }
    setLoadingMessage('thinking');
  }, []);

  /** Inline "Map to …" from data preview — not architect stream; use map-only loader. */
  const beginInlineMappingLoader = useCallback((message) => {
    setAgentTurnSteps([]);
    setOperationPreview(createInlineMappingOperationPreview(message));
    setLoadingMessage(message);
    setIsLoading(true);
    if (loadingMessageIntervalRef.current) {
      clearInterval(loadingMessageIntervalRef.current);
      loadingMessageIntervalRef.current = null;
    }
  }, []);

  const endInlineMappingLoader = useCallback(() => {
    stopLoadingMessages();
    setIsLoading(false);
    setOperationPreview(createEmptyOperationPreview());
    setAgentTurnSteps([]);
  }, [stopLoadingMessages]);

  const persistMessage = useCallback(async (conversationId, role, content, metadata = null) => {
    if (!conversationId) return null;
    
    // Backend will create the conversation if it doesn't exist (when first message is sent)
    try {
      const response = await api.post(`/chat/conversations/${conversationId}/messages`, {
        role,
        content,
        metadata,
      });
      const updatedConversation = response.data?.conversation;
      const createdMessage = response.data?.message;
      
      if (updatedConversation) {
        // Update active conversation ID to the actual ID from backend (in case it was different)
        setActiveConversationId(updatedConversation.id);
        upsertConversation(updatedConversation);
        // Pass updated conversation to sidebar to avoid unnecessary refetch
        window.dispatchEvent(new CustomEvent('chat-list-updated', { 
          detail: { conversation: updatedConversation } 
        }));
      }
      return createdMessage || null;
    } catch (error) {
      console.error('Failed to persist chat message:', error);
      return null;
    }
  }, [upsertConversation]);

  const updateMessageMetadata = useCallback(async (conversationId, messageId, metadata) => {
    if (!conversationId || !messageId) return;
    try {
      await api.put(`/chat/conversations/${conversationId}/messages/${messageId}/metadata`, {
        metadata,
      });
    } catch (error) {
      console.error('Failed to update message metadata:', error);
      throw error;
    }
  }, []);

  const handlePreviewDestinationPick = useCallback(
    (jobId, destType, destName, destTarget) =>
      handlePreviewDestinationPickImpl(
        {
          activeConversationId,
          chatHistoryRef,
          inlineDataPreviewsRef,
          persistMessage,
          setChatHistory,
          setInlineDataPreviews,
          setIsLoading,
          setLoadingMessage,
          setMappingPanels,
          stopLoadingMessages,
          updateMessageMetadata,
        },
        jobId,
        destType,
        destName,
        destTarget,
      ),
    [
      activeConversationId,
      updateMessageMetadata,
      persistMessage,
      setLoadingMessage,
      setIsLoading,
      stopLoadingMessages,
      setMappingPanels,
      setChatHistory,
    ],
  );


  const loadConversationMessages = useCallback(
    (conversationId) =>
      loadConversationMessagesImpl(
        {
          activeConversationId,
          activeConversationIdRef,
          appliedExtractionJobFinishIdsRef,
          chatHistoryLength: chatHistory.length,
          conversationLoadGenerationRef,
          isConversationLoading,
          mapServerMessageToEntry,
          plannerPendingRef,
          setAgentTurnSteps,
          setChatHistory,
          setConversationError,
          setIsConversationLoading,
          setMigrationStatuses,
          setStreamingText,
          updateMessageMetadata,
        },
        conversationId,
      ),
    [
    mapServerMessageToEntry,
    activeConversationId,
    chatHistory.length,
    isConversationLoading,
    updateMessageMetadata,
  ],
  );

  // Use paginated API for scalable loading (avoids full payload for large datasets)
  const PAGINATED_PAGE_SIZE = 500;

  // Fetch full extraction data when user expands preview (lazy loading)
  // Uses paginated API for scalable loading; falls back to full API if needed
  const fetchExtractionData = useCallback(
    (jobId, entryId) =>
      fetchExtractionDataImpl(
        {
          PAGINATED_PAGE_SIZE,
          chatHistoryRef,
          inlineDataPreviews,
          setChatHistory,
          setInlineDataPreviews,
        },
        jobId,
        entryId,
      ),
    [inlineDataPreviews],
  );

  // Fetch a specific page of extraction records (for paginated preview)
  const fetchExtractionDataPage = useCallback(async (jobId, page, searchQuery = '') => {
    const pageSize = 500;
    const q = (searchQuery || '').trim();
    try {
      const params = { page, page_size: pageSize };
      if (q) params.q = q;
      const res = await api.get(`/extraction-results/${jobId}/records`, { params });
      const p = res.data;
      if (p?.status === 'success' && p.records) {
        setInlineDataPreviews((prev) => {
          const existing = prev[jobId];
          if (!existing) return prev;
          const fullTotal = existing._fullTotalCount ?? existing._totalCount ?? existing.recordCount;
          return {
            ...prev,
            [jobId]: {
              ...existing,
              data: p.records,
              _fullTotalCount: fullTotal,
              _searchActive: !!q,
              _searchMatchCount: q ? p.total_count : undefined,
              _totalCount: q ? p.total_count : fullTotal,
            },
          };
        });
        return p.records;
      }
    } catch (err) {
      if (err.response?.status === 410) {
        setInlineDataPreviews((prev) => {
          const existing = prev[jobId];
          if (!existing) return prev;
          return { ...prev, [jobId]: { ...existing, _downloadDisabled: true } };
        });
      }
      console.error('Failed to fetch extraction page:', err);
      throw err;
    }
    return null;
  }, []);

  /** Full extraction CSV via server (all rows); used when preview shows >500 records or paginated large jobs. */
  const downloadExtractionCsvFull = useCallback(async (jobId, documentType) => {
    const safeBase = (documentType || 'extracted-data')
      .replace(/[^a-z0-9-_]+/gi, '-')
      .replace(/^-|-$/g, '')
      .slice(0, 48) || 'extracted-data';
    const res = await api.get(`/extraction-results/${jobId}/export.csv`, {
      responseType: 'blob',
    });
    const blob = res.data;
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${safeBase}-${jobId}.csv`;
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, []);

  /**
   * Apply extraction completion from SSE (extraction_job_finished) or server merge when SSE drops.
   */
  const applyTaskJobStatusToUi = useCallback(
    (taskData, sourceType, conversationId) =>
      applyTaskJobStatusToUiImpl(
        {
          PAGINATED_PAGE_SIZE,
          agentTurnStepsRef,
          chatHistoryRef,
          inlineDataPreviewsRef,
          operationPreviewRef,
          persistMessage,
          setAgentTurnSteps,
          setChatHistory,
          setInlineDataPreviews,
          setIsLoading,
          setMappingPanels,
          setOperationPreview,
          stopLoadingMessages,
          updateMessageMetadata,
        },
        taskData,
        sourceType,
        conversationId,
      ),
    [persistMessage, stopLoadingMessages, updateMessageMetadata],
  );

  const resolvePreviewSourceType = (rawSt, hint) => {
    if (hint) return hint;
    if (rawSt === 'google_sheets') return 'gsheets';
    if (rawSt === 'sql') return 'sql';
    if (rawSt === 'salesforce') return 'salesforce';
    return 'file';
  };

  /**
   * When SSE drops after extraction already completed, merge preview from extraction-results APIs.
   */
  const recoverCompletedExtractionsFromActiveJobs = useCallback(
    async (conversationId, streamState) => {
      const jobIds = new Set(activeJobIdsRef.current);
      if (streamState?.pendingHeldExtraction?.jobId) {
        jobIds.add(streamState.pendingHeldExtraction.jobId);
      }
      let recovered = false;
      for (const jid of jobIds) {
        if (!jid || appliedExtractionJobFinishIdsRef.current.has(jid)) continue;
        const hint =
          streamState?.pendingHeldExtraction?.jobId === jid
            ? streamState.pendingHeldExtraction.previewSource
            : null;
        const ok = await syncExtractionPreviewFromServer({
          jobId: jid,
          conversationId,
          sourceTypeHint: hint,
          applyTaskJobStatusToUi,
          resolvePreviewSourceType,
          maxAttempts: 4,
        });
        if (ok) {
          appliedExtractionJobFinishIdsRef.current.add(jid);
          recovered = true;
        }
      }
      return recovered;
    },
    [applyTaskJobStatusToUi],
  );

  /** Fallback when held-open SSE ends without extraction_job_finished (rare). */
  const pollJobForInlinePreview = useCallback(
    async (jobId, conversationId, sourceTypeHint = null) => {
      if (pollingJobs.has(jobId)) return;
      setPollingJobs((prev) => new Set(prev).add(jobId));
      try {
        const ok = await syncExtractionPreviewFromServer({
          jobId,
          conversationId,
          sourceTypeHint,
          applyTaskJobStatusToUi,
          resolvePreviewSourceType,
        });
        if (ok) {
          appliedExtractionJobFinishIdsRef.current.add(jobId);
          const sourceType = resolvePreviewSourceType(null, sourceTypeHint);
          if (sourceType === 'file') {
            setUserMessage('');
            setSelectedFile(null);
            if (fileInputRef.current) fileInputRef.current.value = '';
          }
        }
      } finally {
        setPollingJobs((prev) => {
          const next = new Set(prev);
          next.delete(jobId);
          return next;
        });
      }
    },
    [applyTaskJobStatusToUi, pollingJobs],
  );

  const handleAgentMapFieldsReady = useCallback(
    (payload) =>
      handleAgentMapFieldsReadyImpl(
        {
          activeConversationId,
          chatHistoryRef,
          inlineDataPreviewsRef,
          mappingPanelsRef,
          persistMessage,
          setAgentTurnSteps,
          setChatHistory,
          setIsLoading,
          setMappingPanels,
          setOperationPreview,
          stopLoadingMessages,
        },
        payload,
      ),
    [
      activeConversationId,
      persistMessage,
      stopLoadingMessages,
    ],
  );

  const handlePlanLimitExceededForMigration = useCallback(
    async (errorData) => {
      const details = errorData.details || {};
      const planType = details.plan_type || 'Unknown';
      const balance = details.credit_balance ?? details.remaining_credits ?? 0;
      const canExtract = details.can_extract ?? details.remaining_records ?? 0;
      const requestedRecords = details.requested_records || 0;
      const planLimitText =
        `📊 **Credit Limit Reached**\n\n` +
        `Your current plan (${planType}) is limited by your credit balance.\n\n` +
        `📈 **Summary:**\n` +
        `• Credit balance: **${Number(balance).toLocaleString()}**\n` +
        `• Records you can extract: **${Number(canExtract).toLocaleString()}**\n` +
        (requestedRecords > 0
          ? `• Requested: **${Number(requestedRecords).toLocaleString()}**\n`
          : '') +
        `\n💡 **Solution:** Add credits or upgrade your plan to extract more.`;
      setChatHistory((prev) => [
        ...prev,
        {
          id: `plan-limit-${Date.now()}`,
          type: 'system',
          text: planLimitText,
          timestamp: Date.now(),
          showUpgradeButton: true,
          metadata: {
            event_type: 'plan_limit_exceeded',
            planInfo: {
              plan_type: planType,
              credit_balance: balance,
              can_extract: canExtract,
              requested_records: requestedRecords,
            },
          },
        },
      ]);
      if (activeConversationId) {
        await persistMessage(activeConversationId, 'assistant', planLimitText, {
          planInfo: {
            plan_type: planType,
            credit_balance: balance,
            can_extract: canExtract,
            requested_records: requestedRecords,
          },
        });
      }
      stopLoadingMessages();
      setIsLoading(false);
    },
    [activeConversationId, persistMessage, stopLoadingMessages],
  );

  const migrationStreamHandlers = useMemo(
    () =>
      createMigrationStreamHandlers({
        setChatHistory,
        setMigrationStatuses,
        stopLoadingMessages,
        setIsLoading,
        setAgentTurnSteps,
        setOperationPreview,
        activeJobIdsRef,
        activeConversationId,
        plannerPendingRef,
        updateMessageMetadata,
        clearMigrationStatusPropsCache: () => migrationStatusPropsCache.clear(),
        setRecoveryActivity,
        setLoadingMessage,
      }),
    [
      stopLoadingMessages,
      activeConversationId,
      updateMessageMetadata,
    ],
  );

  const migrationExecutionDeps = useMemo(
    () => ({
      activeConversationId,
      persistMessage,
      setChatHistory,
      setMigrationStatuses,
      stopLoadingMessages,
      setIsLoading,
      setLoadingMessage,
      setRecoveryActivity,
      activeJobIdsRef,
      plannerPendingRef,
      updateMessageMetadata,
      clearMigrationStatusPropsCache: () => migrationStatusPropsCache.clear(),
      formatFastApiDetail,
      onPlanLimitExceeded: handlePlanLimitExceededForMigration,
      onMigrationSanitizeWarning: (message) => {
        setChatHistory((prev) => [
          ...prev,
          {
            id: `migration-sanitize-${Date.now()}`,
            type: 'system',
            text: `⚠️ ${message}`,
            timestamp: Date.now(),
          },
        ]);
      },
      get chatHistory() {
        return chatHistoryRef.current;
      },
      get inlineDataPreviews() {
        return inlineDataPreviewsRef.current;
      },
      ...migrationStreamHandlers,
    }),
    [
      activeConversationId,
      persistMessage,
      stopLoadingMessages,
      handlePlanLimitExceededForMigration,
      migrationStreamHandlers,
      updateMessageMetadata,
    ],
  );

  const handleAgentMigrateDataPrompt = useCallback(
    (payload) =>
      handleAgentMigrateDataPromptImpl(
        {
          activeConversationId,
          chatHistoryRef,
          inlineDataPreviewsRef,
          mappingContextPersistTimersRef,
          mappingPanelsRef,
          migrationConfirmJobRef,
          migrationExecutionDeps,
          setChatHistory,
          setIsLoading,
          startLoadingMessages,
          stopLoadingMessages,
          updateMessageMetadata,
        },
        payload,
      ),
    [
      activeConversationId,
      migrationExecutionDeps,
      stopLoadingMessages,
      startLoadingMessages,
      updateMessageMetadata,
    ],
  );

  const plannerStreamHandlers = useMemo(
    () =>
      createPlannerStreamHandlers({
        plannerPendingRef,
        setChatHistory,
        setIsLoading,
        stopLoadingMessages,
        setAgentTurnSteps,
        setOperationPreview,
        setPlanningActivity,
        persistMessage,
        updateMessageMetadata,
      }),
    [persistMessage, stopLoadingMessages, updateMessageMetadata],
  );

  const { runArchitectStream } = useArchitectStream({
    cancelledRef,
    activeJobIdsRef,
    appliedExtractionJobFinishIdsRef,
    loadingMessageIntervalRef,
    setStreamingText,
    setAgentTurnSteps,
    setOperationPreview,
    setPlanningActivity,
    setRecoveryActivity,
    setLoadingMessage,
    setIsLoading,
    stopLoadingMessages,
    onMapFieldsReady: handleAgentMapFieldsReady,
    onMigrateDataPrompt: handleAgentMigrateDataPrompt,
    ...plannerStreamHandlers,
    ...migrationStreamHandlers,
    applyTaskJobStatusToUi,
    recoverCompletedExtractionsFromActiveJobs,
    pollJobForInlinePreview,
    setChatHistory,
    setMigrationStatuses,
    setInlineDataPreviews,
    addMessage,
    persistMessage,
    ...createArchitectStreamResultHandlers({
      addMessage,
      migrationStatusesRef,
      persistMessage,
      plannerPendingRef,
      setAgentTurnSteps,
      setChatHistory,
      setInlineDataPreviews,
      setIsLoading,
      setMigrationStatuses,
      setOperationPreview,
      stopLoadingMessages,
      updateMessageMetadata,
    }),
  });

  const {
    handlePlannerPlanApprove,
    handlePlannerPlanDismiss,
    handlePlannerSchemaDeployApprove,
    handlePlannerRecoveryApprove,
    handlePlannerRecoveryDismiss,
    handlePlannerClarificationSubmit,
  } = usePlannerResume({
    activeConversationId,
    chatHistoryRef,
    plannerPendingRef,
    cancelledRef,
    abortControllerRef,
    setChatHistory,
    setIsLoading,
    setAgentTurnSteps,
    setOperationPreview,
    startLoadingMessages,
    stopLoadingMessages,
    runArchitectStream,
    addMessage,
    planAutoRunRef,
    apiClient: api,
    updateMessageMetadata,
    persistMessage,
  });

  const fetchMappingData = useCallback(
    (mappingId, entryId, metadata) =>
      fetchMappingDataImpl(
        {
          activeConversationId,
          mappingPanels,
          setChatHistory,
          setMappingPanels,
        },
        mappingId,
        entryId,
        metadata,
      ),
    [mappingPanels, activeConversationId],
  );

  // New Flow from Sidebar/TopBar passes router state when navigating from non-chat routes.
  useEffect(() => {
    if (location.state?.startNewChat) {
      startNewChatFromRouteRef.current = true;
      navigate('/chat', { replace: true, state: {} });
    }
  }, [location.state?.startNewChat, navigate]);

  useEffect(() => {
    // Prevent re-initialization if already initialized
    if (conversationsInitializedRef.current) {
      return;
    }

    let isMounted = true;
    let timeoutId = null;

    const initializeConversations = async (conversationsList = null) => {
      // Prevent multiple initializations
      if (conversationsInitializedRef.current) {
        return;
      }

      try {
        // If conversations are provided (from Sidebar), use them; otherwise wait a bit for Sidebar to load
        let fetchedConversations = conversationsList;
        
        if (!fetchedConversations) {
          // Wait a short time for Sidebar to load conversations
          await new Promise(resolve => setTimeout(resolve, 100));
          // Check if Sidebar has already loaded (via event)
          // If not, we'll wait for the event
          return;
        }
        
        if (!isMounted || conversationsInitializedRef.current) return;
        
        // Mark as initialized before setting state to prevent race conditions
        conversationsInitializedRef.current = true;
        setConversations(fetchedConversations);
        setIsConversationsLoading(false);

        if (takeStartNewChatIntent(startNewChatFromRouteRef)) {
          await handleStartNewConversation(true);
          return;
        }

        if (fetchedConversations.length > 0) {
          const initialConversation = fetchedConversations[0];
          setActiveConversationId(initialConversation.id);
          await loadConversationMessages(initialConversation.id);
        } else {
          // No conversations exist - create new conversation (lazy creation)
          // Don't load messages since conversation doesn't exist in DB yet
          // It will be created when first message is sent
          await handleStartNewConversation(true);
        }
      } catch (error) {
        if (isMounted) {
          console.error('Failed to initialize conversations:', error);
          setConversationError('Unable to load your conversations. Please try again later.');
          setConversations([]);
          setIsConversationsLoading(false);
          // Mark as initialized even on error to prevent retry loops
          conversationsInitializedRef.current = true;
        }
      }
    };

    // Listen for conversations loaded from Sidebar
    const handleConversationsLoaded = (event) => {
      const conversationsList = event.detail?.conversations;
      if (conversationsList && isMounted && !conversationsInitializedRef.current) {
        clearTimeout(timeoutId);
        initializeConversations(conversationsList);
      }
    };

    window.addEventListener('conversations-loaded', handleConversationsLoaded);

    // Fallback: if Sidebar doesn't dispatch within 500ms, fetch conversations ourselves
    // (This handles edge cases where Sidebar might not be present or event is missed)
    timeoutId = setTimeout(async () => {
      if (isMounted && !conversationsInitializedRef.current && isConversationsLoading) {
        try {
          const response = await api.get('/chat/conversations');
          if (isMounted && !conversationsInitializedRef.current) {
            const fetchedConversations = response.data?.conversations || [];
            await initializeConversations(fetchedConversations);
          }
        } catch (error) {
          if (isMounted) {
            console.error('Failed to load conversations (fallback):', error);
            setConversationError('Unable to load your conversations. Please try again later.');
            setConversations([]);
            setIsConversationsLoading(false);
            // Mark as initialized even on error to prevent retry loops
            conversationsInitializedRef.current = true;
          }
        }
      }
    }, 500);

    return () => {
      isMounted = false;
      clearTimeout(timeoutId);
      window.removeEventListener('conversations-loaded', handleConversationsLoaded);
    };
  }, [handleStartNewConversation, loadConversationMessages, isConversationsLoading]);

  useEffect(() => {
    const handleSidebarSelection = (event) => {
      const conversationId = event.detail?.conversationId;
      if (conversationId && conversationId !== activeConversationId) {
        // Only load if it's a different conversation
        setActiveConversationId(conversationId);
        loadConversationMessages(conversationId);
      }
    };

    window.addEventListener('select-conversation-from-sidebar', handleSidebarSelection);
    return () => {
      window.removeEventListener('select-conversation-from-sidebar', handleSidebarSelection);
    };
  }, [loadConversationMessages, activeConversationId]);

  useEffect(() => {
    const handleExternalNewChat = async () => {
      // Create new conversation (lazy creation)
      // Don't load messages since conversation doesn't exist in DB yet
      // It will be created when first message is sent
      await handleStartNewConversation(true);
    };

    window.addEventListener('start-new-chat', handleExternalNewChat);
    return () => {
      window.removeEventListener('start-new-chat', handleExternalNewChat);
    };
  }, [handleStartNewConversation]);

  useEffect(() => {
    const handleConversationDeleted = async (event) => {
      const deletedId = event.detail?.conversationId;
      if (!deletedId) return;
      let fallbackId = null;

      setConversations((prev) => {
        const updated = prev.filter((conversation) => conversation.id !== deletedId);
        fallbackId = updated.length > 0 ? updated[0].id : null;
        return updated;
      });

      if (activeConversationId === deletedId) {
        if (fallbackId) {
          setActiveConversationId(fallbackId);
          await loadConversationMessages(fallbackId);
        } else {
          const newConversation = await handleStartNewConversation(true);
          if (newConversation) {
            await loadConversationMessages(newConversation.id);
          } else {
            setChatHistory([]);
            setStreamingText('');
            setAgentTurnSteps([]);
            appliedExtractionJobFinishIdsRef.current.clear();
          }
        }
      }
    };

    window.addEventListener('conversation-deleted', handleConversationDeleted);
    return () => {
      window.removeEventListener('conversation-deleted', handleConversationDeleted);
    };
  }, [activeConversationId, handleStartNewConversation, loadConversationMessages]);

  // Cleanup loading message interval on unmount
  useEffect(() => {
    return () => {
      if (loadingMessageIntervalRef.current) {
        clearInterval(loadingMessageIntervalRef.current);
      }
    };
  }, []);

  const handleStopRequest = useCallback(() => {
    cancelledRef.current = true;
    const jobIds = [...activeJobIdsRef.current];
    activeJobIdsRef.current = new Set();
    if (jobIds.length) {
      cancelJobs(jobIds);
    }
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    activePollIntervalsRef.current.forEach((id) => clearInterval(id));
    activePollIntervalsRef.current.clear();
    if (loadingMessageIntervalRef.current) {
      clearInterval(loadingMessageIntervalRef.current);
      loadingMessageIntervalRef.current = null;
    }
    setPollingJobs(new Set());
    stopLoadingMessages();
    setIsLoading(false);
    setStreamingText('');
    setAgentTurnSteps([]);
    appliedExtractionJobFinishIdsRef.current.clear();
  }, [stopLoadingMessages]);

  const callArchianAIArchitectAgent = async (prompt, conversationId, signal, attachedFile = null) => {
    try {
      return await runArchitectStream(prompt, conversationId, signal, {
        planAutoRun: planAutoRunRef.current,
        attachedFile,
      });
    } catch (err) {
      if (err.name === 'AbortError') return null;
      console.error("Archian AI Architect call failed:", err);
      return "❌ Failed to get a response from Archian AI.";
    }
  };


  const handleSend = () =>
    sendChatMessage({
      abortControllerRef,
      activeJobIdsRef,
      addMessage,
      callArchianAIArchitectAgent,
      cancelledRef,
      ensureActiveConversation,
      fileInputRef,
      isLoading,
      pendingReExtract,
      persistMessage,
      selectedFile,
      setAgentTurnSteps,
      setChatHistory,
      setIsLoading,
      setLoadingMessage,
      setPendingReExtract,
      setSelectedFile,
      setStreamingText,
      setUserMessage,
      startLoadingMessages,
      stopLoadingMessages,
      textareaRef,
      userMessage,
    });

  const chatHistoryEntryProps = {
    abortControllerRef,
    activeConversationId,
    activePlannerEntryId,
    beginInlineMappingLoader,
    chatHistory,
    chatHistoryRef,
    downloadExtractionCsvFull,
    endInlineMappingLoader,
    fetchExtractionData,
    fetchExtractionDataPage,
    fetchMappingData,
    handlePlannerClarificationSubmit,
    handlePlannerPlanApprove,
    handlePlannerPlanDismiss,
    handlePlannerRecoveryApprove,
    handlePlannerRecoveryDismiss,
    handlePlannerSchemaDeployApprove,
    handlePreviewDestinationPick,
    inlineDataPreviews,
    inlineDataPreviewsRef,
    isLoading,
    loadingMessageIntervalRef,
    mapButtonRef,
    mappingContextPersistTimersRef,
    mappingLearningFinalizeTimersRef,
    mappingPanels,
    migrationExecutionDeps,
    migrationStatuses,
    persistMessage,
    plannerPendingRef,
    processingPreferenceUpdates,
    recoveryActivity,
    setChatHistory,
    setInlineDataPreviews,
    setIsLoading,
    setLoadingMessage,
    setMappingPanels,
    setPendingReExtract,
    startMigrationButtonRef,
    stopLoadingMessages,
    textareaRef,
    updateMessageMetadata,
  };

  return (
    <div className="chat-uploader-container">
      {/* Sync Status Notification Bar - Fixed on right */}
      {syncBanners.length > 0 && (
        <SyncNotificationBar syncBanners={syncBanners} setSyncBanners={setSyncBanners} />
      )}

      <div className={`content-area ${isMobile ? 'mobile' : ''}`}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
      >

        {isConversationLoading && (
          <div className="conversation-loading">Loading conversation...</div>
        )}

        {chatHistory.length === 0 && !isLoading && !isConversationLoading && !isConversationsLoading && (
          <ChatWelcomeSection
            isMobile={isMobile}
            exampleIndex={exampleIndex}
            hasSalesforceConnection={hasSalesforceConnection}
          />
        )}

        {chatHistory.map((entry, index) => (
          <ChatHistoryEntry
            key={entry.id ? `${entry.id}-${index}` : `chat-entry-${index}`}
            entry={entry}
            index={index}
            {...chatHistoryEntryProps}
          />
        ))}


        <ChatStreamingStatus
          isLoading={isLoading}
          loadingMessage={loadingMessage}
          streamingText={streamingText}
          operationPreview={operationPreview}
          agentTurnSteps={agentTurnSteps}
          planningActivity={planningActivity}
          recoveryActivity={recoveryActivity}
          migrationStatuses={migrationStatuses}
        />

        <div ref={scrollRef} />
      </div>

      <div 
        className={`chat-input-container ${isMobile ? 'mobile' : ''} ${dragOver ? 'drag-over' : ''} ${sidebarCollapsed ? 'sidebar-collapsed' : 'sidebar-expanded'}`}
      >
        <ChatInputBar
          value={userMessage}
          onChange={setUserMessage}
          onSend={handleSend}
          onStopRequest={handleStopRequest}
          isLoading={isLoading}
          selectedFile={selectedFile}
          onFileClear={() => {
            setSelectedFile(null);
            if (fileInputRef.current) fileInputRef.current.value = '';
          }}
          fileInputRef={fileInputRef}
          onFileChange={handleFileChange}
          textareaRef={textareaRef}
          placeholder="Ask to build a data pipeline or run an extraction across your connected applications"
          isMobile={isMobile}
          planAutoRun={planAutoRun}
          onPlanAutoRunChange={setPlanAutoRun}
        />
      </div>

      <style>{`
        .blinking-cursor {
          display: inline-block;
          width: 1px;
          background-color: #000;
          margin-left: 2px;
          animation: blink 1s step-start infinite;
        }

        @keyframes blink {
          50% { opacity: 0; }
        }
      `}</style>
    </div>
  );
}