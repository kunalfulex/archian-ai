import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import Select from 'react-select';
import {
  FiChevronDown,
  FiChevronUp,
  FiMap,
  FiMaximize2,
  FiMinimize2,
} from 'react-icons/fi';
import api from '../api';
import { useFeatures } from '../hooks/useFeatures';
import './InlineMappingPanel.css';
import {
  isHiddenExtractionMetadataKey,
  omitHiddenExtractionRecordKeys,
} from '../utils/extractionPreviewHiddenKeys';
import {
  logSqlMappingDebug,
  sanitizeSqlMappingForMigration,
} from '../utils/sqlMappingSanitize';
import {
  clearAllTransformations,
  combineSourceFields,
  findCombineRuleForSourceKey,
  formatCombineSourcesLabel,
  getOrphanCombineOutputKeys,
  hasActiveTransformations,
  isCombineSuppressedSourceKey,
  isEligibleForCombine,
  isEligibleForSplit,
  splitSourceField,
  syncMappingFlagsFromTransformationRules,
  uncombineGroup,
  unsplitField,
} from '../utils/mappingTransformEdit';
import { CombineFieldsModal, SplitFieldModal } from './MappingTransformModals';
import { destinationTargetValueForType } from '../utils/destinationTargetOptions';
import {
  applyLookupOfToMappingEntry,
  stripEmptyLookupOfFromMapping,
} from '../utils/mappingLookupOf';

const camelCaseToLabel = (str) =>
  str
    .replace(/_/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/^./, (char) => char.toUpperCase());

/** SQL: FK column on primary pointing at target, or FK on child pointing at primary — matches backend contract. */
function suggestSqlLookupOf(primaryTable, targetTable, destinationSchema) {
  if (!primaryTable || !targetTable || !destinationSchema) return '';
  const p = String(primaryTable).trim();
  const t = String(targetTable).trim();
  if (!p || !t || p.toLowerCase() === t.toLowerCase()) return '';
  const pMeta = destinationSchema[p];
  const tMeta = destinationSchema[t];
  const pFks = (pMeta && Array.isArray(pMeta.foreign_keys) && pMeta.foreign_keys) || [];
  const tFks = (tMeta && Array.isArray(tMeta.foreign_keys) && tMeta.foreign_keys) || [];
  for (const fk of pFks) {
    const ref = (fk.referenced_table || '').trim();
    if (ref && ref.toLowerCase() === t.toLowerCase()) {
      const col = (fk.column_name || '').trim();
      if (col) return `${p}.${col}`;
    }
  }
  for (const fk of tFks) {
    const ref = (fk.referenced_table || '').trim();
    if (ref && ref.toLowerCase() === p.toLowerCase()) {
      const col = (fk.column_name || '').trim();
      if (col) return `${t}.${col}`;
    }
  }
  return '';
}

function sqlRelationshipSelectOptions(primaryTable, targetTable, destinationSchema) {
  const out = new Set();
  const hint = suggestSqlLookupOf(primaryTable, targetTable, destinationSchema);
  if (hint) out.add(hint);
  if (!primaryTable || !destinationSchema) return Array.from(out).sort();
  const p = String(primaryTable).trim();
  const t = (targetTable && String(targetTable).trim()) || '';
  if (!p) return Array.from(out).sort();
  for (const [tblName, meta] of Object.entries(destinationSchema)) {
    const fks = (meta && Array.isArray(meta.foreign_keys) && meta.foreign_keys) || [];
    for (const fk of fks) {
      const col = (fk.column_name || '').trim();
      const ref = (fk.referenced_table || '').trim();
      if (!col || !ref) continue;
      if (tblName.toLowerCase() === p.toLowerCase() && t && ref.toLowerCase() === t.toLowerCase()) {
        out.add(`${tblName}.${col}`);
      }
      if (t && tblName.toLowerCase() === t.toLowerCase() && ref.toLowerCase() === p.toLowerCase()) {
        out.add(`${tblName}.${col}`);
      }
    }
  }
  return Array.from(out).sort();
}

/** Salesforce: BaseObject.LookupField when metadata includes lookup hints (from map-fields describe). */
function suggestSalesforceLookupOf(primaryObject, lookupObject, destinationSchema) {
  if (!primaryObject || !lookupObject || !destinationSchema) return '';
  if (primaryObject === lookupObject) return '';
  const fieldsBlock = destinationSchema[primaryObject];
  if (!fieldsBlock || typeof fieldsBlock !== 'object') return '';
  for (const [fname, finfo] of Object.entries(fieldsBlock)) {
    if (!finfo || typeof finfo !== 'object') continue;
    const lo = finfo.lookup_object || finfo.lookupObject;
    if (finfo.is_lookup && lo === lookupObject) return `${primaryObject}.${fname}`;
    if (
      (finfo.data_type === 'reference' || finfo.type === 'reference') &&
      lo === lookupObject
    ) {
      return `${primaryObject}.${fname}`;
    }
  }
  return '';
}

const InlineMappingPanel = ({
  firstRecord,
  allRecords,
  totalRecordCount, // Total records (from DB) - for plan validation and backend-pull migration
  mapping: initialMapping,
  transformationRules = { fields: [] }, // Transformation rules from map-fields response
  objectFieldMap,
  documentType,
  documentDescription,
  sourceDocumentType,
  sourceDocumentDescription,
  destinationDocumentType,
  destinationDocumentDescription,
  destinationObjectApiName,
  destinationTableName = null,
  destinationSheetName = null,
  destination = 'salesforce',
  destinationName,
  onMigrationStart,
  conversationId,
  isCollapsed: initialCollapsed = false,
  onMappingChange: onMappingChangeCallback,
  mappingId,
  onMigrationStartLoader,
  onMigrationEndLoader,
  missingFieldPreference, // { option: 'placeholder'|'skip', placeholderValue: string }
  onPlanLimitExceeded, // Callback for plan limit errors (optional)
  onMissingFieldPreferenceChange, // Callback to update preference in metadata
  mappingMessageId, // The UUID of the mapping_started message for updating metadata
  jobId, // Extraction job_id for transformation
  flowId, // Optional flow_id for saved flows
  orgId, // Optional org_id for Salesforce metadata lookup
  onTransformationComplete, // Optional callback when transformation is complete
  /** Optional ref for auto-click (programmatic trigger when Auto mode is on) */
  startMigrationButtonRef,
  /** Per-object display JSON from map-fields (mapped objects only): { validation[], duplicate[], matching[], has_any } */
  orgRulesDisplayByObject = {},
  /** Row-level source rows were removed by retention; mapping from chat metadata is still shown. */
  detailExpired = false,
  /** API primary table for SQL (map-fields `primary_table`); used for lookup_of hints vs inferred primary. */
  primaryTable = null,
}) => {
  const { billing } = useFeatures();
  const destinationTarget = destinationTargetValueForType(destination, {
    destinationObjectApiName,
    destinationTableName,
    destinationSheetName,
  });
  const [mapping, setMapping] = useState(initialMapping || {});
  const [transRules, setTransRules] = useState(() => transformationRules || { fields: [] });
  const [isCollapsed, setIsCollapsed] = useState(initialCollapsed);
  const [isExpanded, setIsExpanded] = useState(false);
  const [isStarting, setIsStarting] = useState(false);
  const [missingFieldOption, setMissingFieldOption] = useState(missingFieldPreference?.option || 'skip');
  const [placeholderValue, setPlaceholderValue] = useState(missingFieldPreference?.placeholderValue || 'Unknown');
  const [choiceSavedMessage, setChoiceSavedMessage] = useState('');
  const [transformedData, setTransformedData] = useState(null);
  const [isTransforming, setIsTransforming] = useState(false);
  const [transformationError, setTransformationError] = useState(null);
  const [selectedCombineKeys, setSelectedCombineKeys] = useState(() => new Set());
  const [combineModalOpen, setCombineModalOpen] = useState(false);
  const [splitModalSourceKey, setSplitModalSourceKey] = useState(null);
  
  // Sync state with prop changes - update when preference prop changes
  useEffect(() => {
    if (missingFieldPreference) {
      setMissingFieldOption(missingFieldPreference.option || 'skip');
      setPlaceholderValue(missingFieldPreference.placeholderValue || 'Unknown');
    } else {
      // If no preference prop, use defaults
      setMissingFieldOption('skip');
      setPlaceholderValue('Unknown');
    }
  }, [missingFieldPreference]);

  const isUserChangeRef = useRef(false);
  const lastSyncedMappingRef = useRef(JSON.stringify(initialMapping || {}));
  const lastSyncedRulesRef = useRef(JSON.stringify(transformationRules || { fields: [] }));

  const applyPanelUpdate = useCallback(
    (updates) => {
      isUserChangeRef.current = true;

      const nextMapping = updates.mapping !== undefined ? updates.mapping : mapping;
      const nextRules =
        updates.transformationRules !== undefined ? updates.transformationRules : transRules;

      if (updates.mapping !== undefined) {
        setMapping(nextMapping);
        lastSyncedMappingRef.current = JSON.stringify(nextMapping);
      }
      if (updates.transformationRules !== undefined) {
        setTransRules(nextRules);
        lastSyncedRulesRef.current = JSON.stringify(nextRules);
      }

      if (onMappingChangeCallback) {
        onMappingChangeCallback(mappingId, nextMapping, nextRules);
      }
    },
    [mapping, transRules, mappingId, onMappingChangeCallback],
  );

  const handleUncombine = useCallback(
    (combineGroup) => {
      const result = uncombineGroup(mapping, transRules, combineGroup);
      setTransformedData(null);
      setTransformationError(null);
      applyPanelUpdate(result);
    },
    [mapping, transRules, applyPanelUpdate],
  );

  const handleUnsplit = useCallback(
    (sourceKey) => {
      const items = mapping[sourceKey];
      let keepIndex = 0;
      if (Array.isArray(items) && items.length > 1) {
        const destKey = (item) => item?.field || item?.column || '';
        const firstDest = destKey(items[0]);
        const allSame = items.every((item) => destKey(item) === firstDest);
        if (!allSame) {
          const labels = items
            .map((item, i) => `${i + 1}. ${destKey(item) || 'field'}`)
            .join(', ');
          const choice = window.prompt(
            `Which destination mapping should be kept? Enter 1-${items.length} (${labels})`,
            '1',
          );
          const parsed = parseInt(choice, 10);
          if (Number.isNaN(parsed) || parsed < 1 || parsed > items.length) return;
          keepIndex = parsed - 1;
        }
      }
      const result = unsplitField(mapping, transRules, sourceKey, { keepIndex });
      setTransformedData(null);
      setTransformationError(null);
      applyPanelUpdate(result);
    },
    [mapping, transRules, applyPanelUpdate],
  );

  const handleClearAllTransformations = useCallback(() => {
    if (
      !window.confirm(
        'Remove all combine and split transformations? Each field will use a single mapping.',
      )
    ) {
      return;
    }
    const result = clearAllTransformations(mapping, transRules);
    setTransformedData(null);
    setTransformationError(null);
    applyPanelUpdate(result);
  }, [mapping, transRules, applyPanelUpdate]);

  const showTransformActions = useMemo(
    () => hasActiveTransformations(mapping, transRules),
    [mapping, transRules],
  );

  const toggleCombineSelect = useCallback((key) => {
    setSelectedCombineKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const handleCombineConfirm = useCallback(
    ({ primary, secondary, separator }) => {
      const orderedKeys = Array.from(selectedCombineKeys);
      const result = combineSourceFields(
        mapping,
        transRules,
        orderedKeys,
        { primary, secondary },
        { separator, destination },
      );
      if (!result) {
        alert('Could not combine selected fields. Ensure each row has a valid mapping.');
        return;
      }
      setSelectedCombineKeys(new Set());
      setCombineModalOpen(false);
      setTransformedData(null);
      setTransformationError(null);
      applyPanelUpdate(result);
    },
    [mapping, transRules, selectedCombineKeys, destination, applyPanelUpdate],
  );

  const handleSplitConfirm = useCallback(
    ({ targets }) => {
      if (!splitModalSourceKey) return;
      const result = splitSourceField(
        mapping,
        transRules,
        splitModalSourceKey,
        targets,
        { destination },
      );
      if (!result) {
        alert('Could not split this field.');
        return;
      }
      setSplitModalSourceKey(null);
      setTransformedData(null);
      setTransformationError(null);
      applyPanelUpdate(result);
    },
    [mapping, transRules, splitModalSourceKey, destination, applyPanelUpdate],
  );

  useEffect(() => {
    setSelectedCombineKeys((prev) => {
      const next = new Set(
        [...prev].filter((key) => isEligibleForCombine(key, mapping)),
      );
      return next.size === prev.size ? prev : next;
    });
  }, [mapping]);

  // Only sync from prop if it's not a user-initiated change
  useEffect(() => {
    const currentInitialMappingStr = JSON.stringify(initialMapping || {});
    const currentMappingStr = JSON.stringify(mapping);
    const lastSyncedStr = lastSyncedMappingRef.current;
    
    // Only sync if:
    // 1. Prop changed from what we last synced, AND
    // 2. Either it's not from a user change, OR the prop matches our current state (meaning parent echoed our change)
    if (currentInitialMappingStr !== lastSyncedStr) {
      if (!isUserChangeRef.current || currentInitialMappingStr === currentMappingStr) {
        // Sync: either external change or parent echoed our user change
        setMapping(initialMapping || {});
        lastSyncedMappingRef.current = currentInitialMappingStr;
      }
      // Reset flag after handling
      isUserChangeRef.current = false;
    }
  }, [initialMapping, mapping]);

  useEffect(() => {
    const incomingStr = JSON.stringify(transformationRules || { fields: [] });
    const currentStr = JSON.stringify(transRules);
    const lastSyncedStr = lastSyncedRulesRef.current;

    if (incomingStr !== lastSyncedStr) {
      if (!isUserChangeRef.current || incomingStr === currentStr) {
        setTransRules(transformationRules || { fields: [] });
        lastSyncedRulesRef.current = incomingStr;
      }
    }
  }, [transformationRules, transRules]);

  useEffect(() => {
    if (!isExpanded) return undefined;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        setIsExpanded(false);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [isExpanded]);

  // When transformation rules define combine but mapping keys lack flags, sync for display + save flow.
  useEffect(() => {
    if (!transRules?.fields?.length) return;
    const synced = syncMappingFlagsFromTransformationRules(mapping, transRules, destination);
    if (synced !== mapping) {
      applyPanelUpdate({ mapping: synced });
    }
  }, [transRules, destination]); // eslint-disable-line react-hooks/exhaustive-deps

  const orphanCombineKeys = useMemo(
    () => getOrphanCombineOutputKeys(mapping, transRules),
    [mapping, transRules],
  );

  // Helper functions to normalize destination-specific data
  const normalizeSchema = (schemaResponse) => {
    if (!schemaResponse) return {};
    
    // If schemaResponse already has the destination-specific key, use it
    if (destination === 'salesforce') {
      return schemaResponse.available_objects || schemaResponse;
    } else if (destination === 'google_sheets') {
      return schemaResponse.available_sheets || schemaResponse;
    } else if (destination === 'sql') {
      return schemaResponse.available_tables || schemaResponse;
    }
    
    // Fallback: try to detect from structure
    // If it has nested columns structure, it's likely Google Sheets or SQL
    const firstKey = Object.keys(schemaResponse)[0];
    if (firstKey && schemaResponse[firstKey]?.columns) {
      // Could be Google Sheets or SQL - return as is, destination will determine usage
      return schemaResponse;
    }
    
    // Otherwise assume it's already in the correct format
    return schemaResponse;
  };

  const getMappingPrimary = (mappingItem) => {
    if (!mappingItem) return '';
    // Handle array case (split fields) - get from first item
    if (Array.isArray(mappingItem) && mappingItem.length > 0) {
      const firstItem = mappingItem[0];
      return firstItem.object || firstItem.sheet || firstItem.table || '';
    }
    // Single object case
    if (typeof mappingItem === 'object') {
      return mappingItem.object || mappingItem.sheet || mappingItem.table || '';
    }
    return '';
  };

  const getMappingSecondary = (mappingItem) => {
    if (!mappingItem) return '';
    // Handle array case (split fields) - return empty or indicate multiple
    if (Array.isArray(mappingItem)) {
      // For arrays, we'll show multiple rows, so return empty here
      return '';
    }
    // Single object case
    if (typeof mappingItem === 'object') {
      return mappingItem.field || mappingItem.column || '';
    }
    return '';
  };

  const getDestinationHeaders = () => {
    switch(destination) {
      case 'salesforce':
        return {
          primaryLabel: 'Salesforce Object',
          secondaryLabel: 'Salesforce Field',
          primaryKey: 'object',
          secondaryKey: 'field'
        };
      case 'google_sheets':
        return {
          primaryLabel: 'Sheet Name',
          secondaryLabel: 'Column Name',
          primaryKey: 'sheet',
          secondaryKey: 'column'
        };
      case 'sql':
        return {
          primaryLabel: 'Table Name',
          secondaryLabel: 'Column Name',
          primaryKey: 'table',
          secondaryKey: 'column'
        };
      default:
        return {
          primaryLabel: 'Object/Sheet/Table',
          secondaryLabel: 'Field/Column',
          primaryKey: 'object',
          secondaryKey: 'field'
        };
    }
  };

  // Normalize the schema based on destination
  const destinationSchema = normalizeSchema(objectFieldMap);
  const headers = getDestinationHeaders();

  // Find the primary destination object (most common in mapping, or first one)
  const primaryObject = useMemo(() => {
    if (!destinationSchema || Object.keys(destinationSchema).length === 0) {
      return null;
    }

    // Try to determine primary object from existing mapping (handle arrays)
    const objectCounts = {};
    Object.values(mapping).forEach((mappingItem) => {
      // Handle array case (split fields) - count objects from all items
      if (Array.isArray(mappingItem)) {
        mappingItem.forEach(item => {
          const obj = getMappingPrimary(item);
          if (obj) {
            objectCounts[obj] = (objectCounts[obj] || 0) + 1;
          }
        });
      } else {
        // Single object case
        const obj = getMappingPrimary(mappingItem);
        if (obj) {
          objectCounts[obj] = (objectCounts[obj] || 0) + 1;
        }
      }
    });
    
    // Get the most frequently mapped object, or the first available object
    if (Object.keys(objectCounts).length > 0) {
      return Object.keys(objectCounts).reduce((a, b) => 
        objectCounts[a] > objectCounts[b] ? a : b
      );
    } else {
      return Object.keys(destinationSchema)[0];
    }
  }, [mapping, destinationSchema]);

  const effectivePrimaryTable =
    (primaryTable && String(primaryTable).trim()) || primaryObject || null;

  const showLookupRelationshipColumn =
    destination === 'sql' || destination === 'salesforce';

  const lookupRelationshipPlaceholder =
    destination === 'sql'
      ? 'e.g. user_plans.user_id (only when mapping a different table)'
      : `${effectivePrimaryTable || 'Contact'}.AccountId`;

  // Initialize mapping for required destination fields that are missing
  // Note: This is a helper to ensure required destination fields get added to mapping
  // The actual display logic in enhancedFirstRecord handles showing them
  useEffect(() => {
    if (destination !== 'salesforce' || !primaryObject || !destinationSchema[primaryObject]) {
      return;
    }

    // This effect is mainly for future enhancement - currently we rely on the mapping
    // from backend to have required fields, and enhancedFirstRecord displays them
    // even if source values are blank
  }, [primaryObject, destinationSchema, destination]);

  // Get enhanced firstRecord with all required destination fields included
  const enhancedFirstRecord = useMemo(() => {
    if (!firstRecord) {
      return {};
    }

    const enhancedRecord = omitHiddenExtractionRecordKeys(firstRecord);

    // Ensure mapped source fields appear (including combine groups); do not inject destination-only keys.
    Object.entries(mapping).forEach(([sourceFieldKey, mappingItem]) => {
      if (isHiddenExtractionMetadataKey(sourceFieldKey)) {
        return;
      }
      if (isCombineSuppressedSourceKey(sourceFieldKey, transRules)) {
        return;
      }
      // Check if mappingItem is an array (split fields)
      if (Array.isArray(mappingItem)) {
        // For arrays, check if any item is required
        const hasRequired = mappingItem.some(item => item?.is_required);
        if (hasRequired && !(sourceFieldKey in enhancedRecord)) {
          enhancedRecord[sourceFieldKey] = null;
        }
      } else if (mappingItem && typeof mappingItem === 'object') {
        // Single mapping object
        if (mappingItem.is_required && !(sourceFieldKey in enhancedRecord)) {
          // Add required field from mapping with empty value
          enhancedRecord[sourceFieldKey] = null;
        }
      }
    });

    return enhancedRecord;
  }, [firstRecord, mapping]);

  // Check for records with missing required fields (handle both single objects and arrays)
  const recordsWithMissingRequiredFields = useMemo(() => {
    if (!allRecords || !mapping) return [];
    
    return allRecords.filter(record => {
      return Object.entries(mapping).some(([key, meta]) => {
        // Handle array case (split fields)
        if (Array.isArray(meta)) {
          // Check if any required field in the array is missing
          return meta.some(item => {
            if (item?.is_required) {
              // For split fields, the value comes from the original source field
              return !record[key] || record[key] === null || record[key] === '' ||
                     (typeof record[key] === 'string' && record[key].trim() === '');
            }
            return false;
          });
        }
        // Handle single object case
        return meta?.is_required && (!record[key] || record[key] === null || record[key] === '' ||
        (typeof record[key] === 'string' && record[key].trim() === ''));
      });
    });
  }, [allRecords, mapping]);

  const hasMissingRequiredFields = recordsWithMissingRequiredFields.length > 0;

  /** Every Salesforce object API name currently selected in the mapping (distinct). */
  const mappedObjectNames = useMemo(() => {
    const s = new Set();
    Object.values(mapping || {}).forEach((val) => {
      if (Array.isArray(val)) {
        val.forEach((item) => {
          if (item?.object) s.add(String(item.object).trim());
        });
      } else if (val?.object) {
        s.add(String(val.object).trim());
      }
    });
    return Array.from(s).filter(Boolean).sort();
  }, [mapping]);

  /** One block per mapped object that has display rules from API (validation / duplicate / matching). */
  const orgRulesBlocks = useMemo(() => {
    if (destination !== 'salesforce') return [];
    const out = [];
    mappedObjectNames.forEach((objectApiName) => {
      const d = orgRulesDisplayByObject?.[objectApiName];
      if (!d || typeof d !== 'object') return;
      const validationLines = (d.validation || []).map((r) => r.summary || '').filter(Boolean);
      const duplicateLines = (d.duplicate || []).map((r) => r.summary || '').filter(Boolean);
      const matchingLines = (d.matching || []).map((r) => r.summary || '').filter(Boolean);
      const hasAny =
        validationLines.length > 0 || duplicateLines.length > 0 || matchingLines.length > 0;
      if (hasAny) {
        out.push({
          objectApiName,
          validationLines,
          duplicateLines,
          matchingLines,
        });
      }
    });
    return out;
  }, [destination, mappedObjectNames, orgRulesDisplayByObject]);

  const hasOrgRulesContent = orgRulesBlocks.length > 0;

  const handleMissingFieldChoiceChange = async (option) => {
    setMissingFieldOption(option);
    
    // Only save immediately if option is 'skip'
    // For 'placeholder', we'll save when user clicks "Start Migration"
    if (option === 'skip' && onMissingFieldPreferenceChange) {
      const preference = {
        option: 'skip',
        placeholderValue: placeholderValue,
      };
      
      // Call callback to update metadata and show message
      await onMissingFieldPreferenceChange(preference);
      
      // Show confirmation message
      setChoiceSavedMessage(`✅ Skip records with missing required fields - Preference saved`);
      // Clear message after 3 seconds
      setTimeout(() => setChoiceSavedMessage(''), 3000);
    } else if (option === 'placeholder') {
      // Just show a message that preference will be saved on migration
      setChoiceSavedMessage(`✅ Preference will be saved when you start migration`);
      setTimeout(() => setChoiceSavedMessage(''), 3000);
    }
  };
  
  const handlePlaceholderValueChange = (value) => {
    // Just update local state, don't save to metadata yet
    // Will be saved when user clicks "Start Migration"
    setPlaceholderValue(value);
  };

  const handleMappingChange = (key, field, value) => {
    const previousItem = mapping[key] || {};
    let updatedItem = {
      ...previousItem,
      [field]: value,
    };

    if (field === 'lookup_of') {
      updatedItem = applyLookupOfToMappingEntry(updatedItem, value);
    } else if (field === headers.primaryKey) {
      updatedItem[headers.secondaryKey] = '';
      const nextPrimary = (value || '').trim();
      let suggested = '';
      if (destination === 'sql' && effectivePrimaryTable && nextPrimary) {
        suggested =
          suggestSqlLookupOf(effectivePrimaryTable, nextPrimary, destinationSchema) || '';
      } else if (destination === 'salesforce' && primaryObject && nextPrimary) {
        suggested =
          suggestSalesforceLookupOf(primaryObject, nextPrimary, destinationSchema) || '';
      }
      updatedItem = applyLookupOfToMappingEntry(updatedItem, suggested);
    }

    const newMapping = {
      ...mapping,
      [key]: updatedItem,
    };

    applyPanelUpdate({ mapping: newMapping });
  };

  const handleLookupOfChange = (key, value, splitItemIndex = null) => {
    const cur = mapping[key];
    if (Array.isArray(cur)) {
      const idx = splitItemIndex ?? 0;
      const updatedArray = cur.map((it, i) =>
        i === idx ? applyLookupOfToMappingEntry(it, value) : { ...it },
      );
      const newMapping = { ...mapping, [key]: updatedArray };
      applyPanelUpdate({ mapping: newMapping });
      return;
    }
    handleMappingChange(key, 'lookup_of', value);
  };

  const handlePreviewTransformation = async () => {
    if (!jobId || !mapping || Object.keys(mapping).length === 0) {
      alert('Job ID and mapping are required for transformation preview');
      return;
    }

    setIsTransforming(true);
    setTransformationError(null);

    try {
      const payload = {
        job_id: jobId,
        mapping: mapping,
        transformation_rules: transRules,
        destination: destination, // Send destination from map-fields response
      };

      // Add optional fields if available
      if (flowId) {
        payload.flow_id = flowId;
      }
      if (orgId && destination === 'salesforce') {
        payload.org_id = orgId;
      }

      const response = await api.post('/transform-data', payload);

      if (response.data.status === 'success') {
        const rows = response.data.transformed_data;
        const apiCount =
          response.data.record_count != null && response.data.record_count !== ''
            ? Number(response.data.record_count)
            : Array.isArray(rows)
              ? rows.length
              : 0;
        const transformedData = {
          data: rows,
          metadata: response.data.transformation_metadata,
          record_count: apiCount,
          transformed_job_id: response.data.transformed_job_id,
        };
        setTransformedData(transformedData);
        
        // Notify parent component about transformation completion
        if (onTransformationComplete) {
          const tm = {
            ...(response.data.transformation_metadata || {}),
            record_count: apiCount,
          };
          onTransformationComplete(rows, response.data.transformed_job_id, tm);
        }
      } else {
        setTransformationError(response.data.detail || 'Transformation failed');
      }
    } catch (error) {
      console.error('Transformation error:', error);
      setTransformationError(
        error.response?.data?.detail || error.message || 'Failed to transform data'
      );
    } finally {
      setIsTransforming(false);
    }
  };

  const handleStartMigration = async () => {
    if (!onMigrationStart) return;
    if (detailExpired) {
      if (onMigrationEndLoader) onMigrationEndLoader();
      return;
    }

    // Persist preference before migration (backend applies skip/placeholder when loading from DB)
    if (hasMissingRequiredFields && onMissingFieldPreferenceChange) {
      const preference =
        missingFieldOption === 'placeholder'
          ? { option: 'placeholder', placeholderValue: placeholderValue || 'Unknown' }
          : { option: 'skip' };
      await onMissingFieldPreferenceChange(preference);
    }

    const isTransformedData = !!(
      transformedData &&
      transformedData.data &&
      transformedData.data.length > 0
    );
    const transformedJobId = isTransformedData ? transformedData.transformed_job_id : null;

    setIsStarting(true);

    if (onMigrationStartLoader) {
      const destinationLabel =
        destinationName ||
        (destination === 'google_sheets'
          ? 'Google Sheets'
          : destination === 'sql'
            ? 'Database'
            : 'Salesforce');
      onMigrationStartLoader(`Starting migration to ${destinationLabel}`);
    }

    try {
      const recordCountForValidation =
        totalRecordCount != null && totalRecordCount >= 0
          ? totalRecordCount
          : allRecords?.length ?? 0;
      const validationResponse = billing
        ? await api.post("/validate-extraction", {
            record_count: recordCountForValidation
          })
        : null;

      if (validationResponse?.data?.status === 'error') {
        const errorData = validationResponse.data;
        const details = errorData.details || {};
        
        // Check if upgrade is required
        if (errorData.upgrade_required || details.upgrade_required) {
          // Use callback to add message to chat history (similar to ChatUploader pattern)
          if (onPlanLimitExceeded) {
            onPlanLimitExceeded({
              message: errorData.message,
              details: details
            });
          } else {
            // Fallback: show alert if callback is not provided
            const userConfirmed = window.confirm(
              `${errorData.message}\n\nWould you like to upgrade your plan now?`
            );
            
            if (userConfirmed) {
              window.location.href = '/plans';
            }
          }
        } else {
          // Show regular error alert for non-upgrade errors
          alert(`Plan limit exceeded: ${errorData.message}`);
        }
        
        setIsStarting(false);
        // Hide loader on validation error
        if (onMigrationEndLoader) {
          onMigrationEndLoader();
        }
        return;
      }

      const migrationRulesPayload =
        hasMissingRequiredFields &&
        (missingFieldOption === 'placeholder' || missingFieldOption === 'skip')
          ? {
              missing_field_preference:
                missingFieldOption === 'placeholder'
                  ? {
                      option: 'placeholder',
                      placeholderValue: placeholderValue || 'Unknown',
                    }
                  : { option: 'skip' },
            }
          : undefined;

      let mappingForMigrate = mapping;
      const primary = effectivePrimaryTable || primaryObject;

      if (destination === 'sql') {
        const { mapping: sanitized, dropped } = sanitizeSqlMappingForMigration(
          mappingForMigrate,
          primary,
        );
        mappingForMigrate = sanitized;
        logSqlMappingDebug(sanitized, primary, dropped);
      } else if (destination === 'salesforce') {
        mappingForMigrate = stripEmptyLookupOfFromMapping(mappingForMigrate);
      }

      await onMigrationStart({
        mapping: mappingForMigrate,
        document_type: documentType,
        document_description: documentDescription,
        destination,
        destination_name: destinationName,
        conversation_id: conversationId,
        transformed_job_id: transformedJobId,
        is_transformed_data: isTransformedData,
        transformation_rules: isTransformedData ? transRules : null,
        job_id: jobId,
        primary_table: effectivePrimaryTable || primaryObject || null,
        migration_rules: migrationRulesPayload,
      });
      
      // Note: Loader will be hidden in parent after migration status is added
    } catch (error) {
      console.error('Migration start error:', error);
      alert(`Failed to start migration: ${error.response?.data?.detail || error.message}`);
      // Hide loader on error
      if (onMigrationEndLoader) {
        onMigrationEndLoader();
      }
    } finally {
      setIsStarting(false);
    }
  };

  return (
    <div className={`inline-mapping-panel${isExpanded ? ' inline-mapping-panel-expanded' : ''}`}>
      <div 
        className="inline-mapping-panel-header"
        onClick={() => setIsCollapsed(!isCollapsed)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setIsCollapsed(!isCollapsed);
          }
        }}
      >
        <div className="inline-mapping-panel-title">
          <span className="inline-mapping-panel-icon"><FiMap /></span>
        <div className="inline-mapping-panel-title-text">
          <span className="inline-mapping-panel-heading">
            <strong>Field Mapping</strong>
          </span>
          {destinationName && (
            <span className="inline-mapping-panel-destination" title="Current destination">
              → {destinationName}
              {destinationTarget ? ` · ${destinationTarget}` : ''}
            </span>
          )}
        </div>
        </div>
        <div className="inline-mapping-panel-toggle">
          {isCollapsed ? <FiChevronDown /> : <FiChevronUp />}
        </div>
      </div>

        {!isCollapsed && (
          <div className="inline-mapping-panel-content">
            {detailExpired && (
              <div
                className="inline-mapping-retention-notice"
                style={{
                  marginBottom: '12px',
                  padding: '12px',
                  backgroundColor: '#fef3c7',
                  border: '1px solid #fcd34d',
                  borderRadius: '6px',
                  color: '#92400e',
                  fontSize: '13px',
                }}
              >
                Extraction record detail is no longer available (retention policy). Saved field mapping below is
                unchanged.
              </div>
            )}
            <div className="inline-mapping-panel-info">
              <p>
                Mapping <strong>{
                  Object.keys(enhancedFirstRecord || {})
                    .filter(key => {
                      if (isHiddenExtractionMetadataKey(key)) return false;
                      // Filter out attachment fields for Salesforce - they're handled automatically
                      if (destination === 'salesforce') {
                        const lowerKey = key.toLowerCase();
                        return !['attachments', '_attachments'].includes(lowerKey);
                      }
                      return true;
                    }).length
                } fields</strong> for{' '}
                <strong>
                  {totalRecordCount != null && totalRecordCount !== undefined
                    ? totalRecordCount
                    : allRecords.length}{' '}
                  records
                </strong>
              </p>
              {(sourceDocumentType || destinationDocumentType || documentType) && (
                <p className="inline-mapping-panel-meta">
                  {sourceDocumentType && (
                    <>
                      <strong>Source:</strong> {sourceDocumentType}
                      {sourceDocumentDescription && ` • ${sourceDocumentDescription}`}
                    </>
                  )}
                  {(sourceDocumentType || sourceDocumentDescription) &&
                    (destinationDocumentType || documentType || destinationObjectApiName) &&
                    ' → '}
                  {(destinationDocumentType || documentType) && (
                    <>
                      <strong>Destination:</strong>{' '}
                      {destinationObjectApiName || destinationDocumentType || documentType}
                      {(destinationDocumentDescription || documentDescription) &&
                        ` • ${destinationDocumentDescription || documentDescription}`}
                      {destinationName ? ` (${destinationName})` : ''}
                    </>
                  )}
                </p>
              )}
            </div>

            {showLookupRelationshipColumn && (
              <p className="inline-mapping-lookup-hint">
                {destination === 'sql' ? (
                  <>
                    <strong>Lookup / relationship</strong> uses the same <code>table.fk_column</code> contract as
                    migrations (driving table: <code>{effectivePrimaryTable || '—'}</code>). Pick a non-primary table to
                    see suggested FK paths; you can edit the value if your schema has multiple relationships.
                  </>
                ) : (
                  <>
                    <strong>Lookup / relationship</strong> uses <code>BaseObject.LookupFieldApiName</code> (for example{' '}
                    <code>{lookupRelationshipPlaceholder}</code>) when a field maps to a related object.
                  </>
                )}
              </p>
            )}

            <div className="inline-mapping-transform-toolbar">
              {selectedCombineKeys.size >= 2 && (
                <button
                  type="button"
                  className="inline-mapping-panel-btn inline-mapping-combine-selected-btn"
                  onClick={() => setCombineModalOpen(true)}
                >
                  Combine selected ({selectedCombineKeys.size})
                </button>
              )}
              {selectedCombineKeys.size === 1 && (
                <span className="inline-mapping-transform-toolbar-hint">
                  Select one more field to combine
                </span>
              )}
            </div>

            <div className="inline-mapping-panel-table-wrapper">
              <table className="inline-mapping-panel-table">
                <colgroup>
                  <col className="inline-mapping-col-select" />
                  <col className="inline-mapping-col-field" />
                  <col className="inline-mapping-col-sample" />
                  <col className="inline-mapping-col-primary" />
                  <col className="inline-mapping-col-secondary" />
                  {showLookupRelationshipColumn && <col className="inline-mapping-col-lookup" />}
                  <col className="inline-mapping-col-duplicate" />
                </colgroup>
                <thead>
                  <tr>
                    <th className="inline-mapping-th-select" aria-label="Select for combine">
                      <button
                        type="button"
                        className="inline-mapping-expand-btn"
                        onClick={(e) => {
                          e.stopPropagation();
                          setIsExpanded((prev) => !prev);
                        }}
                        aria-label={isExpanded ? 'Collapse mapping view' : 'Expand mapping view'}
                        title={isExpanded ? 'Collapse mapping view' : 'Expand mapping view'}
                      >
                        {isExpanded ? <FiMinimize2 /> : <FiMaximize2 />}
                      </button>
                    </th>
                    <th>Field</th>
                    <th>Sample Value</th>
                    <th>{headers.primaryLabel}</th>
                    <th>{headers.secondaryLabel}</th>
                    {showLookupRelationshipColumn && (
                      <th className="inline-mapping-th-lookup">Lookup / relationship</th>
                    )}
                    <th>Duplicate Check</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(enhancedFirstRecord || {})
                    .filter(([key]) => {
                      if (isHiddenExtractionMetadataKey(key)) return false;
                      if (orphanCombineKeys.has(key)) return false;
                      if (isCombineSuppressedSourceKey(key, transRules)) return false;
                      // Filter out attachment fields for Salesforce - they're handled automatically
                      if (destination === 'salesforce') {
                        const lowerKey = key.toLowerCase();
                        return !['attachments', '_attachments'].includes(lowerKey);
                      }
                      return true;
                    })
                    .map(([key, value], idx) => {
                    const mappingItem = mapping[key];
                    
                    // Check if this is an array (split fields)
                    const isSplitField = Array.isArray(mappingItem);
                    
                    // For split fields, we'll render differently
                    if (isSplitField && mappingItem.length > 0) {
                      // Render split field: one row with sub-rows for each array item
                      return (
                        <React.Fragment key={idx}>
                          {/* Main row showing the source field with split indicator */}
                          {mappingItem.map((item, itemIdx) => {
                            const selectedPrimary = getMappingPrimary(item);
                            const selectedSecondary = item?.field || item?.column || '';
                            const isCustom = item?.is_custom || false;
                            const isDuplicateCheck = item?.is_duplicate_check || false;
                            const isRequired = item?.is_required || false;

                            // Primary options (objects/sheets/tables)
                            const primaryOptions = Object.keys(destinationSchema).map((primaryKey) => ({
                              label: primaryKey,
                              value: primaryKey,
                            }));

                            // Get secondary options based on selected primary and destination type
                            let secondaryOptions = [];
                            if (selectedPrimary && destinationSchema[selectedPrimary]) {
                              if (destination === 'salesforce') {
                                secondaryOptions = Object.keys(destinationSchema[selectedPrimary]).map((fieldName) => ({
                                  label: fieldName,
                                  value: fieldName,
                                }));
                              } else if (destination === 'google_sheets') {
                                const columns = destinationSchema[selectedPrimary].columns || {};
                                secondaryOptions = Object.keys(columns).map((colName) => ({
                                  label: colName,
                                  value: colName,
                                }));
                              } else if (destination === 'sql') {
                                const columns = destinationSchema[selectedPrimary].columns || {};
                                secondaryOptions = Object.keys(columns).map((colName) => ({
                                  label: colName,
                                  value: colName,
                                }));
                              }
                            }

                            // Custom field option for Salesforce only
                            const customFieldOption =
                              destination === 'salesforce' && 
                              isCustom && 
                              selectedSecondary && 
                              !secondaryOptions.find(f => f.value === selectedSecondary)
                                ? [{ label: selectedSecondary, value: selectedSecondary }]
                                : [];

                            const allSecondaryOptions = [...customFieldOption, ...secondaryOptions];

                            return (
                              <tr key={`${idx}-${itemIdx}`} style={{ backgroundColor: itemIdx > 0 ? '#f9fafb' : 'transparent', borderTop: itemIdx > 0 ? '1px solid #e5e7eb' : 'none' }}>
                                <td className="inline-mapping-td-select" />
                                <td>
                                  {itemIdx === 0 && (
                                    <>
                                      <div style={{ display: 'flex', alignItems: 'center', gap: '4px', flexWrap: 'wrap' }}>
                                        <strong>{camelCaseToLabel(key)}</strong>
                                        <span style={{ fontSize: '10px', color: '#6366f1', fontWeight: '500' }}>
                                          (split)
                                        </span>
                                        <button
                                          type="button"
                                          className="inline-mapping-transform-action"
                                          onClick={() => handleUnsplit(key)}
                                          title="Collapse split into a single destination mapping"
                                        >
                                          Use single mapping
                                        </button>
                                      </div>
                                      <div style={{ fontSize: '11px', color: '#6b7280', marginTop: '4px' }}>
                                        ↳ <strong>{camelCaseToLabel(selectedSecondary || 'Field')}</strong>
                                        {isRequired && <span className="required-badge" style={{ marginLeft: '4px' }}>*</span>}
                                      </div>
                                    </>
                                  )}
                                  {itemIdx > 0 && (
                                    <div style={{ fontSize: '12px', color: '#374151', paddingLeft: '16px' }}>
                                      <span style={{ fontSize: '10px', color: '#6366f1', marginRight: '4px' }}>└─</span>
                                      <strong>{camelCaseToLabel(selectedSecondary || 'Field')}</strong>
                                      {isRequired && <span className="required-badge" style={{ marginLeft: '4px' }}>*</span>}
                                    </div>
                                  )}
                                </td>
                                <td className="sample-value">
                                  {itemIdx === 0 && (
                                    <>
                                      {value !== null && value !== undefined && value !== '' 
                                        ? String(value).substring(0, 50) 
                                        : <span style={{ color: '#999', fontStyle: 'italic' }}>(empty)</span>}
                                      {value && String(value).length > 50 && '...'}
                                    </>
                                  )}
                                  {itemIdx > 0 && (
                                    <span style={{ color: '#999', fontStyle: 'italic', fontSize: '11px' }}>
                                      (split from above)
                                    </span>
                                  )}
                                </td>
                                <td>
                                  <Select
                                    value={primaryOptions.find(opt => opt.value === selectedPrimary) || null}
                                    onChange={(option) => {
                                      // Update all items in array with same primary + relationship hint per row
                                      const np = option?.value || '';
                                      const updatedArray = mappingItem.map((arrItem) => {
                                        const next = { ...arrItem, [headers.primaryKey]: np };
                                        let suggested = '';
                                        if (destination === 'sql' && effectivePrimaryTable && np) {
                                          suggested =
                                            suggestSqlLookupOf(
                                              effectivePrimaryTable,
                                              np,
                                              destinationSchema,
                                            ) || '';
                                        } else if (destination === 'salesforce' && primaryObject && np) {
                                          suggested =
                                            suggestSalesforceLookupOf(
                                              primaryObject,
                                              np,
                                              destinationSchema,
                                            ) || '';
                                        }
                                        return applyLookupOfToMappingEntry(next, suggested);
                                      });
                                      const newMapping = { ...mapping, [key]: updatedArray };
                                      applyPanelUpdate({ mapping: newMapping });
                                    }}
                                    options={primaryOptions}
                                    placeholder={`Select ${headers.primaryLabel.toLowerCase()}...`}
                                    isClearable
                                    menuPortalTarget={document.body}
                                    menuPosition="fixed"
                                    menuShouldScrollIntoView={false}
                                    styles={{
                                      menuPortal: (base) => ({ ...base, zIndex: 11050 }),
                                      menu: (base) => ({ ...base, zIndex: 11050 })
                                    }}
                                    className="inline-mapping-select"
                                    classNamePrefix="inline-mapping-select"
                                  />
                                </td>
                                <td>
                                  <Select
                                    value={allSecondaryOptions.find(opt => opt.value === selectedSecondary) || null}
                                    onChange={(option) => {
                                      const newValue = option?.value || '';
                                      const updatedArray = mappingItem.map((arrItem, arrIdx) => 
                                        arrIdx === itemIdx 
                                          ? {
                                              ...arrItem,
                                              [headers.secondaryKey]: newValue,
                                              is_custom: destination === 'salesforce' && newValue && !secondaryOptions.find(f => f.value === newValue)
                                            }
                                          : arrItem
                                      );
                                      const newMapping = { ...mapping, [key]: updatedArray };
                                      applyPanelUpdate({ mapping: newMapping });
                                    }}
                                    options={allSecondaryOptions}
                                    placeholder={`Select ${headers.secondaryLabel.toLowerCase()}...`}
                                    isClearable
                                    isSearchable
                                    menuPortalTarget={document.body}
                                    menuPosition="fixed"
                                    menuShouldScrollIntoView={false}
                                    styles={{
                                      menuPortal: (base) => ({ ...base, zIndex: 11050 }),
                                      menu: (base) => ({ ...base, zIndex: 11050 })
                                    }}
                                    className="inline-mapping-select"
                                    classNamePrefix="inline-mapping-select"
                                    isDisabled={!selectedPrimary}
                                  />
                                  {destination === 'salesforce' && isCustom && selectedSecondary && (
                                    <span className="custom-field-indicator" style={{ fontSize: '11px', color: '#666', display: 'block', marginTop: '4px' }}>
                                      Custom Field
                                    </span>
                                  )}
                                </td>
                                {showLookupRelationshipColumn && (
                                  <td className="inline-mapping-td-lookup">
                                    <input
                                      type="text"
                                      className="inline-mapping-lookup-input"
                                      list={
                                        destination === 'sql'
                                          ? `inline-mapping-lookup-dl-${idx}-${itemIdx}`
                                          : undefined
                                      }
                                      value={(item && item.lookup_of) || ''}
                                      onChange={(e) =>
                                        handleLookupOfChange(key, e.target.value, itemIdx)
                                      }
                                      placeholder={lookupRelationshipPlaceholder}
                                      title="FK or relationship path for this destination row"
                                    />
                                    {destination === 'sql' && effectivePrimaryTable && (
                                      <datalist id={`inline-mapping-lookup-dl-${idx}-${itemIdx}`}>
                                        {sqlRelationshipSelectOptions(
                                          effectivePrimaryTable,
                                          selectedPrimary,
                                          destinationSchema
                                        ).map((opt) => (
                                          <option key={opt} value={opt} />
                                        ))}
                                      </datalist>
                                    )}
                                  </td>
                                )}
                                <td>
                                  <input
                                    type="checkbox"
                                    checked={isDuplicateCheck}
                                    onChange={(e) => {
                                      const updatedArray = mappingItem.map((arrItem, arrIdx) => 
                                        arrIdx === itemIdx 
                                          ? { ...arrItem, is_duplicate_check: e.target.checked }
                                          : arrItem
                                      );
                                      const newMapping = { ...mapping, [key]: updatedArray };
                                      applyPanelUpdate({ mapping: newMapping });
                                    }}
                                    className="inline-mapping-checkbox"
                                  />
                                </td>
                              </tr>
                            );
                          })}
                        </React.Fragment>
                      );
                    }
                    
                    // Regular single mapping object (non-array)
                    const singleMappingItem = mappingItem || {};
                    const selectedPrimary = getMappingPrimary(singleMappingItem);
                    const selectedSecondary = getMappingSecondary(singleMappingItem);
                    const isCustom = singleMappingItem.is_custom;
                    const isDuplicateCheck = singleMappingItem.is_duplicate_check || false;
                    const isCombinedField = singleMappingItem.is_combined_field || false;
                    const combineGroup = singleMappingItem.combine_group;
                    const combineRule = findCombineRuleForSourceKey(transRules, key);
                    const combineSourcesLabel =
                      isCombinedField && combineRule
                        ? formatCombineSourcesLabel(combineRule.source_fields, combineRule.output_field)
                        : null;
                    
                    // Find other fields in the same combine group
                    const combinedFields = [];
                    if (isCombinedField && combineGroup) {
                      Object.entries(mapping || {}).forEach(([otherKey, otherMappingItem]) => {
                        if (otherKey !== key && 
                            !Array.isArray(otherMappingItem) && 
                            otherMappingItem?.is_combined_field && 
                            otherMappingItem?.combine_group === combineGroup) {
                          combinedFields.push({
                            key: otherKey,
                            mappingItem: otherMappingItem,
                            value: enhancedFirstRecord?.[otherKey]
                          });
                        }
                      });
                      // Sort by combine_order
                      combinedFields.sort((a, b) => {
                        const orderA = a.mappingItem.combine_order ?? 0;
                        const orderB = b.mappingItem.combine_order ?? 0;
                        return orderA - orderB;
                      });
                    }
                    
                    // Check if this is the first field in the combine group (lowest combine_order)
                    const isFirstInCombineGroup = isCombinedField && combineGroup && 
                      (singleMappingItem.combine_order ?? 0) === Math.min(
                        ...Object.values(mapping || {})
                          .filter(m => !Array.isArray(m) && m?.is_combined_field && m?.combine_group === combineGroup)
                          .map(m => m.combine_order ?? 0)
                      );

                    // Primary options (objects/sheets/tables)
                    const primaryOptions = Object.keys(destinationSchema).map((primaryKey) => ({
                      label: primaryKey,
                      value: primaryKey,
                    }));

                    // Get secondary options based on selected primary and destination type
                    let secondaryOptions = [];
                    if (selectedPrimary && destinationSchema[selectedPrimary]) {
                      if (destination === 'salesforce') {
                        // Salesforce: destinationSchema[selectedPrimary] is { "Email": {...}, "FirstName": {...} }
                        secondaryOptions = Object.keys(destinationSchema[selectedPrimary]).map((fieldName) => ({
                          label: fieldName,
                          value: fieldName,
                        }));
                      } else if (destination === 'google_sheets') {
                        // Google Sheets: destinationSchema[selectedPrimary] is { "columns": { "Email": {...} } }
                        const columns = destinationSchema[selectedPrimary].columns || {};
                        secondaryOptions = Object.keys(columns).map((colName) => ({
                          label: colName,
                          value: colName,
                        }));
                      } else if (destination === 'sql') {
                        // SQL: destinationSchema[selectedPrimary] is { "columns": { "email": {...} } }
                        const columns = destinationSchema[selectedPrimary].columns || {};
                        secondaryOptions = Object.keys(columns).map((colName) => ({
                          label: colName,
                          value: colName,
                        }));
                      }
                    }

                    // Custom field option for Salesforce only
                    const customFieldOption =
                      destination === 'salesforce' && 
                      isCustom && 
                      selectedSecondary && 
                      !secondaryOptions.find(f => f.value === selectedSecondary)
                        ? [{ label: selectedSecondary, value: selectedSecondary }]
                        : [];

                    const allSecondaryOptions = [...customFieldOption, ...secondaryOptions];

                    // If this is a combined field but not the first one, skip rendering (will be rendered as part of first field)
                    if (isCombinedField && !isFirstInCombineGroup) {
                      return null;
                    }
                    
                    return (
                      <React.Fragment key={idx}>
                        {/* Main row for first field in combine group or regular field */}
                        <tr style={{ backgroundColor: isCombinedField ? '#f0f9ff' : 'transparent' }}>
                          <td className="inline-mapping-td-select">
                            {!isCombinedField && isEligibleForCombine(key, mapping) && (
                              <input
                                type="checkbox"
                                checked={selectedCombineKeys.has(key)}
                                onChange={() => toggleCombineSelect(key)}
                                aria-label={`Select ${key} for combine`}
                                className="inline-mapping-checkbox"
                              />
                            )}
                          </td>
                          <td>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '4px', flexWrap: 'wrap' }}>
                              <strong>{camelCaseToLabel(key)}</strong>
                              {isCombinedField && (
                                <>
                                  <span style={{ fontSize: '10px', color: '#10b981', fontWeight: '500' }}>
                                    (combined)
                                  </span>
                                  {combineSourcesLabel && isFirstInCombineGroup && (
                                    <span
                                      style={{
                                        fontSize: '11px',
                                        color: '#0369a1',
                                        fontWeight: '500',
                                        width: '100%',
                                      }}
                                      title="These source fields are merged at migration time"
                                    >
                                      {combineSourcesLabel}
                                    </span>
                                  )}
                                  {combineGroup && (
                                    <button
                                      type="button"
                                      className="inline-mapping-transform-action"
                                      onClick={() => handleUncombine(combineGroup)}
                                      title="Map each source field independently"
                                    >
                                      Separate fields
                                    </button>
                                  )}
                                </>
                              )}
                              {!isCombinedField && isEligibleForSplit(key, mapping) && (
                                <button
                                  type="button"
                                  className="inline-mapping-transform-action"
                                  onClick={() => setSplitModalSourceKey(key)}
                                  title="Split this source field into two destination columns"
                                >
                                  Split field
                                </button>
                              )}
                              {mapping[key]?.is_required && <span className="required-badge">*</span>}
                            </div>
                          </td>
                          <td className="sample-value">
                            {value !== null && value !== undefined && value !== '' 
                              ? String(value).substring(0, 50) 
                              : <span style={{ color: '#999', fontStyle: 'italic' }}>(empty)</span>}
                            {value && String(value).length > 50 && '...'}
                          </td>
                        <td>
                          <Select
                            value={primaryOptions.find(opt => opt.value === selectedPrimary) || null}
                            onChange={(option) => handleMappingChange(key, headers.primaryKey, option?.value || '')}
                            options={primaryOptions}
                            placeholder={`Select ${headers.primaryLabel.toLowerCase()}...`}
                            isClearable
                            menuPortalTarget={document.body}
                            menuPosition="fixed"
                            menuShouldScrollIntoView={false}
                            styles={{
                              menuPortal: (base) => ({ ...base, zIndex: 11050 }),
                              menu: (base) => ({ ...base, zIndex: 11050 })
                            }}
                            className="inline-mapping-select"
                            classNamePrefix="inline-mapping-select"
                          />
                        </td>
                        <td>
                          <Select
                            value={allSecondaryOptions.find(opt => opt.value === selectedSecondary) || null}
                            onChange={(option) => {
                              const newValue = option?.value || '';
                              const previousItem = mapping[key] || {};
                              
                              // Update both field and is_custom in a single state update
                              const updatedItem = {
                                ...previousItem,
                                [headers.secondaryKey]: newValue,
                                is_custom: destination === 'salesforce' && newValue && !secondaryOptions.find(f => f.value === newValue)
                              };
                              
                              const newMapping = {
                                ...mapping,
                                [key]: updatedItem,
                              };
                              
                              applyPanelUpdate({ mapping: newMapping });
                            }}
                            options={allSecondaryOptions}
                            placeholder={`Select ${headers.secondaryLabel.toLowerCase()}...`}
                            isClearable
                            isSearchable
                            menuPortalTarget={document.body}
                            menuPosition="fixed"
                            menuShouldScrollIntoView={false}
                            styles={{
                              menuPortal: (base) => ({ ...base, zIndex: 11050 }),
                              menu: (base) => ({ ...base, zIndex: 11050 })
                            }}
                            className="inline-mapping-select"
                            classNamePrefix="inline-mapping-select"
                            isDisabled={!selectedPrimary}
                          />
                          {destination === 'salesforce' && isCustom && selectedSecondary && (
                            <span className="custom-field-indicator" style={{ fontSize: '11px', color: '#666', display: 'block', marginTop: '4px' }}>
                              Custom Field
                            </span>
                          )}
                        </td>
                        {showLookupRelationshipColumn && (
                          <td className="inline-mapping-td-lookup">
                            <input
                              type="text"
                              className="inline-mapping-lookup-input"
                              list={
                                destination === 'sql'
                                  ? `inline-mapping-lookup-dl-row-${idx}`
                                  : undefined
                              }
                              value={(singleMappingItem && singleMappingItem.lookup_of) || ''}
                              onChange={(e) =>
                                handleMappingChange(key, 'lookup_of', e.target.value)
                              }
                              placeholder={lookupRelationshipPlaceholder}
                              title="Relationship path for this mapping row"
                            />
                            {destination === 'sql' && effectivePrimaryTable && (
                              <datalist id={`inline-mapping-lookup-dl-row-${idx}`}>
                                {sqlRelationshipSelectOptions(
                                  effectivePrimaryTable,
                                  selectedPrimary,
                                  destinationSchema
                                ).map((opt) => (
                                  <option key={opt} value={opt} />
                                ))}
                              </datalist>
                            )}
                          </td>
                        )}
                        <td>
                          <input
                            type="checkbox"
                            checked={isDuplicateCheck}
                            onChange={(e) => handleMappingChange(key, 'is_duplicate_check', e.target.checked)}
                            className="inline-mapping-checkbox"
                          />
                        </td>
                      </tr>
                      {/* Additional rows for other fields in the combine group */}
                      {isCombinedField && combinedFields.map((combinedField, combinedIdx) => {
                        const combinedKey = combinedField.key;
                        const combinedMappingItem = combinedField.mappingItem;
                        const combinedValue = combinedField.value;
                        const combinedSelectedPrimary = getMappingPrimary(combinedMappingItem);
                        const combinedSelectedSecondary = getMappingSecondary(combinedMappingItem);
                        const combinedIsCustom = combinedMappingItem.is_custom;
                        const combinedIsDuplicateCheck = combinedMappingItem.is_duplicate_check || false;
                        
                        // Get secondary options for combined field
                        let combinedSecondaryOptions = [];
                        if (combinedSelectedPrimary && destinationSchema[combinedSelectedPrimary]) {
                          if (destination === 'salesforce') {
                            combinedSecondaryOptions = Object.keys(destinationSchema[combinedSelectedPrimary]).map((fieldName) => ({
                              label: fieldName,
                              value: fieldName,
                            }));
                          } else if (destination === 'google_sheets') {
                            const columns = destinationSchema[combinedSelectedPrimary].columns || {};
                            combinedSecondaryOptions = Object.keys(columns).map((colName) => ({
                              label: colName,
                              value: colName,
                            }));
                          } else if (destination === 'sql') {
                            const columns = destinationSchema[combinedSelectedPrimary].columns || {};
                            combinedSecondaryOptions = Object.keys(columns).map((colName) => ({
                              label: colName,
                              value: colName,
                            }));
                          }
                        }
                        
                        const combinedCustomFieldOption =
                          destination === 'salesforce' && 
                          combinedIsCustom && 
                          combinedSelectedSecondary && 
                          !combinedSecondaryOptions.find(f => f.value === combinedSelectedSecondary)
                            ? [{ label: combinedSelectedSecondary, value: combinedSelectedSecondary }]
                            : [];
                        
                        const allCombinedSecondaryOptions = [...combinedCustomFieldOption, ...combinedSecondaryOptions];
                        
                        return (
                          <tr key={`${idx}-combined-${combinedIdx}`} style={{ backgroundColor: '#f0f9ff', borderTop: '1px solid #e5e7eb' }}>
                            <td className="inline-mapping-td-select" />
                            <td>
                              <div style={{ fontSize: '12px', color: '#374151', paddingLeft: '16px' }}>
                                <span style={{ fontSize: '10px', color: '#10b981', marginRight: '4px' }}>└─</span>
                                <strong>{camelCaseToLabel(combinedKey)}</strong>
                                <span style={{ fontSize: '10px', color: '#10b981', fontWeight: '500', marginLeft: '4px' }}>
                                  (combined)
                                </span>
                                {mapping[combinedKey]?.is_required && <span className="required-badge" style={{ marginLeft: '4px' }}>*</span>}
                              </div>
                            </td>
                            <td className="sample-value">
                              {combinedValue !== null && combinedValue !== undefined && combinedValue !== '' 
                                ? String(combinedValue).substring(0, 50) 
                                : <span style={{ color: '#999', fontStyle: 'italic' }}>(empty)</span>}
                              {combinedValue && String(combinedValue).length > 50 && '...'}
                            </td>
                            <td>
                              <Select
                                value={primaryOptions.find(opt => opt.value === combinedSelectedPrimary) || null}
                                onChange={(option) => handleMappingChange(combinedKey, headers.primaryKey, option?.value || '')}
                                options={primaryOptions}
                                placeholder={`Select ${headers.primaryLabel.toLowerCase()}...`}
                                isClearable
                                menuPortalTarget={document.body}
                                menuPosition="fixed"
                                menuShouldScrollIntoView={false}
                                styles={{
                                  menuPortal: (base) => ({ ...base, zIndex: 11050 }),
                                  menu: (base) => ({ ...base, zIndex: 11050 })
                                }}
                                className="inline-mapping-select"
                                classNamePrefix="inline-mapping-select"
                              />
                            </td>
                            <td>
                              <Select
                                value={allCombinedSecondaryOptions.find(opt => opt.value === combinedSelectedSecondary) || null}
                                onChange={(option) => {
                                  const newValue = option?.value || '';
                                  const previousItem = mapping[combinedKey] || {};
                                  
                                  const updatedItem = {
                                    ...previousItem,
                                    [headers.secondaryKey]: newValue,
                                    is_custom: destination === 'salesforce' && newValue && !combinedSecondaryOptions.find(f => f.value === newValue)
                                  };
                                  
                                  const newMapping = {
                                    ...mapping,
                                    [combinedKey]: updatedItem,
                                  };
                                  
                                  applyPanelUpdate({ mapping: newMapping });
                                }}
                                options={allCombinedSecondaryOptions}
                                placeholder={`Select ${headers.secondaryLabel.toLowerCase()}...`}
                                isClearable
                                isSearchable
                                menuPortalTarget={document.body}
                                menuPosition="fixed"
                                menuShouldScrollIntoView={false}
                                styles={{
                                  menuPortal: (base) => ({ ...base, zIndex: 11050 }),
                                  menu: (base) => ({ ...base, zIndex: 11050 })
                                }}
                                className="inline-mapping-select"
                                classNamePrefix="inline-mapping-select"
                                isDisabled={!combinedSelectedPrimary}
                              />
                              {destination === 'salesforce' && combinedIsCustom && combinedSelectedSecondary && (
                                <span className="custom-field-indicator" style={{ fontSize: '11px', color: '#666', display: 'block', marginTop: '4px' }}>
                                  Custom Field
                                </span>
                              )}
                            </td>
                            {showLookupRelationshipColumn && (
                              <td className="inline-mapping-td-lookup">
                                <input
                                  type="text"
                                  className="inline-mapping-lookup-input"
                                  list={
                                    destination === 'sql'
                                      ? `inline-mapping-lookup-dl-comb-${idx}-${combinedIdx}`
                                      : undefined
                                  }
                                  value={(combinedMappingItem && combinedMappingItem.lookup_of) || ''}
                                  onChange={(e) =>
                                    handleMappingChange(combinedKey, 'lookup_of', e.target.value)
                                  }
                                  placeholder={lookupRelationshipPlaceholder}
                                  title="Relationship path for this mapping row"
                                />
                                {destination === 'sql' && effectivePrimaryTable && (
                                  <datalist id={`inline-mapping-lookup-dl-comb-${idx}-${combinedIdx}`}>
                                    {sqlRelationshipSelectOptions(
                                      effectivePrimaryTable,
                                      combinedSelectedPrimary,
                                      destinationSchema
                                    ).map((opt) => (
                                      <option key={opt} value={opt} />
                                    ))}
                                  </datalist>
                                )}
                              </td>
                            )}
                            <td>
                              <input
                                type="checkbox"
                                checked={combinedIsDuplicateCheck}
                                onChange={(e) => {
                                  const updatedItem = {
                                    ...mapping[combinedKey],
                                    is_duplicate_check: e.target.checked
                                  };
                                  const newMapping = {
                                    ...mapping,
                                    [combinedKey]: updatedItem,
                                  };
                                  applyPanelUpdate({ mapping: newMapping });
                                }}
                                className="inline-mapping-checkbox"
                              />
                            </td>
                          </tr>
                        );
                      })}
                    </React.Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div className="inline-mapping-panel-footer inline-mapping-panel-footer-stack">
              {hasMissingRequiredFields && (
                <div
                  style={{
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '0.5rem',
                    marginBottom: '0.5rem',
                  }}
                >
                  <div style={{ fontSize: '13px', color: '#dc2626', marginBottom: '0.25rem', fontWeight: '500' }}>
                    ⚠️ {recordsWithMissingRequiredFields.length} record{recordsWithMissingRequiredFields.length !== 1 ? 's' : ''} will fail to migrate because {recordsWithMissingRequiredFields.length === 1 ? 'it is' : 'they are'} missing required field{recordsWithMissingRequiredFields.length === 1 ? '' : 's'}
                  </div>
                  <div style={{ fontSize: '12px', color: '#666', marginBottom: '0.5rem' }}>
                    Choose how to handle these records:
                  </div>
                  <div style={{ display: 'flex', gap: '1rem', alignItems: 'center', flexWrap: 'wrap' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                      <input
                        type="radio"
                        name={`missingFieldOption-${mappingId}`}
                        id={`missing-placeholder-${mappingId}`}
                        value="placeholder"
                        checked={missingFieldOption === 'placeholder'}
                        onChange={(e) => handleMissingFieldChoiceChange(e.target.value)}
                      />
                      <label htmlFor={`missing-placeholder-${mappingId}`} style={{ fontSize: '14px', cursor: 'pointer' }}>
                        Fill missing required fields with placeholder:
                      </label>
                      <input
                        type="text"
                        id={`missing-placeholder-input-${mappingId}`}
                        value={placeholderValue}
                        onChange={(e) => handlePlaceholderValueChange(e.target.value)}
                        placeholder="Unknown"
                        style={{
                          padding: '4px 8px',
                          fontSize: '13px',
                          border: '1px solid #ddd',
                          borderRadius: '4px',
                          width: '120px'
                        }}
                        disabled={missingFieldOption !== 'placeholder'}
                      />
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                      <input
                        type="radio"
                        name={`missingFieldOption-${mappingId}`}
                        id={`missing-skip-${mappingId}`}
                        value="skip"
                        checked={missingFieldOption === 'skip'}
                        onChange={(e) => handleMissingFieldChoiceChange(e.target.value)}
                      />
                      <label htmlFor={`missing-skip-${mappingId}`} style={{ fontSize: '14px', cursor: 'pointer' }}>
                        Skip records with missing required fields
                      </label>
                    </div>
                  </div>
                  {choiceSavedMessage && (
                    <div style={{ fontSize: '12px', color: '#10b981', marginTop: '0.25rem' }}>
                      {choiceSavedMessage}
                    </div>
                  )}
                </div>
              )}

              {destination === 'salesforce' && hasOrgRulesContent && (
                <details className="inline-mapping-org-rules-details">
                  <summary className="inline-mapping-org-rules-summary">
                    Salesforce rules
                    <span className="inline-mapping-org-rules-summary-meta">
                      ({orgRulesBlocks.map((b) => b.objectApiName).join(', ')})
                    </span>
                  </summary>
                  <div className="inline-mapping-org-rules-scroll">
                    {orgRulesBlocks.map((block) => (
                      <div key={block.objectApiName} className="inline-mapping-org-rules-compact">
                        <div className="inline-mapping-org-rules-object-title">
                          {destinationName || 'Salesforce'} · {block.objectApiName}
                        </div>
                        {block.validationLines.length > 0 && (
                          <div className="inline-mapping-org-rules-block">
                            <div className="inline-mapping-org-rules-label">Validation</div>
                            <ul className="inline-mapping-org-rules-list">
                              {block.validationLines.map((line, idx) => (
                                <li key={`vr-${block.objectApiName}-${idx}`}>{line}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {block.duplicateLines.length > 0 && (
                          <div className="inline-mapping-org-rules-block">
                            <div className="inline-mapping-org-rules-label">Duplicate</div>
                            <ul className="inline-mapping-org-rules-list">
                              {block.duplicateLines.map((line, idx) => (
                                <li key={`dr-${block.objectApiName}-${idx}`}>{line}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {block.matchingLines.length > 0 && (
                          <div className="inline-mapping-org-rules-block">
                            <div className="inline-mapping-org-rules-label">Matching</div>
                            <ul className="inline-mapping-org-rules-list">
                              {block.matchingLines.map((line, idx) => (
                                <li key={`mr-${block.objectApiName}-${idx}`}>{line}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </details>
              )}

              <div className="inline-mapping-panel-footer-actions">
                {showTransformActions && (
                  <button
                    type="button"
                    onClick={handleClearAllTransformations}
                    className="inline-mapping-panel-btn inline-mapping-clear-transforms-btn"
                    disabled={isTransforming || isStarting || detailExpired}
                    title="Remove all combine and split transformations"
                  >
                    Clear transformations
                  </button>
                )}
                {jobId && transRules?.fields?.length > 0 && (
                  <button
                    onClick={handlePreviewTransformation}
                    className="inline-mapping-panel-btn"
                    disabled={isTransforming || isStarting || detailExpired}
                    title={
                      detailExpired
                        ? 'Extracted rows were removed (retention). Run extraction again before transforming.'
                        : undefined
                    }
                    style={{ backgroundColor: '#6366f1', color: 'white' }}
                  >
                    {isTransforming ? 'Transforming...' : 'Preview Transformation'}
                  </button>
                )}
                <button
                  ref={startMigrationButtonRef}
                  onClick={handleStartMigration}
                  className="inline-mapping-panel-btn primary"
                  disabled={isStarting || isTransforming || detailExpired}
                  title={
                    detailExpired
                      ? 'Extracted rows were removed (retention). Run extraction again, then start migration.'
                      : undefined
                  }
                >
                  {isStarting ? 'Starting Migration...' : 'Start Migration'}
                </button>
              </div>

              {/* Transformation Error */}
              {transformationError && (
                <div style={{ 
                  marginTop: '1rem', 
                  padding: '0.75rem', 
                  backgroundColor: '#fee2e2', 
                  border: '1px solid #ef4444',
                  borderRadius: '4px',
                  color: '#991b1b'
                }}>
                  <strong>Transformation Error:</strong> {transformationError}
                </div>
              )}
            </div>
          </div>
        )}
      <CombineFieldsModal
        isOpen={combineModalOpen}
        onClose={() => setCombineModalOpen(false)}
        onConfirm={handleCombineConfirm}
        selectedKeys={Array.from(selectedCombineKeys)}
        mapping={mapping}
        destination={destination}
        destinationSchema={destinationSchema}
        primaryLabel={headers.primaryLabel}
        secondaryLabel={headers.secondaryLabel}
      />
      <SplitFieldModal
        isOpen={!!splitModalSourceKey}
        onClose={() => setSplitModalSourceKey(null)}
        onConfirm={handleSplitConfirm}
        sourceKey={splitModalSourceKey}
        mapping={mapping}
        destination={destination}
        destinationSchema={destinationSchema}
        primaryLabel={headers.primaryLabel}
        secondaryLabel={headers.secondaryLabel}
      />
    </div>
  );
};

export default InlineMappingPanel;

