import React, { useState, useEffect } from 'react';
import { FiEdit2, FiX } from 'react-icons/fi';
import './RenameFlowModal.css';

const RenameFlowModal = ({ isOpen, onClose, initialName, onSave, isLoading = false }) => {
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
    onSave(trimmed);
  };

  return (
    <div className="rename-flow-modal-overlay" onClick={onClose} role="presentation">
      <div className="rename-flow-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-labelledby="rename-flow-title">
        <div className="rename-flow-modal-header">
          <div className="rename-flow-modal-icon" aria-hidden>
            <FiEdit2 />
          </div>
          <h2 id="rename-flow-title">Rename flow</h2>
          <button type="button" className="rename-flow-modal-close" onClick={onClose} aria-label="Close">
            <FiX />
          </button>
        </div>

        <form className="rename-flow-modal-content" onSubmit={handleSubmit}>
          <label htmlFor="rename-flow-input" className="rename-flow-label">
            Flow name
          </label>
          <input
            id="rename-flow-input"
            type="text"
            className="rename-flow-input"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            maxLength={255}
            autoFocus
            disabled={isLoading}
            placeholder="Enter flow name"
          />

          <div className="rename-flow-actions">
            <button type="button" className="rename-flow-cancel" onClick={onClose} disabled={isLoading}>
              Cancel
            </button>
            <button type="submit" className="rename-flow-save" disabled={isLoading || !value.trim()}>
              {isLoading ? 'Saving…' : 'Save'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default RenameFlowModal;
