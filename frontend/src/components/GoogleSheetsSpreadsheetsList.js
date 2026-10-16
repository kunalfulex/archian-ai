import React, { useMemo, useState } from 'react';
import { FiDatabase, FiRefreshCw, FiSearch, FiTrash2, FiX } from 'react-icons/fi';
import './GoogleSheetsSpreadsheetsList.css';

function matchesSearch(spreadsheet, query) {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const name = (spreadsheet.spreadsheet_name || '').toLowerCase();
  if (name.includes(q)) return true;
  return (spreadsheet.sheets || []).some((s) =>
    (s.sheet_name || '').toLowerCase().includes(q),
  );
}

const MAX_VISIBLE_SHEET_CHIPS = 4;

function SheetChips({ sheets }) {
  const list = sheets || [];
  if (!list.length) {
    return <span className="gs-sheet-chip gs-sheet-chip--empty">No tabs synced</span>;
  }
  const visible = list.slice(0, MAX_VISIBLE_SHEET_CHIPS);
  const extra = list.length - visible.length;
  return (
    <div className="gs-sheet-chips" title={list.map((s) => s.sheet_name).join(', ')}>
      {visible.map((sheet) => (
        <span key={sheet.id || sheet.sheet_name} className="gs-sheet-chip">
          {sheet.sheet_name}
        </span>
      ))}
      {extra > 0 && <span className="gs-sheet-chip gs-sheet-chip--more">+{extra} more</span>}
    </div>
  );
}

export default function GoogleSheetsSpreadsheetsList({
  spreadsheets,
  isLoading,
  syncingSpreadsheetId,
  onResync,
  onDelete,
}) {
  const [search, setSearch] = useState('');

  const filtered = useMemo(
    () => spreadsheets.filter((s) => matchesSearch(s, search)),
    [spreadsheets, search],
  );

  const total = spreadsheets.length;
  const totalSheets = useMemo(
    () => spreadsheets.reduce((n, s) => n + (s.sheets?.length || 0), 0),
    [spreadsheets],
  );
  const showing = filtered.length;

  return (
    <div className="gs-spreadsheets-block">
      <div className="gs-spreadsheets-toolbar">
        <h3 className="gs-spreadsheets-heading">
          <FiDatabase className="gs-spreadsheets-heading-icon" aria-hidden />
          Connected workbook
          {total > 0 && (
            <span className="gs-spreadsheets-count" title={`${totalSheets} tabs across ${total} workbooks`}>
              {total} · {totalSheets} tabs
            </span>
          )}
        </h3>
        {total > 0 && (
          <div className="gs-spreadsheets-search-wrap">
            <FiSearch className="gs-spreadsheets-search-icon" aria-hidden />
            <input
              type="search"
              className="gs-spreadsheets-search"
              placeholder="Search workbooks or tabs…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label="Search connected workbooks"
            />
            {search && (
              <button
                type="button"
                className="gs-spreadsheets-search-clear"
                onClick={() => setSearch('')}
                aria-label="Clear search"
              >
                <FiX aria-hidden />
              </button>
            )}
          </div>
        )}
      </div>

      {total === 0 ? (
        <p className="gs-spreadsheets-empty">
          No workbooks connected yet. Add a spreadsheet URL in chat to connect one.
        </p>
      ) : (
        <>
          {search && (
            <p className="gs-spreadsheets-filter-hint">
              {showing === 0
                ? 'No workbooks match your search.'
                : `Showing ${showing} of ${total}`}
            </p>
          )}
          <div className="gs-spreadsheets-scroll">
            {showing > 0 ? (
              <ul className="gs-spreadsheets-list">
                {filtered.map((spreadsheet) => (
                  <li key={spreadsheet.spreadsheet_id} className="gs-spreadsheet-row">
                    <div className="gs-spreadsheet-main">
                      <a
                        href={`https://docs.google.com/spreadsheets/d/${spreadsheet.spreadsheet_id}/edit`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="gs-spreadsheet-name"
                        title={spreadsheet.spreadsheet_name}
                      >
                        {spreadsheet.spreadsheet_name}
                      </a>
                      <SheetChips sheets={spreadsheet.sheets} />
                    </div>
                    <div className="gs-spreadsheet-actions">
                      <button
                        type="button"
                        onClick={() =>
                          onResync(spreadsheet.spreadsheet_id, spreadsheet.spreadsheet_name)
                        }
                        disabled={isLoading || syncingSpreadsheetId === spreadsheet.spreadsheet_id}
                        className="gs-spreadsheet-btn gs-spreadsheet-btn--sync"
                        title="Re-sync and re-analyze this spreadsheet"
                      >
                        {syncingSpreadsheetId === spreadsheet.spreadsheet_id ? (
                          <>
                            <FiRefreshCw className="gs-spin" aria-hidden /> …
                          </>
                        ) : (
                          <FiRefreshCw aria-hidden />
                        )}
                      </button>
                      <button
                        type="button"
                        onClick={() =>
                          onDelete(spreadsheet.spreadsheet_id, spreadsheet.spreadsheet_name)
                        }
                        disabled={isLoading}
                        className="gs-spreadsheet-btn gs-spreadsheet-btn--delete"
                        title="Remove this spreadsheet"
                        aria-label={`Delete ${spreadsheet.spreadsheet_name}`}
                      >
                        <FiTrash2 aria-hidden />
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="gs-spreadsheets-empty gs-spreadsheets-empty--in-scroll">
                Try a different search term.
              </p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
