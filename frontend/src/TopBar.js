import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import {
  FiMenu,
  FiX,
  FiMessageCircle,
  FiDatabase,
  FiShare2,
  FiBarChart2,
  FiCreditCard,
  FiSettings,
  FiLogOut,
  FiPlus,
  FiChevronDown,
  FiChevronUp,
  FiMoreHorizontal,
  FiTrash2,
} from 'react-icons/fi';
import api from './api';
import { useFeatures } from './hooks/useFeatures';
import { clearAuthTokens, getCachedUserEmail, checkSession } from './utils/authStorage';
import './TopBar.css';
/** Reuse chat list + chip styles from Sidebar (Sidebar may not mount on mobile). */
import './Sidebar.css';

const TopBar = () => {
  const navigate = useNavigate();
  const { billing } = useFeatures();
  const location = useLocation();
  const [isMobile, setIsMobile] = useState(false);
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const [userEmail, setUserEmailState] = useState(getCachedUserEmail() || '');
  const [showChats, setShowChats] = useState(true);
  const [sessionReady, setSessionReady] = useState(false);

  useEffect(() => {
    checkSession().then((valid) => {
      setSessionReady(valid);
      if (valid) {
        setUserEmailState(getCachedUserEmail() || '');
      }
    });
  }, []);
  const [conversations, setConversations] = useState([]);
  const [isLoadingConversations, setIsLoadingConversations] = useState(true);
  const [conversationsError, setConversationsError] = useState(null);
  const [menuConversationId, setMenuConversationId] = useState(null);
  const [activeConversationId, setActiveConversationId] = useState(null);

  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth <= 768);
    };

    checkMobile();
    window.addEventListener('resize', checkMobile);

    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  const fetchConversations = useCallback(async () => {
    try {
      setIsLoadingConversations(true);
      const response = await api.get('/chat/conversations');
      const data = response.data || {};
      const conversationsList = data.conversations || [];
      setConversations(conversationsList);
      setConversationsError(null);
      window.dispatchEvent(
        new CustomEvent('conversations-loaded', {
          detail: { conversations: conversationsList },
        })
      );
    } catch (error) {
      console.error('TopBar conversations load failed:', error);
      setConversationsError('Unable to load chats');
    } finally {
      setIsLoadingConversations(false);
    }
  }, []);

  useEffect(() => {
    if (!isMobile || !sessionReady) return;

    fetchConversations();

    const syncListener = (event) => {
      const updatedConversation = event.detail?.conversation;
      if (updatedConversation) {
        setConversations((prev) => {
          const filtered = prev.filter((c) => c.id !== updatedConversation.id);
          return [updatedConversation, ...filtered];
        });
      } else {
        fetchConversations();
      }
    };
    window.addEventListener('chat-list-updated', syncListener);

    return () => {
      window.removeEventListener('chat-list-updated', syncListener);
    };
  }, [isMobile, sessionReady, fetchConversations]);

  useEffect(() => {
    if (!isMobile) return;

    const activeConversationListener = (event) => {
      const conversationId = event.detail?.conversationId;
      if (conversationId) {
        setActiveConversationId(conversationId);
      }
    };
    window.addEventListener('active-conversation-changed', activeConversationListener);

    return () => {
      window.removeEventListener('active-conversation-changed', activeConversationListener);
    };
  }, [isMobile]);

  useEffect(() => {
    const id = location.state?.conversationId;
    if (id) {
      setActiveConversationId(id);
    }
  }, [location.state?.conversationId]);

  useEffect(() => {
    if (!isMobile) return;

    const handleGlobalClick = () => setMenuConversationId(null);
    window.addEventListener('click', handleGlobalClick);
    return () => window.removeEventListener('click', handleGlobalClick);
  }, [isMobile]);

  const email = userEmail;

  const handleLogout = async () => {
    await clearAuthTokens();

    window.dispatchEvent(new CustomEvent('logout'));

    navigate('/login');
  };

  const isActive = (path) =>
    path === '/flows'
      ? location.pathname === '/flows' || location.pathname.startsWith('/flows/')
      : location.pathname === path;

  const handleNavClick = (path) => {
    navigate(path);
    setIsMenuOpen(false);
  };

  const handleKeyActivate = (event, callback) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      callback();
    }
  };

  const formatConversationTime = (timestamp) => {
    if (!timestamp) return '';

    try {
      const date = new Date(timestamp);
      if (isNaN(date.getTime())) return '';

      const now = new Date();
      const diffInMs = now - date;
      const diffInHours = Math.floor(diffInMs / (1000 * 60 * 60));
      const diffInDays = Math.floor(diffInMs / (1000 * 60 * 60 * 24));

      if (diffInHours < 24) {
        if (diffInHours === 0) {
          const diffInMinutes = Math.floor(diffInMs / (1000 * 60));
          return diffInMinutes < 1 ? 'just now' : `${diffInMinutes}m`;
        }
        return `${diffInHours}h`;
      }

      if (diffInDays < 30) {
        return `${diffInDays}d`;
      }

      return date.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
      });
    } catch (error) {
      return '';
    }
  };

  const handleNewChatClick = () => {
    setIsMenuOpen(false);
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
      setConversations((prev) => prev.filter((c) => c.id !== conversationId));
      setMenuConversationId(null);
      window.dispatchEvent(new CustomEvent('conversation-deleted', { detail: { conversationId } }));
    } catch (error) {
      console.error('Failed to delete conversation:', error);
      setConversationsError('Unable to delete chat right now.');
    }
  };

  const selectConversation = (conversationId) => {
    setMenuConversationId(null);
    setActiveConversationId(conversationId);
    setIsMenuOpen(false);
    navigate('/chat', { state: { conversationId } });
    window.dispatchEvent(
      new CustomEvent('select-conversation-from-sidebar', {
        detail: { conversationId },
      })
    );
  };

  if (!sessionReady) return null;

  if (isMobile) {
    return (
      <div className="mobile-topbar">
        <div className="topbar-header">
          <div className="brand-section">
            <img src="/icon.svg" className="topbar-icon" alt="archian ai" />
            <span className="topbar-title">archian ai</span>
          </div>
          <button
            className="menu-toggle"
            onClick={() => setIsMenuOpen(!isMenuOpen)}
            aria-label={isMenuOpen ? 'Close Menu' : 'Open Menu'}
          >
            {isMenuOpen ? <FiX size={24} /> : <FiMenu size={24} />}
          </button>
        </div>

        {isMenuOpen && (
          <div className="mobile-menu">
            <div className="menu-section">
              <div
                className="menu-item menu-item-new-chat"
                role="button"
                tabIndex={0}
                onClick={handleNewChatClick}
                onKeyDown={(e) => handleKeyActivate(e, handleNewChatClick)}
              >
                <FiPlus className="menu-icon" />
                <span className="menu-label">New Flow</span>
              </div>

              <div
                className={`menu-item ${isActive('/chat') ? 'active' : ''}`}
                onClick={() => handleNavClick('/chat')}
              >
                <FiMessageCircle className="menu-icon" />
                <span className="menu-label">Chat</span>
              </div>

              <div className="mobile-menu-chats">
                <div className="sidebar-chats-toggle">
                  <div
                    className="sidebar-chats-button"
                    role="button"
                    tabIndex={0}
                    onClick={() => setShowChats(!showChats)}
                    onKeyDown={(e) => handleKeyActivate(e, () => setShowChats(!showChats))}
                  >
                    <span>Recents</span>
                    {showChats ? <FiChevronUp /> : <FiChevronDown />}
                  </div>
                  {conversationsError && <p className="chats-error">{conversationsError}</p>}
                  {showChats && (
                    <div className="sidebar-chats-list">
                      {isLoadingConversations ? (
                        <div className="chats-loading">Loading chats...</div>
                      ) : (
                        <>
                          {conversations.map((conversation) => {
                            const title = conversation.title || 'Untitled chat';
                            const timestamp = formatConversationTime(
                              conversation.updated_at || conversation.created_at
                            );
                            return (
                              <div
                                key={conversation.id}
                                className={`sidebar-chat-chip ${
                                  activeConversationId === conversation.id ? 'active' : ''
                                }`}
                                role="button"
                                tabIndex={0}
                                onClick={() => selectConversation(conversation.id)}
                                onKeyDown={(e) =>
                                  handleKeyActivate(e, () => selectConversation(conversation.id))
                                }
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
                                      type="button"
                                      className="chat-chip-menu-item"
                                      onClick={(event) =>
                                        handleDeleteConversation(event, conversation.id)
                                      }
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
              </div>

              <div
                className={`menu-item ${isActive('/flows') ? 'active' : ''}`}
                onClick={() => handleNavClick('/flows')}
              >
                <FiShare2 className="menu-icon" />
                <span className="menu-label">Flows</span>
              </div>
              <div
                className={`menu-item ${isActive('/connections') ? 'active' : ''}`}
                onClick={() => handleNavClick('/connections')}
              >
                <FiDatabase className="menu-icon" />
                <span className="menu-label">Connections</span>
              </div>
              <div
                className={`menu-item ${isActive('/dashboard') ? 'active' : ''}`}
                onClick={() => handleNavClick('/dashboard')}
              >
                <FiBarChart2 className="menu-icon" />
                <span className="menu-label">Dashboard</span>
              </div>
              {billing && (
                <div
                  className={`menu-item ${isActive('/plans') ? 'active' : ''}`}
                  onClick={() => handleNavClick('/plans')}
                >
                  <FiCreditCard className="menu-icon" />
                  <span className="menu-label">Plans</span>
                </div>
              )}
            </div>

            <div className="menu-footer">
              <div
                className={`menu-item ${isActive('/settings') ? 'active' : ''}`}
                onClick={() => handleNavClick('/settings')}
              >
                <FiSettings className="menu-icon" />
                <span className="menu-label">Settings</span>
              </div>
              <div className="menu-item logout" onClick={handleLogout}>
                <FiLogOut className="menu-icon" />
                <span className="menu-label">Logout</span>
              </div>
              <div className="user-info">
                <span className="user-email">
                  Logged in as <strong>{email}</strong>
                </span>
              </div>
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div
      style={{
        position: 'fixed',
        top: 0,
        right: 0,
        background: '#f9fafb',
        padding: '10px 20px',
        boxShadow: '0 1px 5px rgba(0,0,0,0.1)',
        display: 'flex',
        alignItems: 'center',
        gap: '10px',
        zIndex: 1000,
      }}
    >
      <span style={{ fontSize: '14px', color: '#333' }}>
        Logged in as <strong>{email}</strong>
      </span>
      <button
        onClick={handleLogout}
        style={{
          background: '#ef4444',
          color: 'white',
          borderRadius: '6px',
          padding: '6px 12px',
          border: 'none',
          cursor: 'pointer',
        }}
      >
        Logout
      </button>
    </div>
  );
};

export default TopBar;
