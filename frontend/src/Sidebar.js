import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import {
  FiSettings,
  FiLogOut,
  FiMessageCircle,
  FiDatabase,
  FiShare2,
  FiChevronLeft,
  FiBarChart2,
  FiCreditCard,
  FiPlus,
  FiChevronDown,
  FiChevronUp,
  FiMoreHorizontal,
  FiTrash2,
} from 'react-icons/fi';
import './Sidebar.css';
import api from './api';
import { useFeatures } from './hooks/useFeatures';
import { clearAuthTokens } from './utils/authStorage';

const Sidebar = () => {
  const navigate = useNavigate();
  const { billing } = useFeatures();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const [showChats, setShowChats] = useState(true);
  const [conversations, setConversations] = useState([]);
  const [isLoadingConversations, setIsLoadingConversations] = useState(true);
  const [conversationsError, setConversationsError] = useState(null);
  const [menuConversationId, setMenuConversationId] = useState(null);
  const [activeConversationId, setActiveConversationId] = useState(null);

  // Check if device is mobile on component mount
  useEffect(() => {
    const checkMobile = () => {
      const isMobile = window.innerWidth <= 768; // Mobile breakpoint
      setCollapsed(isMobile);
    };

    // Check on mount
    checkMobile();

    // Add resize listener
    window.addEventListener('resize', checkMobile);

    // Cleanup
    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  const handleLogout = async () => {
    await clearAuthTokens();
    navigate('/login');
  };

  const fetchConversations = useCallback(async () => {
    try {
      setIsLoadingConversations(true);
      const response = await api.get('/chat/conversations');
      const data = response.data || {};
      const conversationsList = data.conversations || [];
      setConversations(conversationsList);
      setConversationsError(null);
      
      // Dispatch conversations to ChatUploader to avoid duplicate fetch
      window.dispatchEvent(new CustomEvent('conversations-loaded', {
        detail: { conversations: conversationsList }
      }));
    } catch (error) {
      console.error('Sidebar conversations load failed:', error);
      setConversationsError('Unable to load chats');
    } finally {
      setIsLoadingConversations(false);
    }
  }, []);

  useEffect(() => {
    fetchConversations();

    const syncListener = (event) => {
      const updatedConversation = event.detail?.conversation;
      if (updatedConversation) {
        // Update sidebar state directly with the updated conversation
        // This avoids unnecessary database refetch since we already have the updated data
        setConversations((prev) => {
          // Remove the conversation if it exists (by ID)
          const filtered = prev.filter((c) => c.id !== updatedConversation.id);
          // Add updated conversation at the top (most recent)
          return [updatedConversation, ...filtered];
        });
      } else {
        // Fallback: refetch if no conversation data provided (for backwards compatibility)
        fetchConversations();
      }
    };
    window.addEventListener('chat-list-updated', syncListener);

    // Listen for active conversation changes from ChatUploader
    const activeConversationListener = (event) => {
      setActiveConversationId(event.detail?.conversationId ?? null);
    };
    window.addEventListener('active-conversation-changed', activeConversationListener);

    return () => {
      window.removeEventListener('chat-list-updated', syncListener);
      window.removeEventListener('active-conversation-changed', activeConversationListener);
    };
  }, [fetchConversations]);

  // Sync active conversation when navigation passes conversationId in location state
  useEffect(() => {
    const id = location.state?.conversationId;
    if (id) {
      setActiveConversationId(id);
    }
  }, [location.state?.conversationId]);

  useEffect(() => {
    const handleGlobalClick = () => setMenuConversationId(null);
    window.addEventListener('click', handleGlobalClick);
    return () => window.removeEventListener('click', handleGlobalClick);
  }, []);

  const handleKeyActivate = (event, callback) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      callback();
    }
  };

  const handleNewChatClick = () => {
    setActiveConversationId(null);
    window.dispatchEvent(new CustomEvent('active-conversation-changed', {
      detail: { conversationId: null },
    }));
    if (location.pathname === '/chat') {
      window.dispatchEvent(new Event('start-new-chat'));
    } else {
      navigate('/chat', { state: { startNewChat: true } });
    }
  };

  const handleMenuToggle = (event, conversationId) => {
    event.preventDefault();
    event.stopPropagation();
    setMenuConversationId((prev) => (prev === conversationId ? null : conversationId));
  };

  const handleDeleteConversation = async (event, conversationId) => {
    event.preventDefault();
    event.stopPropagation();
    try {
      await api.delete(`/chat/conversations/${conversationId}`);
      // Update local state directly - no need to refetch
      setConversations((prev) => prev.filter((conversation) => conversation.id !== conversationId));
      setMenuConversationId(null);
      // Notify ChatUploader about deletion (it handles its own state)
      window.dispatchEvent(new CustomEvent('conversation-deleted', { detail: { conversationId } }));
    } catch (error) {
      console.error('Failed to delete conversation:', error);
      setConversationsError('Unable to delete chat right now.');
    }
  };

  const isActive = (path) =>
    path === '/flows'
      ? location.pathname === '/flows' || location.pathname.startsWith('/flows/')
      : location.pathname === path;

  const formatConversationTime = (timestamp) => {
    if (!timestamp) return '';
    
    try {
      const date = new Date(timestamp);
      if (isNaN(date.getTime())) return '';
      
      const now = new Date();
      const diffInMs = now - date;
      const diffInHours = Math.floor(diffInMs / (1000 * 60 * 60));
      const diffInDays = Math.floor(diffInMs / (1000 * 60 * 60 * 24));
      
      // If less than 24 hours, show hours
      if (diffInHours < 24) {
        if (diffInHours === 0) {
          const diffInMinutes = Math.floor(diffInMs / (1000 * 60));
          return diffInMinutes < 1 ? 'just now' : `${diffInMinutes}m`;
        }
        return `${diffInHours}h`;
      }
      
      // If less than 30 days, show days
      if (diffInDays < 30) {
        return `${diffInDays}d`;
      }
      
      // For older dates, show month-date format (e.g., "Nov 25")
      return date.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric'
      });
    } catch (error) {
      return '';
    }
  };

  return (
    <div
      className={`sidebar ${collapsed ? 'collapsed' : ''}`}
      style={{
        width: collapsed ? '60px' : '240px',
        transition: 'width 0.3s ease'
      }}
    >
      <div className="sidebar-header">
        <div className="brand-section" onClick={() => collapsed && setCollapsed(false)}>
          <img src="/icon.svg" className="icon" alt="archian ai"/>
          <span className="sidebar-title">{!collapsed && 'archian ai'}</span>
        </div>
        {!collapsed && (
          <button
            onClick={() => setCollapsed(!collapsed)}
            className="toggle-button"
            aria-label="Collapse Sidebar"
          >
            <FiChevronLeft size={20} />
          </button>
        )}
      </div>

      <nav className="sidebar-nav">
        <div  className="sidebar-item"
          role="button"
          tabIndex={0}
          onClick={handleNewChatClick}
          onKeyDown={(event) => handleKeyActivate(event, handleNewChatClick)}
        >
          <FiPlus className="sidebar-icon" />
          {!collapsed && <span>New Flow</span>}
        </div>
        <div
          className={`sidebar-item hidden ${isActive('/chat') ? 'active' : ''}`}
          onClick={() => navigate('/chat')}
        >
          <FiMessageCircle className="sidebar-icon" />
          {!collapsed && <span className="sidebar-label">Chat</span>}
        </div>
        <div
          className={`sidebar-item ${isActive('/flows') ? 'active' : ''}`}
          onClick={() => navigate('/flows')}
        >
          <FiShare2 className="sidebar-icon" />
          {!collapsed && <span className="sidebar-label">Flows</span>}
        </div>
        <div
          className={`sidebar-item ${isActive('/connections') ? 'active' : ''}`}
          onClick={() => navigate('/connections')}
        >
          <FiDatabase className="sidebar-icon" />
          {!collapsed && <span className="sidebar-label">Connections</span>}
        </div>
        <div
          className={`sidebar-item ${isActive('/dashboard') ? 'active' : ''}`}
          onClick={() => navigate('/dashboard')}
        >
          <FiBarChart2 className="sidebar-icon" />
          {!collapsed && <span className="sidebar-label">Dashboard</span>}
        </div>
        {billing && (
          <div
            className={`sidebar-item ${isActive('/plans') ? 'active' : ''}`}
            onClick={() => navigate('/plans')}
          >
            <FiCreditCard className="sidebar-icon" />
            {!collapsed && <span className="sidebar-label">Plans</span>}
          </div>
        )}

        {!collapsed && (
          <div className="sidebar-chats-toggle">
            <div
              className="sidebar-chats-button"
              role="button"
              tabIndex={0}
              onClick={() => setShowChats(!showChats)}
              onKeyDown={(event) => handleKeyActivate(event, () => setShowChats(!showChats))}
            >
              <span>Recents</span>
              {showChats ? <FiChevronUp /> : <FiChevronDown />}
            </div>
            {conversationsError && (
              <p className="chats-error">{conversationsError}</p>
            )}
            {showChats && (
              <div className="sidebar-chats-list">
              {isLoadingConversations ? (
                <div className="chats-loading">Loading chats...</div>
              ) : (
                <>
                  {conversations.map((conversation) => {
                    const title = conversation.title || 'Untitled chat';
                    const timestamp = formatConversationTime(conversation.updated_at || conversation.created_at);
                    return (
                      <div
                        key={conversation.id}
                        className={`sidebar-chat-chip ${activeConversationId === conversation.id ? 'active' : ''}`}
                        role="button"
                        tabIndex={0}
                        onClick={() => {
                          setMenuConversationId(null);
                          setActiveConversationId(conversation.id);
                          navigate('/chat', { state: { conversationId: conversation.id } });
                          window.dispatchEvent(new CustomEvent('select-conversation-from-sidebar', {
                            detail: { conversationId: conversation.id }
                          }));
                        }}
                        onKeyDown={(event) => handleKeyActivate(event, () => {
                          setMenuConversationId(null);
                          setActiveConversationId(conversation.id);
                          navigate('/chat', { state: { conversationId: conversation.id } });
                          window.dispatchEvent(new CustomEvent('select-conversation-from-sidebar', {
                            detail: { conversationId: conversation.id }
                          }));
                        })}
                      >
                        <div className="chat-chip-text">
                          <span className="chat-chip-title">{title}</span>
                          {timestamp && <span className="chat-chip-summary">{timestamp}</span>}
                        </div>

                        <div
                          className="chat-chip-menu-trigger"
                          aria-label="Open chat actions"
                          onClick={(event) => handleMenuToggle(event, conversation.id)}
                        >
                          <FiMoreHorizontal size={16} />
                        </div>

                        {menuConversationId === conversation.id && (
                          <div
                            className="chat-chip-menu"
                            onClick={(event) => event.stopPropagation()}
                          >
                            <button
                              className="chat-chip-menu-item"
                              onClick={(event) => handleDeleteConversation(event, conversation.id)}
                            >
                              <FiTrash2 size={14} />
                              Delete
                            </button>
                          </div>
                        )}
                      </div>
                    );
                  })}
                  {conversations.length === 0 && (
                    <div className="chats-empty-state">No chats yet.</div>
                  )}
                </>
              )}
            </div>
            )}
          </div>
        )}
      </nav>

      <div className="sidebar-footer">
        <div
          className={`sidebar-item ${isActive('/settings') ? 'active' : ''}`}
          onClick={() => navigate('/settings')}
        >
          <FiSettings className="sidebar-icon" />
          {!collapsed && <span className="sidebar-label">Settings</span>}
        </div>
        <button className="logout-button" onClick={handleLogout}>
          <FiLogOut className="sidebar-icon" />
          {!collapsed && <span className="sidebar-label">Logout</span>}
        </button>
      </div>
    </div>
  );
};

export default Sidebar;
