import React, { useEffect, useMemo, useState } from 'react';
import SemanticMappingsEditor from './SemanticMappingsEditor';
import ProposalVersionPicker from './ProposalVersionPicker';
import { useLakeProposal } from '../hooks/useLakeProposal';
import { useWarehouseProposal } from '../hooks/useWarehouseProposal';
import {
  LakeBronzeTableList,
  LakeGapsList,
  LakeLineageTable,
  LakeProposalOverview,
  LakeProposalToolbar,
  LakeSilverViewList,
} from './lake/LakeProposalParts';
import {
  WarehouseGapsList,
  WarehouseLineageTable,
  WarehouseProposalOverview,
  WarehouseProposalToolbar,
  WarehouseTableList,
} from './warehouse/WarehouseProposalParts';
import './AssessmentDashboard.css';
import '../EnterpriseGraphsPage.css';

const MAIN_TABS = {
  mappings: 'mappings',
  lake: 'lake',
  warehouse: 'warehouse',
};

const SUB_TABS = {
  schema: 'schema',
  gaps: 'gaps',
  logical: 'logical',
  lineage: 'lineage',
};

function countLakeLineageRows(sourceMap) {
  let n = 0;
  (sourceMap?.mappings || []).forEach((m) => {
    n += Object.keys(m.column_map || {}).length;
  });
  return n;
}

function ProposalSubTabs({ tabs, active, onChange }) {
  return (
    <nav className="eg-results-subtabs-nav" role="tablist" aria-label="Proposal sections">
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          role="tab"
          aria-selected={active === t.id}
          className={`eg-results-subtab${active === t.id ? ' eg-results-subtab--active' : ''}`}
          onClick={() => onChange(t.id)}
        >
          {t.label}
        </button>
      ))}
    </nav>
  );
}

export default function EnterpriseGraphResultsTabs({
  graphId,
  lakeEnabled = false,
  proposalRefreshKey = 0,
  onMappingsChanged,
}) {
  const [mainTab, setMainTab] = useState(MAIN_TABS.mappings);
  const [lakeSubTab, setLakeSubTab] = useState(SUB_TABS.schema);
  const [warehouseSubTab, setWarehouseSubTab] = useState(SUB_TABS.schema);
  const [warehouseLogicalFilter, setWarehouseLogicalFilter] = useState('all');
  const [showWarehouseDdl, setShowWarehouseDdl] = useState(false);
  const [showLakeDdl, setShowLakeDdl] = useState(false);

  const {
    proposal: lakeProposal,
    loading: lakeLoading,
    notFound: lakeNotFound,
    error: lakeError,
    approving: lakeApproving,
    redesigning: lakeRedesigning,
    executing: lakeExecuting,
    approve: lakeApprove,
    redesign: lakeRedesign,
    executeDdl: lakeExecuteDdl,
    exportLineage: lakeExportLineage,
    loadProposal: loadLakeProposal,
    versions: lakeVersions,
    selectedVersionId: lakeSelectedVersionId,
    selectVersion: selectLakeVersion,
  } = useLakeProposal(graphId);

  const {
    proposal: warehouseProposal,
    loading: warehouseLoading,
    error: warehouseError,
    approving: warehouseApproving,
    redesigning: warehouseRedesigning,
    executing: warehouseExecuting,
    approve: warehouseApprove,
    redesign: warehouseRedesign,
    executeDdl: warehouseExecuteDdl,
    exportJson: warehouseExportJson,
    loadProposal: loadWarehouseProposal,
    versions: warehouseVersions,
    selectedVersionId: warehouseSelectedVersionId,
    selectVersion: selectWarehouseVersion,
  } = useWarehouseProposal(graphId);

  useEffect(() => {
    if (proposalRefreshKey > 0) {
      loadLakeProposal();
      loadWarehouseProposal();
    }
  }, [proposalRefreshKey, loadLakeProposal, loadWarehouseProposal]);

  const warehouseGaps = warehouseProposal?.gaps || [];
  const warehouseTables = warehouseProposal?.logical_model?.tables || [];
  const warehouseLineage = warehouseProposal?.source_to_warehouse_map?.mappings || [];
  const lakeBronze = lakeProposal?.logical_model?.bronze_tables || [];
  const lakeSilver = lakeProposal?.logical_model?.silver_views || [];
  const lakeGaps = lakeProposal?.gaps || [];
  const lakeLineageCount = countLakeLineageRows(lakeProposal?.source_to_lake_map);
  const showLakeTab = lakeEnabled || (!lakeNotFound && (lakeLoading || Boolean(lakeProposal)));

  const mainTabs = useMemo(
    () => [
      { id: MAIN_TABS.mappings, label: 'Cross-connector mappings' },
      ...(showLakeTab
        ? [
            {
              id: MAIN_TABS.lake,
              label: lakeBronze.length
                ? `Lake proposal (${lakeBronze.length} bronze)`
                : 'Lake proposal',
            },
          ]
        : []),
      { id: MAIN_TABS.warehouse, label: 'Warehouse schema proposal' },
    ],
    [showLakeTab, lakeBronze.length],
  );

  const lakeSubTabs = useMemo(
    () => [
      { id: SUB_TABS.schema, label: 'Schema proposal' },
      { id: SUB_TABS.gaps, label: lakeGaps.length ? `Gaps (${lakeGaps.length})` : 'Gaps' },
      {
        id: SUB_TABS.logical,
        label:
          lakeBronze.length || lakeSilver.length
            ? `Logical model (${lakeBronze.length + lakeSilver.length})`
            : 'Logical model',
      },
      {
        id: SUB_TABS.lineage,
        label: lakeLineageCount ? `Column lineage (${lakeLineageCount})` : 'Column lineage',
      },
    ],
    [lakeGaps.length, lakeBronze.length, lakeSilver.length, lakeLineageCount],
  );

  const warehouseSubTabs = useMemo(
    () => [
      { id: SUB_TABS.schema, label: 'Schema proposal' },
      {
        id: SUB_TABS.gaps,
        label: warehouseGaps.length ? `Gaps (${warehouseGaps.length})` : 'Gaps',
      },
      {
        id: SUB_TABS.logical,
        label: warehouseTables.length
          ? `Logical model (${warehouseTables.length})`
          : 'Logical model',
      },
      {
        id: SUB_TABS.lineage,
        label: warehouseLineage.length
          ? `Column lineage (${warehouseLineage.length})`
          : 'Column lineage',
      },
    ],
    [warehouseGaps.length, warehouseTables.length, warehouseLineage.length],
  );

  const renderLakePanel = () => (
    <div className="eg-results-tab-inner">
      <div className="eg-mappings-head">
        <p className="eg-wizard-hint" style={{ margin: 0, flex: 1 }}>
          Medallion lake layout: bronze landing tables and silver staging views.
        </p>
        <ProposalVersionPicker
          versions={lakeVersions}
          selectedId={lakeSelectedVersionId}
          onSelect={selectLakeVersion}
          disabled={lakeLoading || lakeRedesigning}
          label="Lake version"
        />
        <LakeProposalToolbar
          proposal={lakeProposal}
          approving={lakeApproving}
          redesigning={lakeRedesigning}
          executing={lakeExecuting}
          onRedesign={lakeRedesign}
          onExportLineage={lakeExportLineage}
          onApprove={lakeApprove}
          onExecuteDdl={lakeExecuteDdl}
        />
      </div>
      {lakeError && <div className="eg-error">{lakeError}</div>}
      <ProposalSubTabs tabs={lakeSubTabs} active={lakeSubTab} onChange={setLakeSubTab} />
      {lakeLoading ? (
        <p className="eg-wizard-hint">Loading lake proposal…</p>
      ) : (
        <>
          {lakeSubTab === SUB_TABS.schema && (
            <LakeProposalOverview
              proposal={lakeProposal}
              showDdl={showLakeDdl}
              onToggleDdl={() => setShowLakeDdl((v) => !v)}
            />
          )}
          {lakeSubTab === SUB_TABS.gaps && (
            <>
              <p className="eg-wizard-hint">
                Issues to review before trusting the lake layout (unmapped sources, missing silver views).
              </p>
              <LakeGapsList gaps={lakeGaps} />
            </>
          )}
          {lakeSubTab === SUB_TABS.logical && (
            <>
              <h3 className="eg-results-subhead">Bronze tables (raw landing)</h3>
              <LakeBronzeTableList tables={lakeBronze} />
              <h3 className="eg-results-subhead">Silver views (conformed)</h3>
              <LakeSilverViewList views={lakeSilver} />
            </>
          )}
          {lakeSubTab === SUB_TABS.lineage && (
            <>
              <p className="eg-wizard-hint">
                How source columns map into proposed bronze tables.
              </p>
              <LakeLineageTable sourceMap={lakeProposal?.source_to_lake_map} />
            </>
          )}
        </>
      )}
    </div>
  );

  const renderWarehousePanel = () => (
    <div className="eg-results-tab-inner">
      <div className="eg-mappings-head">
        <p className="eg-wizard-hint" style={{ margin: 0, flex: 1 }}>
          Star-schema proposal and DDL preview for your unified canonical model.
        </p>
        <ProposalVersionPicker
          versions={warehouseVersions}
          selectedId={warehouseSelectedVersionId}
          onSelect={selectWarehouseVersion}
          disabled={warehouseLoading || warehouseRedesigning}
          label="Warehouse version"
        />
        <WarehouseProposalToolbar
          proposal={warehouseProposal}
          approving={warehouseApproving}
          redesigning={warehouseRedesigning}
          executing={warehouseExecuting}
          onRedesign={warehouseRedesign}
          onExport={warehouseExportJson}
          onApprove={warehouseApprove}
          onExecuteDdl={warehouseExecuteDdl}
        />
      </div>
      {warehouseError && <div className="eg-error">{warehouseError}</div>}
      <ProposalSubTabs tabs={warehouseSubTabs} active={warehouseSubTab} onChange={setWarehouseSubTab} />
      {warehouseLoading ? (
        <p className="eg-wizard-hint">Loading warehouse proposal…</p>
      ) : (
        <>
          {warehouseSubTab === SUB_TABS.schema && (
            <WarehouseProposalOverview
              proposal={warehouseProposal}
              showDdl={showWarehouseDdl}
              onToggleDdl={() => setShowWarehouseDdl((v) => !v)}
            />
          )}
          {warehouseSubTab === SUB_TABS.gaps && (
            <>
              <p className="eg-wizard-hint">
                Issues to review before trusting the warehouse design (unmapped entities, sparse columns).
              </p>
              <WarehouseGapsList gaps={warehouseGaps} />
            </>
          )}
          {warehouseSubTab === SUB_TABS.logical && (
            <>
              <div className="eg-logical-filters">
                <span className="eg-wizard-hint">Show:</span>
                {['all', 'fact', 'dimension'].map((f) => (
                  <button
                    key={f}
                    type="button"
                    className={`eg-filter-chip${warehouseLogicalFilter === f ? ' eg-filter-chip--active' : ''}`}
                    onClick={() => setWarehouseLogicalFilter(f)}
                  >
                    {f === 'all' ? 'All tables' : f === 'fact' ? 'Facts' : 'Dimensions'}
                  </button>
                ))}
              </div>
              <WarehouseTableList tables={warehouseTables} filter={warehouseLogicalFilter} />
            </>
          )}
          {warehouseSubTab === SUB_TABS.lineage && (
            <>
              <p className="eg-wizard-hint">
                How source columns map into proposed warehouse tables.
              </p>
              <WarehouseLineageTable lineage={warehouseLineage} pageSize={50} />
            </>
          )}
        </>
      )}
    </div>
  );

  return (
    <div className="eg-results-tabs">
      <div className="eg-wizard-card eg-results-tabs-card">
        <nav className="assessment-dashboard-tabs eg-results-tabs-nav" role="tablist">
          {mainTabs.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={mainTab === t.id}
              className={`assessment-dashboard-tab${mainTab === t.id ? ' active' : ''}`}
              onClick={() => setMainTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </nav>

        <div className="eg-results-tab-panel" role="tabpanel">
          {mainTab === MAIN_TABS.mappings && (
            <SemanticMappingsEditor
              graphId={graphId}
              embedded
              onMappingsChanged={onMappingsChanged}
            />
          )}

          {mainTab === MAIN_TABS.lake && showLakeTab && renderLakePanel()}

          {mainTab === MAIN_TABS.warehouse && renderWarehousePanel()}
        </div>
      </div>
    </div>
  );
}
