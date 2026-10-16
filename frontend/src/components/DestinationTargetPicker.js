import React, { useCallback, useEffect, useState } from 'react';
import {
  fetchDestinationConnectionOptions,
  fetchDestinationTargets,
  labelForDestinationTarget,
  labelForDestinationType,
  normalizeDestType,
} from '../utils/destinationTargetOptions';
import './DestinationTargetPicker.css';

/**
 * Type → connection → object/table/tab picker (shared by data preview + mapping panel).
 */
export default function DestinationTargetPicker({
  variant = 'preview',
  title = 'Choose destination',
  submitLabel = 'Apply',
  onSubmit,
  disabled = false,
  initialDestType = 'salesforce',
  initialDestName = '',
  initialDestTarget = '',
}) {
  const [destType, setDestType] = useState(normalizeDestType(initialDestType) || 'salesforce');
  const [destName, setDestName] = useState(initialDestName || '');
  const [destTarget, setDestTarget] = useState(initialDestTarget || '');

  const [connectionOptions, setConnectionOptions] = useState([]);
  const [targetOptions, setTargetOptions] = useState([]);
  const [targetLabel, setTargetLabel] = useState(null);

  const [connectionsLoading, setConnectionsLoading] = useState(false);
  const [targetsLoading, setTargetsLoading] = useState(false);
  const [connectionError, setConnectionError] = useState(null);
  const [targetError, setTargetError] = useState(null);

  const prefix =
    variant === 'remap' ? 'destination-target-picker remap' : 'destination-target-picker preview';

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setConnectionsLoading(true);
      setConnectionError(null);
      setConnectionOptions([]);
      try {
        const opts = await fetchDestinationConnectionOptions(destType);
        if (cancelled) return;
        setConnectionOptions(opts);
        const current = (destName || '').trim().toLowerCase();
        const match = opts.find((o) => o.label.trim().toLowerCase() === current);
        if (match) {
          setDestName(match.label);
        } else if (!current && opts.length) {
          setDestName(opts[0].label);
        } else if (current && !match) {
          setDestName('');
          setDestTarget('');
        }
      } catch (err) {
        if (!cancelled) {
          console.warn('Failed to load destination connections:', err);
          setConnectionError('Could not load connections.');
        }
      } finally {
        if (!cancelled) setConnectionsLoading(false);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [destType]);

  useEffect(() => {
    if (!destName.trim()) {
      setTargetOptions([]);
      setDestTarget('');
      return undefined;
    }
    let cancelled = false;
    const load = async () => {
      setTargetsLoading(true);
      setTargetError(null);
      setTargetOptions([]);
      try {
        const { targetLabel: tl, targets } = await fetchDestinationTargets(destType, destName);
        if (cancelled) return;
        setTargetLabel(tl);
        setTargetOptions(targets);
        const current = (destTarget || '').trim();
        const match = targets.find((t) => t.value === current);
        if (match) {
          setDestTarget(match.value);
        } else if (targets.length) {
          setDestTarget(targets[0].value);
        } else {
          setDestTarget('');
        }
      } catch (err) {
        if (!cancelled) {
          console.warn('Failed to load destination targets:', err);
          setTargetError('Could not load objects / tables / tabs.');
          setDestTarget('');
        }
      } finally {
        if (!cancelled) setTargetsLoading(false);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [destType, destName]);

  const handleTypeChange = useCallback((nextType) => {
    setDestType(normalizeDestType(nextType));
    setDestName('');
    setDestTarget('');
  }, []);

  const canSubmit =
    !disabled &&
    !connectionsLoading &&
    !targetsLoading &&
    destName.trim() &&
    destTarget.trim() &&
    connectionOptions.length > 0 &&
    targetOptions.length > 0;

  const targetFieldLabel =
    targetLabel || labelForDestinationTarget(destType);

  return (
    <div className={`${prefix}-wrap`}>
      {title ? <span className={`${prefix}-title`}>{title}</span> : null}
      <div className={`${prefix}-fields`}>
        <div className={`${prefix}-field`}>
          <span className={`${prefix}-field-label`}>Type</span>
          <select
            className={`${prefix}-select`}
            value={destType}
            onChange={(e) => handleTypeChange(e.target.value)}
            disabled={disabled}
          >
            <option value="salesforce">Salesforce org</option>
            <option value="sql">SQL database</option>
            <option value="google_sheets">Google Sheets</option>
          </select>
        </div>
        <div className={`${prefix}-field ${prefix}-field-connection`}>
          <span className={`${prefix}-field-label`}>{labelForDestinationType(destType)}</span>
          <select
            className={`${prefix}-select`}
            value={destName}
            onChange={(e) => {
              setDestName(e.target.value);
              setDestTarget('');
            }}
            disabled={disabled || connectionsLoading || connectionOptions.length === 0}
          >
            {connectionsLoading ? <option value="">Loading connections…</option> : null}
            {!connectionsLoading && connectionOptions.length === 0 ? (
              <option value="">No connections available</option>
            ) : null}
            {!connectionsLoading &&
              connectionOptions.map((opt) => (
                <option key={opt.value} value={opt.label}>
                  {opt.label}
                </option>
              ))}
          </select>
        </div>
        <div className={`${prefix}-field ${prefix}-field-target`}>
          <span className={`${prefix}-field-label`}>{targetFieldLabel}</span>
          <select
            className={`${prefix}-select`}
            value={destTarget}
            onChange={(e) => setDestTarget(e.target.value)}
            disabled={
              disabled ||
              !destName.trim() ||
              targetsLoading ||
              targetOptions.length === 0
            }
          >
            {targetsLoading ? <option value="">Loading…</option> : null}
            {!targetsLoading && destName && targetOptions.length === 0 ? (
              <option value="">No synced metadata — sync in Connections</option>
            ) : null}
            {!targetsLoading &&
              targetOptions.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
          </select>
        </div>
        {onSubmit ? (
          <button
            type="button"
            className={`${prefix}-submit`}
            disabled={!canSubmit}
            onClick={() =>
              onSubmit({
                destType,
                destName: destName.trim(),
                destTarget: destTarget.trim(),
              })
            }
          >
            {submitLabel}
          </button>
        ) : null}
      </div>
      {connectionError ? (
        <p className={`${prefix}-hint ${prefix}-hint-error`}>{connectionError}</p>
      ) : null}
      {targetError ? (
        <p className={`${prefix}-hint ${prefix}-hint-error`}>{targetError}</p>
      ) : null}
      {!connectionsLoading && !connectionError && connectionOptions.length === 0 ? (
        <p className={`${prefix}-hint`}>
          No {labelForDestinationType(destType).toLowerCase()} found.{' '}
          <a href="/connections">Add a connection</a>
        </p>
      ) : null}
    </div>
  );
}
