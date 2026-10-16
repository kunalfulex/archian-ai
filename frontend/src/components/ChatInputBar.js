import React, { useEffect, useRef, useState } from 'react';
import { FiPaperclip, FiUpload, FiArrowUp, FiChevronDown, FiChevronsRight, FiPauseCircle } from 'react-icons/fi';
import { BsSquareFill } from 'react-icons/bs';
import './ChatInputBar.css';

export default function ChatInputBar({
  value,
  onChange,
  onSend,
  onStopRequest,
  isLoading,
  selectedFile,
  onFileSelect,
  onFileClear,
  fileInputRef,
  onFileChange,
  textareaRef,
  placeholder = 'Ask to build a data pipeline or run an extraction across your connected applications',
  isMobile = false,
  planAutoRun = false,
  onPlanAutoRunChange,
}) {
  const canSend = value.trim().length > 0;
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);

  useEffect(() => {
    if (!menuOpen) return undefined;
    const onDocClick = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [menuOpen]);

  const selectMode = (autoRun) => {
    onPlanAutoRunChange?.(autoRun);
    setMenuOpen(false);
  };

  return (
    <>
      <input
        type="file"
        ref={fileInputRef}
        onChange={onFileChange}
        className="file-input-hidden"
        aria-hidden="true"
      />

      <div className="input-content-wrapper">
        {selectedFile && (
          <div className="file-selected-indicator">
            <span className="filename">
              <FiPaperclip style={{ display: 'inline', verticalAlign: 'middle', marginRight: '4px' }} />
              {selectedFile.name}
            </span>
            <span
              className="remove-btn"
              onClick={() => {
                onFileClear?.();
              }}
            >
              ×
            </span>
          </div>
        )}

        <div className={`chat-input-pill ${isMobile ? 'mobile' : ''}`}>
          <div className="textarea-wrapper">
            <textarea
              ref={textareaRef}
              id="chat-message-input"
              name="message"
              disabled={false}
              value={value ?? ''}
              onChange={(e) => onChange(e.target.value ?? '')}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  if (isLoading) return;
                  onSend?.();
                }
              }}
              placeholder={placeholder}
              className={`chat-textarea ${isMobile ? 'mobile' : ''}`}
            />
          </div>

          <div className="chat-input-auto-row">
            <button
              type="button"
              className="chat-input-upload-btn"
              onClick={() => fileInputRef?.current?.click()}
              title="Upload file"
              aria-label="Upload file"
            >
              <FiUpload size={18} />
            </button>

            <div className="chat-plan-mode-wrap" ref={menuRef}>
              <button
                type="button"
                className={`chat-plan-mode-trigger ${planAutoRun ? 'act' : 'ask'}`}
                onClick={() => setMenuOpen((o) => !o)}
                aria-haspopup="listbox"
                aria-expanded={menuOpen}
                title={planAutoRun ? 'Act without asking' : 'Ask before acting'}
              >
                {planAutoRun ? (
                  <FiChevronsRight size={16} aria-hidden />
                ) : (
                  <FiPauseCircle size={16} aria-hidden />
                )}
                <span>{planAutoRun ? 'Act' : 'Ask'}</span>
                <FiChevronDown size={14} aria-hidden className="chat-plan-mode-chevron" />
              </button>
              {menuOpen && (
                <div className="chat-plan-mode-menu" role="listbox">
                  <button
                    type="button"
                    role="option"
                    aria-selected={!planAutoRun}
                    className={`chat-plan-mode-option ${!planAutoRun ? 'selected' : ''}`}
                    onClick={() => selectMode(false)}
                  >
                    <FiPauseCircle size={18} aria-hidden />
                    <span className="chat-plan-mode-option-text">
                      <strong>Ask before acting</strong>
                      <span>Pause for plan approval before each run</span>
                    </span>
                    {!planAutoRun && <span className="chat-plan-mode-check">✓</span>}
                  </button>
                  <button
                    type="button"
                    role="option"
                    aria-selected={planAutoRun}
                    className={`chat-plan-mode-option ${planAutoRun ? 'selected' : ''}`}
                    onClick={() => selectMode(true)}
                  >
                    <FiChevronsRight size={18} aria-hidden />
                    <span className="chat-plan-mode-option-text">
                      <strong>Act without asking</strong>
                      <span>Run plans automatically without Run plan</span>
                    </span>
                    {planAutoRun && <span className="chat-plan-mode-check">✓</span>}
                  </button>
                </div>
              )}
            </div>

            <div className="chat-input-send-wrap">
              {isLoading ? (
                <button
                  onClick={onStopRequest}
                  className={`send-button stop-button ${isMobile ? 'mobile' : ''}`}
                  title="Stop"
                >
                  <BsSquareFill size={12} color="white" />
                </button>
              ) : (
                <button
                  onClick={onSend}
                  disabled={!canSend}
                  className={`send-button ${isMobile ? 'mobile' : ''}`}
                  title="Send"
                >
                  <FiArrowUp size={16} />
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
