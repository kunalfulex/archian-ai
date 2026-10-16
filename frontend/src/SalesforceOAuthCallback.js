import React, { useEffect, useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import api from './api';
import { checkSession } from './utils/authStorage';

const SalesforceOAuthCallback = () => {
  const navigate = useNavigate();
  const [message, setMessage] = useState("Completing Salesforce login...");
  const [syncStatus, setSyncStatus] = useState("checking"); // checking, syncing, complete
  const hasProcessed = useRef(false); // Use ref to prevent duplicate calls
  const pollingIntervalRef = useRef(null);

  useEffect(() => {
    const queryParams = new URLSearchParams(window.location.search);
    const code = queryParams.get("code");
    const state = queryParams.get("state");

    if (!code || !state) {
      setMessage("Missing authorization code or state. \n\n Make sure you are logged out of Salesforce and try again.");
      return;
    }

    // Prevent multiple executions using ref
    if (hasProcessed.current) {
      return;
    }
    hasProcessed.current = true;

    const exchangeToken = async () => {
      try {
        const apiBase = process.env.REACT_APP_API_URL || "";
        const response = await fetch(`${apiBase}/oauth/callback`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json"
          },
          body: JSON.stringify({ code, state })
        });

        if (!response.ok) {
          let detail = 'Failed to exchange token';
          try {
            const errBody = await response.json();
            detail = errBody.detail || errBody.message || detail;
          } catch {
            // ignore parse errors
          }
          throw new Error(detail);
        }

        const data = await response.json();
        setMessage("Salesforce login successful!\nArchian AI is reading your Salesforce org. Once completed, you can start talking to your Salesforce org.");
        setSyncStatus("syncing");
        
        if (data.org_id) {
          const displayName =
            data.org_display_name || data.salesforce_username || 'Salesforce Org';
          sessionStorage.setItem('pending_sync', JSON.stringify({
            type: 'salesforce',
            connectorType: 'salesforce',
            connectorInstanceId: data.connector_id || data.org_id,
            orgId: data.org_id,
            name: displayName,
            displayName,
            trigger: data.is_reconnect ? 'resync' : 'create',
          }));
          // Start polling for sync status
          startPollingSyncStatus(data.org_id);
        }
      } catch (error) {
        console.error("OAuth callback error:", error);
        setMessage(`❌ ${error.message || 'Failed to complete Salesforce login.'}`);
        setSyncStatus("error");
      }
    };

    exchangeToken();

    // Cleanup polling on unmount
    return () => {
      if (pollingIntervalRef.current) {
        clearInterval(pollingIntervalRef.current);
      }
    };
  }, [navigate]);

  const startPollingSyncStatus = (orgId) => {
    const checkSyncStatus = async () => {
      try {
        const response = await api.get(`/api/salesforce-orgs/${orgId}/sync-status`);
        if (response.data.status === "success") {
          if (response.data.sync_complete) {
            setSyncStatus("complete");
            setMessage("Salesforce login successful!\nArchian AI has finished reading your Salesforce org. You can now start talking to your Salesforce org!");
            // IMPORTANT: Do NOT clear sessionStorage.pending_sync here.
            // ChatUploader owns the sync banner UX and will:
            // - detect sync_complete
            // - show the completion banner
            // - remove 'pending_sync' after showing the message for a few seconds.
            // Here we only stop this component's polling.
            if (pollingIntervalRef.current) {
              clearInterval(pollingIntervalRef.current);
              pollingIntervalRef.current = null;
            }
          }
        }
      } catch (error) {
        console.error("Error checking sync status:", error);
        // Continue polling even on error
      }
    };

    // Check immediately
    checkSyncStatus();
    
    // Then poll every 3 seconds
    pollingIntervalRef.current = setInterval(checkSyncStatus, 3000);
  };

  const handleStartConversation = () => {
    checkSession().then((valid) => {
      navigate(valid ? '/chat' : '/login');
    });
  };

  return (
    <div style={{ textAlign: "center", marginTop: "100px", fontFamily: "sans-serif", maxWidth: "600px", margin: "100px auto", padding: "20px" }}>
      <div style={{ marginBottom: "30px" }}>
        <img 
          src="/icon.svg" 
          alt="Archian AI" 
          style={{ 
            width: "40px", 
            height: "40px",
            animation: syncStatus === "syncing" ? "spin 2s linear infinite" : "none"
          }} 
        />
      </div>
      <h2 style={{ whiteSpace: "pre-line", marginBottom: "30px", lineHeight: "1.6" }}>{message}</h2>
      
      {syncStatus === "syncing" && (
        <div style={{ marginBottom: "30px", color: "#666", fontSize: "14px" }}>
          Reading your Salesforce data structure...
        </div>
      )}
      
      <button
        onClick={handleStartConversation}
        style={{
          padding: "12px 24px",
          fontSize: "16px",
          fontWeight: "600",
          color: "#fff",
          backgroundColor: "#007bff",
          border: "none",
          borderRadius: "6px",
          cursor: "pointer",
          transition: "background-color 0.2s"
        }}
        onMouseOver={(e) => e.target.style.backgroundColor = "#0056b3"}
        onMouseOut={(e) => e.target.style.backgroundColor = "#007bff"}
      >
        Start Your Conversation with Archian
      </button>
      
      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
};

export default SalesforceOAuthCallback;
