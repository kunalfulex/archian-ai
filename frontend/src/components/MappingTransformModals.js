import React, { useEffect, useMemo, useState } from 'react';
import Select from 'react-select';
import { getMappingDestination } from '../utils/mappingTransformEdit';

const selectStyles = {
  menuPortal: (base) => ({ ...base, zIndex: 10001 }),
  menu: (base) => ({ ...base, zIndex: 10001 }),
};

function ModalShell({ title, onClose, onConfirm, confirmLabel, confirmDisabled, children }) {
  return (
    <div className="inline-mapping-modal-overlay" role="presentation" onClick={onClose}>
      <div
        className="inline-mapping-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="inline-mapping-modal-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h4 id="inline-mapping-modal-title" className="inline-mapping-modal-title">
          {title}
        </h4>
        {children}
        <div className="inline-mapping-modal-actions">
          <button type="button" className="inline-mapping-panel-btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="inline-mapping-panel-btn primary"
            onClick={onConfirm}
            disabled={confirmDisabled}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

function getSecondaryOptions(destination, destinationSchema, primary) {
  if (!primary || !destinationSchema?.[primary]) return [];
  if (destination === 'salesforce') {
    return Object.keys(destinationSchema[primary]).map((name) => ({
      label: name,
      value: name,
    }));
  }
  const columns = destinationSchema[primary].columns || {};
  return Object.keys(columns).map((name) => ({ label: name, value: name }));
}

export function CombineFieldsModal({
  isOpen,
  onClose,
  onConfirm,
  selectedKeys,
  mapping,
  destination,
  destinationSchema,
  primaryLabel,
  secondaryLabel,
}) {
  const [separator, setSeparator] = useState(' ');
  const [primary, setPrimary] = useState('');
  const [secondary, setSecondary] = useState('');

  const primaryOptions = useMemo(
    () =>
      Object.keys(destinationSchema || {}).map((name) => ({
        label: name,
        value: name,
      })),
    [destinationSchema],
  );

  const secondaryOptions = useMemo(
    () => getSecondaryOptions(destination, destinationSchema, primary),
    [destination, destinationSchema, primary],
  );

  useEffect(() => {
    if (!isOpen) return;
    setSeparator(' ');
    const firstKey = selectedKeys?.[0];
    const firstDest = getMappingDestination(mapping?.[firstKey], destination);
    const allSame = (selectedKeys || []).every((key) => {
      const dest = getMappingDestination(mapping?.[key], destination);
      return dest.primary === firstDest.primary && dest.secondary === firstDest.secondary;
    });
    if (allSame && firstDest.primary && firstDest.secondary) {
      setPrimary(firstDest.primary);
      setSecondary(firstDest.secondary);
    } else {
      setPrimary(firstDest.primary || primaryOptions[0]?.value || '');
      setSecondary('');
    }
  }, [isOpen, selectedKeys, mapping, destination, primaryOptions]);

  if (!isOpen) return null;

  const fieldList = (selectedKeys || []).join(', ');

  return (
    <ModalShell
      title="Combine fields"
      onClose={onClose}
      confirmLabel="Combine"
      confirmDisabled={!primary || !secondary || (selectedKeys || []).length < 2}
      onConfirm={() => onConfirm({ primary, secondary, separator })}
    >
      <p className="inline-mapping-modal-hint">
        Source fields: <strong>{fieldList}</strong>. Values will be joined with the separator and
        written to one destination column at migration time.
      </p>
      <label className="inline-mapping-modal-field">
        <span>{primaryLabel}</span>
        <Select
          value={primaryOptions.find((o) => o.value === primary) || null}
          onChange={(opt) => {
            setPrimary(opt?.value || '');
            setSecondary('');
          }}
          options={primaryOptions}
          menuPortalTarget={document.body}
          menuPosition="fixed"
          styles={selectStyles}
          classNamePrefix="inline-mapping-select"
        />
      </label>
      <label className="inline-mapping-modal-field">
        <span>{secondaryLabel}</span>
        <Select
          value={secondaryOptions.find((o) => o.value === secondary) || null}
          onChange={(opt) => setSecondary(opt?.value || '')}
          options={secondaryOptions}
          isDisabled={!primary}
          menuPortalTarget={document.body}
          menuPosition="fixed"
          styles={selectStyles}
          classNamePrefix="inline-mapping-select"
        />
      </label>
      <label className="inline-mapping-modal-field">
        <span>Separator</span>
        <select
          className="inline-mapping-remap-select"
          value={separator}
          onChange={(e) => setSeparator(e.target.value)}
        >
          <option value=" ">Space</option>
          <option value=", ">, (comma)</option>
          <option value="">None</option>
          <option value="-">- (hyphen)</option>
        </select>
      </label>
    </ModalShell>
  );
}

export function SplitFieldModal({
  isOpen,
  onClose,
  onConfirm,
  sourceKey,
  mapping,
  destination,
  destinationSchema,
  primaryLabel,
  secondaryLabel,
}) {
  const entry = mapping?.[sourceKey];
  const initialDest = getMappingDestination(entry, destination);

  const [primary, setPrimary] = useState(initialDest.primary);
  const [secondaryA, setSecondaryA] = useState('');
  const [secondaryB, setSecondaryB] = useState('');

  const primaryOptions = useMemo(
    () =>
      Object.keys(destinationSchema || {}).map((name) => ({
        label: name,
        value: name,
      })),
    [destinationSchema],
  );

  const secondaryOptions = useMemo(
    () => getSecondaryOptions(destination, destinationSchema, primary),
    [destination, destinationSchema, primary],
  );

  useEffect(() => {
    if (!isOpen) return;
    const dest = getMappingDestination(mapping?.[sourceKey], destination);
    setPrimary(dest.primary || primaryOptions[0]?.value || '');
    setSecondaryA('');
    setSecondaryB('');
  }, [isOpen, sourceKey, mapping, destination, primaryOptions]);

  if (!isOpen || !sourceKey) return null;

  const canConfirm =
    primary &&
    secondaryA &&
    secondaryB &&
    secondaryA !== secondaryB;

  return (
    <ModalShell
      title={`Split “${sourceKey}”`}
      onClose={onClose}
      confirmLabel="Split field"
      confirmDisabled={!canConfirm}
      onConfirm={() =>
        onConfirm({
          primary,
          targets: [
            { primary, secondary: secondaryA },
            { primary, secondary: secondaryB },
          ],
        })
      }
    >
      <p className="inline-mapping-modal-hint">
        The source value will be split (by space) into two parts and mapped to the destination
        columns below.
      </p>
      <label className="inline-mapping-modal-field">
        <span>{primaryLabel}</span>
        <Select
          value={primaryOptions.find((o) => o.value === primary) || null}
          onChange={(opt) => {
            setPrimary(opt?.value || '');
            setSecondaryA('');
            setSecondaryB('');
          }}
          options={primaryOptions}
          menuPortalTarget={document.body}
          menuPosition="fixed"
          styles={selectStyles}
          classNamePrefix="inline-mapping-select"
        />
      </label>
      <label className="inline-mapping-modal-field">
        <span>{secondaryLabel} (part 1)</span>
        <Select
          value={secondaryOptions.find((o) => o.value === secondaryA) || null}
          onChange={(opt) => setSecondaryA(opt?.value || '')}
          options={secondaryOptions}
          isDisabled={!primary}
          menuPortalTarget={document.body}
          menuPosition="fixed"
          styles={selectStyles}
          classNamePrefix="inline-mapping-select"
        />
      </label>
      <label className="inline-mapping-modal-field">
        <span>{secondaryLabel} (part 2)</span>
        <Select
          value={secondaryOptions.find((o) => o.value === secondaryB) || null}
          onChange={(opt) => setSecondaryB(opt?.value || '')}
          options={secondaryOptions.filter((o) => o.value !== secondaryA)}
          isDisabled={!primary}
          menuPortalTarget={document.body}
          menuPosition="fixed"
          styles={selectStyles}
          classNamePrefix="inline-mapping-select"
        />
      </label>
    </ModalShell>
  );
}
