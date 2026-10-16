import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import api from '../api';

async function fetchSheetEntities(connectionId) {
  try {
    const { data } = await api.get(
      `/api/connectors/google-sheets/${connectionId}/assessments/entities`,
    );
    return (data.entities || []).map((ent) => ({
      ...ent,
      entity_id: ent.entity_id || ent.value || '',
    }));
  } catch {
    return [];
  }
}

function entityId(ent) {
  return ent?.entity_id || ent?.value || '';
}

function scopeSummary(total, allowlist) {
  if (!total) return 'No sheets synced';
  if (allowlist == null) return `All sheets (${total})`;
  if (allowlist.length === 0) return 'No sheets selected';
  return `${allowlist.length} of ${total} sheet${total === 1 ? '' : 's'}`;
}

export default function GoogleSheetsNodeScope({ connectionId, allowlist, onAllowlistChange }) {
  const [entities, setEntities] = useState([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);

  const load = useCallback(async () => {
    if (!connectionId) return;
    setLoading(true);
    const rows = await fetchSheetEntities(connectionId);
    setEntities(rows);
    setLoading(false);
  }, [connectionId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!open) return undefined;

    const close = () => setOpen(false);

    const onPointerDown = (event) => {
      if (rootRef.current?.contains(event.target)) return;
      close();
    };

    const onKeyDown = (event) => {
      if (event.key === 'Escape') close();
    };

    document.addEventListener('mousedown', onPointerDown, true);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown, true);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  const selected = useMemo(() => new Set(allowlist || []), [allowlist]);
  const allIds = useMemo(
    () => entities.map((e) => entityId(e)).filter(Boolean),
    [entities],
  );
  const useAll = allowlist == null;

  const toggleEntity = (entityIdValue) => {
    const next = new Set(useAll ? allIds : selected);
    if (next.has(entityIdValue)) next.delete(entityIdValue);
    else next.add(entityIdValue);
    if (next.size === 0) {
      onAllowlistChange([]);
    } else if (next.size === allIds.length) {
      onAllowlistChange(null);
    } else {
      onAllowlistChange(Array.from(next));
    }
  };

  const selectAll = () => onAllowlistChange(null);
  const clearAll = () => onAllowlistChange([]);

  return (
    <div className="eg-gsheets-scope" ref={rootRef}>
      <button
        type="button"
        className="eg-gsheets-scope-toggle"
        onMouseDown={(e) => e.stopPropagation()}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        {loading ? 'Loading sheets…' : scopeSummary(entities.length, allowlist)}
      </button>
      {open && (
        <div
          className="eg-gsheets-scope-panel"
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="eg-gsheets-scope-actions">
            <button type="button" className="eg-secondary-btn eg-gsheets-scope-btn" onClick={selectAll}>
              All sheets
            </button>
            <button type="button" className="eg-secondary-btn eg-gsheets-scope-btn" onClick={clearAll}>
              Clear
            </button>
          </div>
          {!entities.length && !loading && (
            <p className="eg-wizard-hint">Sync spreadsheets for this Google account on Connections first.</p>
          )}
          <ul className="eg-gsheets-scope-list">
            {entities.map((ent) => {
              const id = entityId(ent);
              const checked = useAll || selected.has(id);
              return (
                <li key={id}>
                  <label>
                    <input
                      type="checkbox"
                      checked={checked}
                      onMouseDown={(e) => e.stopPropagation()}
                      onClick={(e) => e.stopPropagation()}
                      onChange={(e) => {
                        e.stopPropagation();
                        toggleEntity(id);
                      }}
                    />
                    <span>{ent.label || id}</span>
                  </label>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}

export { fetchSheetEntities, scopeSummary };
