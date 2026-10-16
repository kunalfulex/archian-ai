import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { FiDatabase, FiLink, FiTrash2, FiX } from 'react-icons/fi';
import GoogleSheetsNodeScope from './GoogleSheetsNodeScope';
import { connectorIconPath } from '../utils/enterpriseGraphConnectors';

const PALETTE_MIME = 'application/x-edison-graph-connector';
const NODE_W = 200;
const NODE_TOTAL_H = 88;

const LINK_TYPES = [
  { value: 'cross_map', label: 'Cross-map', hint: 'Link two sources for entity mapping' },
  { value: 'lake_load', label: 'Load to lake', hint: 'Send data from source into lake (bronze)' },
  { value: 'warehouse_load', label: 'Load to warehouse', hint: 'Send data from source into warehouse' },
];

function isSnowflakeOpt(opt) {
  return (opt?.paletteRole === 'snowflake') || (opt?.subtitle || '').toLowerCase().includes('snowflake');
}

function roleLabel(role) {
  if (role === 'warehouse') return 'Warehouse';
  if (role === 'lake') return 'Lake';
  return 'Source';
}

function newId(prefix) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

function defaultPosition(canvasNodes, role) {
  const n = canvasNodes.length;
  const col = role === 'warehouse' ? 3 : n % 3;
  const row = role === 'warehouse' ? 0 : Math.floor(n / 3);
  return { x: 40 + col * (NODE_W + 36), y: 40 + row * (NODE_TOTAL_H + 28) };
}

function ConnectorIcon({ opt, size = 28 }) {
  const src = connectorIconPath(opt);
  if (src) {
    return (
      <img
        src={src}
        alt=""
        className="eg-canvas-node-icon-img"
        style={{ width: size, height: size, objectFit: 'contain' }}
        onError={(e) => {
          e.currentTarget.style.display = 'none';
        }}
      />
    );
  }
  return <FiDatabase className="eg-canvas-node-icon-fallback" aria-hidden />;
}

/** Center of top (in) or bottom (out) connection handle */
function portCenter(node, side) {
  const cx = node.x + NODE_W / 2;
  if (side === 'out') return { x: cx, y: node.y + NODE_TOTAL_H };
  return { x: cx, y: node.y };
}

function isValidTarget(fromNode, toNode, linkType) {
  if (!fromNode || !toNode || fromNode.id === toNode.id) return false;
  if (linkType === 'cross_map') {
    return fromNode.role === 'source' && toNode.role === 'source';
  }
  if (linkType === 'lake_load') {
    return (
      (fromNode.role === 'source' && toNode.role === 'lake') ||
      (fromNode.role === 'lake' && toNode.role === 'source')
    );
  }
  if (linkType === 'warehouse_load') {
    return (
      (fromNode.role === 'source' && toNode.role === 'warehouse') ||
      (fromNode.role === 'warehouse' && toNode.role === 'source')
    );
  }
  return false;
}

/**
 * Free-form landscape canvas: drag connectors anywhere, draw edges between nodes.
 */
export default function EnterpriseGraphCanvasPicker({
  connectorOptions,
  canvasNodes,
  onCanvasNodesChange,
  canvasEdges = [],
  onCanvasEdgesChange,
  lakeEnabled = false,
}) {
  const canvasRef = useRef(null);
  const canvasNodesRef = useRef(canvasNodes);
  const canvasEdgesRef = useRef(canvasEdges);
  const linkTypeRef = useRef('cross_map');
  const linkDragRef = useRef(null);
  const suppressCanvasClickRef = useRef(false);

  const [dragOverCanvas, setDragOverCanvas] = useState(false);
  const [linkType, setLinkType] = useState('cross_map');
  const [pendingFrom, setPendingFrom] = useState(null);
  const [draggingNodeId, setDraggingNodeId] = useState(null);
  const [linkDrag, setLinkDrag] = useState(null);
  const [hoverTargetId, setHoverTargetId] = useState(null);
  const dragOffset = useRef({ x: 0, y: 0 });

  useEffect(() => {
    canvasNodesRef.current = canvasNodes;
  }, [canvasNodes]);

  useEffect(() => {
    canvasEdgesRef.current = canvasEdges;
  }, [canvasEdges]);

  useEffect(() => {
    linkTypeRef.current = linkType;
  }, [linkType]);

  const optionByKey = useMemo(() => {
    const m = new Map();
    connectorOptions.forEach((o) => m.set(o.key, o));
    return m;
  }, [connectorOptions]);

  const usedKeys = useMemo(() => new Set(canvasNodes.map((n) => n.optionKey)), [canvasNodes]);

  const nodeById = useMemo(() => new Map(canvasNodes.map((n) => [n.id, n])), [canvasNodes]);

  const hasWarehouse = canvasNodes.some((n) => n.role === 'warehouse');
  const hasLake = canvasNodes.some((n) => n.role === 'lake');

  const updateNodes = useCallback(
    (next) => onCanvasNodesChange(next),
    [onCanvasNodesChange],
  );

  const updateEdges = useCallback(
    (next) => {
      if (typeof next === 'function') {
        onCanvasEdgesChange?.(next);
      } else {
        onCanvasEdgesChange?.(next);
      }
    },
    [onCanvasEdgesChange],
  );

  const canvasCoordsFromClient = useCallback((clientX, clientY) => {
    const el = canvasRef.current;
    if (!el) return { x: 0, y: 0 };
    const rect = el.getBoundingClientRect();
    return {
      x: clientX - rect.left + (el.scrollLeft || 0),
      y: clientY - rect.top + (el.scrollTop || 0),
    };
  }, []);

  const findNodeAtClient = useCallback((clientX, clientY) => {
    const pt = canvasCoordsFromClient(clientX, clientY);
    const nodes = canvasNodesRef.current;
    for (let i = nodes.length - 1; i >= 0; i -= 1) {
      const n = nodes[i];
      if (
        pt.x >= n.x &&
        pt.x <= n.x + NODE_W &&
        pt.y >= n.y &&
        pt.y <= n.y + NODE_TOTAL_H
      ) {
        return n.id;
      }
    }
    return null;
  }, [canvasCoordsFromClient]);

  const resolveSnowflakeRole = useCallback(
    (opt) => {
      if (!isSnowflakeOpt(opt)) return 'source';
      if (lakeEnabled && !hasLake) return 'lake';
      if (!hasWarehouse) return 'warehouse';
      return null;
    },
    [hasLake, hasWarehouse, lakeEnabled],
  );

  const addNodeAt = useCallback(
    (opt, role, x, y) => {
      if (!opt || usedKeys.has(opt.key)) return;
      let resolvedRole = role;
      if (isSnowflakeOpt(opt)) {
        const sfRole = resolveSnowflakeRole(opt);
        if (!sfRole) return;
        resolvedRole = sfRole;
      }
      if (resolvedRole === 'warehouse' && hasWarehouse) return;
      if (resolvedRole === 'lake' && hasLake) return;
      const pos = x != null && y != null ? { x, y } : defaultPosition(canvasNodes, resolvedRole);
      updateNodes([
        ...canvasNodes,
        { id: newId('n'), optionKey: opt.key, role: resolvedRole, x: pos.x, y: pos.y },
      ]);
    },
    [canvasNodes, hasLake, hasWarehouse, resolveSnowflakeRole, updateNodes, usedKeys],
  );

  const toggleSnowflakeRole = (nodeId) => {
    const node = canvasNodes.find((n) => n.id === nodeId);
    const opt = node ? optionByKey.get(node.optionKey) : null;
    if (!node || !isSnowflakeOpt(opt)) return;
    const nextRole = node.role === 'lake' ? 'warehouse' : 'lake';
    if (nextRole === 'lake' && hasLake && node.role !== 'lake') return;
    if (nextRole === 'warehouse' && hasWarehouse && node.role !== 'warehouse') return;
    updateNodes(canvasNodes.map((n) => (n.id === nodeId ? { ...n, role: nextRole } : n)));
  };

  const removeNode = (id) => {
    updateNodes(canvasNodes.filter((n) => n.id !== id));
    updateEdges((prev) => (prev || []).filter((e) => e.fromNodeId !== id && e.toNodeId !== id));
    if (pendingFrom === id) setPendingFrom(null);
    if (linkDrag?.fromNodeId === id) setLinkDrag(null);
  };

  const updateNodeAllowlist = (nodeId, sheetAllowlist) => {
    updateNodes(
      canvasNodes.map((n) =>
        n.id === nodeId
          ? {
              ...n,
              sheetAllowlist: sheetAllowlist == null ? undefined : sheetAllowlist,
            }
          : n,
      ),
    );
  };

  const removeEdge = (edgeId) => {
    updateEdges((prev) => (prev || []).filter((e) => e.id !== edgeId));
  };

  const tryConnect = useCallback((fromId, toId) => {
    if (!fromId || !toId || fromId === toId) return false;
    const nodes = canvasNodesRef.current;
    const edges = canvasEdgesRef.current || [];
    const type = linkTypeRef.current;
    const nodeMap = new Map(nodes.map((n) => [n.id, n]));
    let from = nodeMap.get(fromId);
    let to = nodeMap.get(toId);
    if (!from || !to) return false;

    if (!isValidTarget(from, to, type)) {
      if (
        (type === 'warehouse_load' || type === 'lake_load') &&
        (from.role === 'warehouse' || from.role === 'lake') &&
        to.role === 'source'
      ) {
        [from, to] = [to, from];
        [fromId, toId] = [toId, fromId];
      } else {
        return false;
      }
    }

    const exists = edges.some(
      (e) =>
        e.linkType === type &&
        ((e.fromNodeId === fromId && e.toNodeId === toId) ||
          (type === 'cross_map' && e.fromNodeId === toId && e.toNodeId === fromId)),
    );
    if (exists) return false;

    updateEdges([
      ...edges,
      { id: newId('e'), fromNodeId: fromId, toNodeId: toId, linkType: type },
    ]);
    return true;
  }, [updateEdges]);

  const finishLinkDrag = useCallback(
    (clientX, clientY) => {
      const drag = linkDragRef.current;
      linkDragRef.current = null;
      setLinkDrag(null);
      setHoverTargetId(null);
      setPendingFrom(null);

      suppressCanvasClickRef.current = true;
      if (!drag) return;
      const targetId = findNodeAtClient(clientX, clientY);
      if (targetId && targetId !== drag.fromNodeId) {
        tryConnect(drag.fromNodeId, targetId);
      }
    },
    [findNodeAtClient, tryConnect],
  );

  const startLinkDrag = useCallback(
    (e, nodeId) => {
      e.preventDefault();
      e.stopPropagation();
      const node = canvasNodesRef.current.find((n) => n.id === nodeId);
      if (!node || node.role !== 'source') return;

      const start = portCenter(node, 'out');
      const dragState = { fromNodeId: nodeId, startX: start.x, startY: start.y, curX: start.x, curY: start.y };
      linkDragRef.current = dragState;
      setLinkDrag(dragState);
      setPendingFrom(nodeId);

      const onMove = (ev) => {
        const prev = linkDragRef.current;
        if (!prev) return;
        const pt = canvasCoordsFromClient(ev.clientX, ev.clientY);
        const next = { ...prev, curX: pt.x, curY: pt.y };
        linkDragRef.current = next;
        setLinkDrag(next);
        const targetId = findNodeAtClient(ev.clientX, ev.clientY);
        setHoverTargetId(targetId && targetId !== nodeId ? targetId : null);
      };

      const onUp = (ev) => {
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
        finishLinkDrag(ev.clientX, ev.clientY);
      };

      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    },
    [canvasCoordsFromClient, findNodeAtClient, finishLinkDrag],
  );

  const onPortClick = (e, nodeId, side) => {
    e.stopPropagation();
    if (side === 'out') {
      setPendingFrom(nodeId);
      return;
    }
    if (pendingFrom) {
      const ok = tryConnect(pendingFrom, nodeId);
      setPendingFrom(null);
      if (ok) suppressCanvasClickRef.current = true;
    }
  };

  const onPaletteDragStart = (e, opt) => {
    e.dataTransfer.setData(PALETTE_MIME, opt.key);
    e.dataTransfer.effectAllowed = 'copy';
  };

  const canvasPointFromEvent = (e) => {
    const rect = canvasRef.current.getBoundingClientRect();
    const scrollLeft = canvasRef.current.scrollLeft || 0;
    const scrollTop = canvasRef.current.scrollTop || 0;
    return {
      x: Math.max(8, e.clientX - rect.left + scrollLeft - NODE_W / 2),
      y: Math.max(8, e.clientY - rect.top + scrollTop - NODE_TOTAL_H / 2),
    };
  };

  const onCanvasDrop = (e) => {
    e.preventDefault();
    setDragOverCanvas(false);
    const key = e.dataTransfer.getData(PALETTE_MIME);
    const opt = optionByKey.get(key);
    if (!opt) return;
    let role = 'source';
    if (opt.paletteRole === 'snowflake') {
      role = resolveSnowflakeRole(opt) || 'warehouse';
    }
    const { x, y } = canvasPointFromEvent(e);
    addNodeAt(opt, role, x, y);
  };

  const onNodeDragStart = (e, nodeId) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const node = nodeById.get(nodeId);
    if (!node || !canvasRef.current) return;
    const rect = canvasRef.current.getBoundingClientRect();
    dragOffset.current = {
      x: e.clientX - rect.left - node.x + (canvasRef.current.scrollLeft || 0),
      y: e.clientY - rect.top - node.y + (canvasRef.current.scrollTop || 0),
    };
    setDraggingNodeId(nodeId);

    const onMove = (ev) => {
      const r = canvasRef.current.getBoundingClientRect();
      const x = ev.clientX - r.left - dragOffset.current.x + (canvasRef.current.scrollLeft || 0);
      const y = ev.clientY - r.top - dragOffset.current.y + (canvasRef.current.scrollTop || 0);
      onCanvasNodesChange((prev) =>
        prev.map((n) =>
          n.id === nodeId ? { ...n, x: Math.max(0, x), y: Math.max(0, y) } : n,
        ),
      );
    };

    const onUp = () => {
      setDraggingNodeId(null);
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  const edgePaths = useMemo(() => {
    return (canvasEdges || [])
      .map((edge) => {
        const from = nodeById.get(edge.fromNodeId);
        const to = nodeById.get(edge.toNodeId);
        if (!from || !to) return null;
        const a = portCenter(from, 'out');
        const b = portCenter(to, 'in');
        const mx = (a.x + b.x) / 2;
        return {
          id: edge.id,
          linkType: edge.linkType,
          d: `M ${a.x} ${a.y} C ${mx} ${a.y}, ${mx} ${b.y}, ${b.x} ${b.y}`,
        };
      })
      .filter(Boolean);
  }, [canvasEdges, nodeById]);

  const previewPath = useMemo(() => {
    if (!linkDrag) return null;
    const { startX, startY, curX, curY } = linkDrag;
    const mx = (startX + curX) / 2;
    return `M ${startX} ${startY} C ${mx} ${startY}, ${mx} ${curY}, ${curX} ${curY}`;
  }, [linkDrag]);

  const validTargetIds = useMemo(() => {
    if (!linkDrag && !pendingFrom) return new Set();
    const fromId = linkDrag?.fromNodeId || pendingFrom;
    const from = nodeById.get(fromId);
    if (!from) return new Set();
    const ids = new Set();
    canvasNodes.forEach((n) => {
      if (n.id !== fromId && isValidTarget(from, n, linkType)) ids.add(n.id);
    });
    return ids;
  }, [canvasNodes, linkDrag, linkType, nodeById, pendingFrom]);

  return (
    <div className="eg-canvas-picker">
      <div className="eg-canvas-palette-wrap">
        <p className="eg-wizard-hint eg-canvas-palette-label">
          Drag connectors onto the canvas. Drag from the blue dot on a source to another node to draw a
          connection (or click blue, then purple).
        </p>
        <div className="eg-canvas-palette" role="list" aria-label="Available connectors">
          {connectorOptions.map((opt) => {
            const onCanvas = usedKeys.has(opt.key);
            return (
              <button
                key={opt.key}
                type="button"
                role="listitem"
                className={`eg-canvas-palette-chip${onCanvas ? ' eg-canvas-palette-chip--used' : ''}`}
                draggable={!onCanvas}
                disabled={onCanvas}
                title={onCanvas ? 'Already on canvas' : `Drag ${opt.label}`}
                onDragStart={(e) => onPaletteDragStart(e, opt)}
              >
                <ConnectorIcon opt={opt} size={22} />
                <span className="eg-canvas-palette-chip-text">
                  <strong>{opt.label}</strong>
                  <small>{opt.subtitle}</small>
                </span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="eg-canvas-toolbar">
        <span className="eg-canvas-toolbar-label">
          <FiLink aria-hidden /> Connection type
        </span>
        {LINK_TYPES.map((lt) => (
          <label key={lt.value} className="eg-canvas-link-type">
            <input
              type="radio"
              name="eg-link-type"
              value={lt.value}
              checked={linkType === lt.value}
              onChange={() => {
                setLinkType(lt.value);
                setPendingFrom(null);
                setLinkDrag(null);
                linkDragRef.current = null;
              }}
            />
            <span>
              <strong>{lt.label}</strong>
              <small>{lt.hint}</small>
            </span>
          </label>
        ))}
        {pendingFrom && !linkDrag && (
          <button type="button" className="eg-canvas-toolbar-cancel" onClick={() => setPendingFrom(null)}>
            Cancel link
          </button>
        )}
        {(linkDrag || pendingFrom) && (
          <span className="eg-canvas-pending-hint">
            {linkDrag
              ? 'Release on a highlighted node to connect'
              : 'Drag from blue dot, or click blue then purple on target'}
          </span>
        )}
      </div>

      <div
        ref={canvasRef}
        className={`eg-canvas-free${dragOverCanvas ? ' eg-canvas-free--active' : ''}${linkDrag ? ' eg-canvas-free--linking' : ''}`}
        onDragOver={(e) => {
          if (e.dataTransfer.types.includes(PALETTE_MIME)) {
            e.preventDefault();
            setDragOverCanvas(true);
          }
        }}
        onDragLeave={() => setDragOverCanvas(false)}
        onDrop={onCanvasDrop}
        onClick={() => {
          if (suppressCanvasClickRef.current) {
            suppressCanvasClickRef.current = false;
            return;
          }
          setPendingFrom(null);
        }}
      >
        {canvasNodes.length === 0 && (
          <p className="eg-canvas-free-empty">Drop sources, lake, and warehouse anywhere on this canvas</p>
        )}

        <svg className="eg-canvas-lines eg-canvas-lines--free" aria-hidden>
          {edgePaths.map((ep) => (
            <path
              key={ep.id}
              d={ep.d}
              className={`eg-canvas-edge eg-canvas-edge--${ep.linkType}`}
            />
          ))}
          {previewPath && (
            <path d={previewPath} className={`eg-canvas-edge eg-canvas-edge--preview eg-canvas-edge--${linkType}`} />
          )}
        </svg>

        {canvasNodes.map((n) => {
          const opt = optionByKey.get(n.optionKey);
          if (!opt) return null;
          const isPending = pendingFrom === n.id;
          const isValidTarget = validTargetIds.has(n.id);
          const isHoverTarget = hoverTargetId === n.id;
          const canStartLink = n.role === 'source';
          return (
            <div
              key={n.id}
              className={`eg-canvas-node eg-canvas-node--free${n.role === 'warehouse' ? ' eg-canvas-node--warehouse' : ''}${n.role === 'lake' ? ' eg-canvas-node--lake' : ''}${isPending ? ' eg-canvas-node--pending' : ''}${draggingNodeId === n.id ? ' eg-canvas-node--dragging' : ''}${isValidTarget ? ' eg-canvas-node--link-target' : ''}${isHoverTarget ? ' eg-canvas-node--link-hover' : ''}`}
              style={{ left: n.x, top: n.y, width: NODE_W, minHeight: NODE_TOTAL_H }}
              onClick={(e) => e.stopPropagation()}
            >
              <button
                type="button"
                className="eg-canvas-port eg-canvas-port--in"
                title="Drop connection here"
                onMouseDown={(e) => e.stopPropagation()}
                onClick={(e) => onPortClick(e, n.id, 'in')}
              />
              <div
                className="eg-canvas-node-body"
                onMouseDown={(e) => onNodeDragStart(e, n.id)}
                role="presentation"
              >
                <ConnectorIcon opt={opt} size={24} />
                <div className="eg-canvas-node-text">
                  <strong>{opt.label}</strong>
                  <span>{opt.subtitle}</span>
                  <span className="eg-canvas-node-role">{roleLabel(n.role)}</span>
                  {opt.connector_type === 'google_sheets' && (
                    <GoogleSheetsNodeScope
                      connectionId={opt.connector_id}
                      allowlist={n.sheetAllowlist}
                      onAllowlistChange={(ids) => updateNodeAllowlist(n.id, ids)}
                    />
                  )}
                  {isSnowflakeOpt(opt) && (
                    <button
                      type="button"
                      className="eg-canvas-role-toggle"
                      onMouseDown={(e) => e.stopPropagation()}
                      onClick={() => toggleSnowflakeRole(n.id)}
                    >
                      Use as {n.role === 'lake' ? 'warehouse' : 'lake'}
                    </button>
                  )}
                </div>
                <button
                  type="button"
                  className="eg-canvas-node-remove"
                  aria-label={`Remove ${opt.label}`}
                  onMouseDown={(e) => e.stopPropagation()}
                  onClick={() => removeNode(n.id)}
                >
                  <FiX />
                </button>
              </div>
              {canStartLink && (
                <button
                  type="button"
                  className="eg-canvas-port eg-canvas-port--out"
                  title="Drag to another node to connect"
                  onMouseDown={(e) => startLinkDrag(e, n.id)}
                  onClick={(e) => onPortClick(e, n.id, 'out')}
                />
              )}
              {!canStartLink && <span className="eg-canvas-port eg-canvas-port--spacer" aria-hidden />}
            </div>
          );
        })}
      </div>

      {(canvasEdges || []).length > 0 && (
        <ul className="eg-canvas-edge-list">
          {(canvasEdges || []).map((edge) => {
            const from = nodeById.get(edge.fromNodeId);
            const to = nodeById.get(edge.toNodeId);
            const fromOpt = from ? optionByKey.get(from.optionKey) : null;
            const toOpt = to ? optionByKey.get(to.optionKey) : null;
            const typeLabel =
              edge.linkType === 'warehouse_load'
                ? 'Warehouse load'
                : edge.linkType === 'lake_load'
                  ? 'Lake load'
                  : 'Cross-map';
            return (
              <li key={edge.id}>
                <span className={`eg-canvas-edge-badge eg-canvas-edge-badge--${edge.linkType}`}>
                  {typeLabel}
                </span>
                <span>
                  {fromOpt?.label || '?'} → {toOpt?.label || '?'}
                </span>
                <button
                  type="button"
                  className="eg-canvas-edge-remove"
                  aria-label="Remove connection"
                  onClick={() => removeEdge(edge.id)}
                >
                  <FiTrash2 />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}