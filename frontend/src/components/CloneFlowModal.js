import React, { useState, useEffect } from 'react';
import { FiCopy, FiX } from 'react-icons/fi';
import './RenameFlowModal.css';

const CloneFlowModal = ({
  isOpen,
  onClose,
  initialName,
  onClone,
  isLoading = false,
  serverError = null,
  onClearServerError,
}) => {
  const [value, setValue] = useState('');

  useEffect(() => {
    if (isOpen) {
      setValue(initialName || '');
    }
  }, [isOpen, initialName]);

  if (!isOpen) return null;

  const handleSubmit = (e) => {
    e.preventDefault();
    const trimmed = value.trim();
    if (!trimmed || isLoading) return;
    onClone(trimmed);
  };

  return (
    <div className="rename-flow-modal-overlay" onClick={() => !isLoading && onClose()} role="presentation">
      <div
        className="rename-flow-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-labelledby="clone-flow-title"
      >
        <div className="rename-flow-modal-header">
          <div className="rename-flow-modal-icon" aria-hidden>
            <FiCopy />
          </div>
          <h2 id="clone-flow-title">Clone flow</h2>
          <button
            type="button"
            className="rename-flow-modal-close"
            onClick={() => !isLoading && onClose()}
            aria-label="Close"
          >
            <FiX />
          </button>
        </div>

        <form className="rename-flow-modal-content" onSubmit={handleSubmit}>
          <p className="clone-flow-hint" style={{ margin: '0 0 12px', fontSize: '0.9rem', color: '#4b5563' }}>
            Enter a unique name for the new flow. Connections, mappings, and migration rules will be copied.
            Scheduling is not copied.
          </p>
          <label htmlFor="clone-flow-input" className="rename-flow-label">
            New flow name
          </label>
          <input
            id="clone-flow-input"
            type="text"
            className="rename-flow-input"
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
              onClearServerError?.();
            }}
            maxLength={255}
            autoFocus
            disabled={isLoading}
            placeholder="Enter a unique flow name"
          />
          {serverError && (
            <p className="clone-flow-error" role="alert" style={{ margin: '8px 0 0', fontSize: '0.875rem', color: '#b91c1c' }}>
              {serverError}
            </p>
          )}

          <div className="rename-flow-actions">
            <button type="button" className="rename-flow-cancel" onClick={onClose} disabled={isLoading}>
              Cancel
            </button>
            <button type="submit" className="rename-flow-save" disabled={isLoading || !value.trim()}>
              {isLoading ? 'Cloning…' : 'Clone'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default CloneFlowModal;
