import React, { useEffect, useMemo, useRef, useState } from 'react';
import { FiChevronDown, FiChevronUp, FiSearch, FiX } from 'react-icons/fi';
import './AssessmentEntityMultiSelect.css';

/**
 * Multi-select dropdown with checkboxes and visible selected chips.
 */
export default function AssessmentEntityMultiSelect({
  options = [],
  selected = [],
  onChange,
  disabled = false,
  entityNoun = 'objects',
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const rootRef = useRef(null);
  const searchRef = useRef(null);

  const optionMap = useMemo(() => {
    const map = new Map();
    options.forEach((o) => map.set(o.value, o.label));
    return map;
  }, [options]);

  const filteredOptions = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return options;
    return options.filter(
      (o) => o.label.toLowerCase().includes(q) || o.value.toLowerCase().includes(q),
    );
  }, [options, searchQuery]);

  const selectedSet = useMemo(() => new Set(selected), [selected]);

  useEffect(() => {
    if (!isOpen) return undefined;
    const onDocClick = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) {
        setIsOpen(false);
        setSearchQuery('');
      }
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [isOpen]);

  useEffect(() => {
    if (isOpen && searchRef.current) searchRef.current.focus();
  }, [isOpen]);

  const toggleOpen = () => {
    if (disabled) return;
    setIsOpen((o) => !o);
    if (isOpen) setSearchQuery('');
  };

  const toggleValue = (value) => {
    const next = new Set(selected);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    onChange(Array.from(next));
  };

  const selectAllFiltered = () => {
    const next = new Set(selected);
    filteredOptions.forEach((o) => next.add(o.value));
    onChange(Array.from(next));
  };

  const clearAll = () => onChange([]);

  const removeOne = (value, e) => {
    e.stopPropagation();
    onChange(selected.filter((v) => v !== value));
  };

  const singular = entityNoun.endsWith('s') ? entityNoun.slice(0, -1) : entityNoun;
  const triggerLabel =
    selected.length === 0
      ? `Select ${entityNoun}…`
      : `${selected.length} ${selected.length === 1 ? singular : entityNoun} selected`;

  return (
    <div className="assessment-entity-multiselect" ref={rootRef}>
      {selected.length > 0 && (
        <div className="assessment-entity-chips" aria-label={`Selected ${entityNoun}`}>
          {selected.map((value) => (
            <span key={value} className="assessment-entity-chip">
              <span className="assessment-entity-chip-label" title={optionMap.get(value) || value}>
                {optionMap.get(value) || value}
              </span>
              {!disabled && (
                <button
                  type="button"
                  className="assessment-entity-chip-remove"
                  onClick={(e) => removeOne(value, e)}
                  aria-label={`Remove ${optionMap.get(value) || value}`}
                >
                  <FiX size={14} />
                </button>
              )}
            </span>
          ))}
        </div>
      )}

      <button
        type="button"
        className={`assessment-entity-trigger${isOpen ? ' open' : ''}${disabled ? ' disabled' : ''}`}
        onClick={toggleOpen}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={isOpen}
      >
        <span className="assessment-entity-trigger-label">{triggerLabel}</span>
        {isOpen ? <FiChevronUp aria-hidden /> : <FiChevronDown aria-hidden />}
      </button>

      {isOpen && (
        <div className="assessment-entity-dropdown" role="listbox" aria-multiselectable="true">
          <div className="assessment-entity-dropdown-search">
            <FiSearch className="assessment-entity-search-icon" aria-hidden />
            <input
              ref={searchRef}
              type="search"
              className="assessment-entity-search-input"
              placeholder={`Search ${entityNoun}…`}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>
          <div className="assessment-entity-dropdown-actions">
            <button type="button" className="assessment-entity-link-btn" onClick={selectAllFiltered}>
              Select all{searchQuery.trim() ? ' shown' : ''}
            </button>
            <span className="assessment-entity-actions-sep">·</span>
            <button type="button" className="assessment-entity-link-btn" onClick={clearAll}>
              Clear all
            </button>
          </div>
          <ul className="assessment-entity-option-list">
            {filteredOptions.length === 0 ? (
              <li className="assessment-entity-empty">No matches</li>
            ) : (
              filteredOptions.map((opt) => {
                const checked = selectedSet.has(opt.value);
                return (
                  <li key={opt.value}>
                    <label className={`assessment-entity-option${checked ? ' checked' : ''}`}>
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleValue(opt.value)}
                      />
                      <span className="assessment-entity-option-label">{opt.label}</span>
                    </label>
                  </li>
                );
              })
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
