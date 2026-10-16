import React from 'react';

function formatVersionLabel(item) {
  if (!item) return 'Version';
  const v = item.version != null ? `v${item.version}` : 'v?';
  const status = item.status ? ` · ${item.status}` : '';
  let when = '';
  if (item.created_at) {
    try {
      when = ` · ${new Date(item.created_at).toLocaleString(undefined, {
        dateStyle: 'medium',
        timeStyle: 'short',
      })}`;
    } catch {
      when = '';
    }
  }
  return `${v}${status}${when}`;
}

export default function ProposalVersionPicker({
  versions = [],
  selectedId,
  onSelect,
  disabled = false,
  label = 'Proposal version',
}) {
  if (!versions.length) return null;

  return (
    <label className="eg-proposal-version-picker">
      <span className="eg-proposal-version-label">{label}</span>
      <select
        value={selectedId || versions[0]?.id || ''}
        disabled={disabled || versions.length <= 1}
        onChange={(e) => onSelect?.(e.target.value)}
        aria-label={label}
      >
        {versions.map((v) => (
          <option key={v.id} value={v.id}>
            {formatVersionLabel(v)}
          </option>
        ))}
      </select>
    </label>
  );
}
