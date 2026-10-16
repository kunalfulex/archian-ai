"""Generate a client-shareable PDF from a connector assessment report."""
from __future__ import annotations

import io
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    CondPageBreak,
    Flowable,
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

DIMENSION_LABELS = {
    "completeness": "Completeness",
    "quality": "Quality",
    "consistency": "Consistency",
    "structure": "Structure",
    "warehouse_readiness": "Warehouse readiness",
}

GAP_CATEGORY_LABELS = {
    "relationship": "Relationship",
    "metadata": "Metadata",
    "schema_staleness": "Schema staleness",
    "coverage": "Coverage",
    "other": "Other",
}

CONNECTOR_LABELS = {
    "salesforce": "Salesforce org",
    "sql": "Database",
    "google_sheets": "Google Sheets",
    "enterprise_graph": "Enterprise data graph",
}

COLOR_PRIMARY = colors.HexColor("#2563eb")
COLOR_TEXT = colors.HexColor("#0f172a")
COLOR_MUTED = colors.HexColor("#64748b")
COLOR_BORDER = colors.HexColor("#e2e8f0")
COLOR_ALERT = colors.HexColor("#dc2626")
COLOR_GOOD = colors.HexColor("#16a34a")
COLOR_FAIR = colors.HexColor("#d97706")
COLOR_RING = colors.HexColor("#22c55e")
COLOR_RING_TRACK = colors.HexColor("#e2e8f0")
COLOR_SCHEMA_BORDER = colors.HexColor("#6366f1")
COLOR_CARD_BG = colors.white
COLOR_SECTION_BG = colors.HexColor("#f8fafc")

CONTENT_WIDTH = 7.0 * inch
CARD_PAD = 10
CARD_GAP = 8
MIN_SECTION_SPACE = 1.35 * inch


def _readiness_tier(score: Optional[float]) -> Tuple[str, str]:
    if score is None:
        return "Unknown", "neutral"
    if score >= 80:
        return "Good", "good"
    if score >= 60:
        return "Fair", "fair"
    return "Needs attention", "alert"


def _tier_colors(tier_class: str) -> Tuple[colors.Color, colors.Color]:
    mapping = {
        "good": (colors.HexColor("#dcfce7"), colors.HexColor("#166534")),
        "fair": (colors.HexColor("#fef3c7"), colors.HexColor("#92400e")),
        "alert": (colors.HexColor("#fee2e2"), colors.HexColor("#b91c1c")),
    }
    return mapping.get(tier_class, (colors.HexColor("#f1f5f9"), colors.HexColor("#475569")))


def _bar_color(pct: float) -> colors.Color:
    if pct >= 75:
        return colors.HexColor("#22c55e")
    if pct >= 55:
        return colors.HexColor("#f59e0b")
    return colors.HexColor("#f97316")


def _severity_colors(severity: str) -> Tuple[colors.Color, colors.Color]:
    sev = (severity or "low").lower()
    if sev == "high":
        return colors.HexColor("#fee2e2"), colors.HexColor("#b91c1c")
    if sev == "medium":
        return colors.HexColor("#fef3c7"), colors.HexColor("#92400e")
    return colors.HexColor("#f1f5f9"), colors.HexColor("#475569")


def _parse_json_field(val: Any) -> Any:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return val
    return val


def _build_metrics(report: Dict[str, Any]) -> Dict[str, Any]:
    scores = report.get("scores") or {}
    sections = report.get("sections") or {}
    entities = report.get("entities") or []
    graph = sections.get("schema_graph") or {}
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []

    entities_assessed = scores.get("entities_assessed") or len(entities)
    overall = scores.get("overall")

    problem_entities = 0
    for ep in entities:
        if ep.get("error"):
            problem_entities += 1
            continue
        qr = ep.get("quality_report") or {}
        summary = qr.get("summary") or {}
        score = summary.get("data_quality_score")
        dups = qr.get("duplicates") or {}
        exact = (dups.get("exact_duplicates") or {}).get("count") or 0
        key_dup = (dups.get("key_field_duplicates") or {}).get("count") or 0
        miss = (qr.get("missing_values") or {}).get("total_missing") or 0
        fmt = sum(
            len(v) if isinstance(v, list) else 0 for v in (qr.get("format_issues") or {}).values()
        )
        if exact or key_dup or miss or fmt or (score is not None and float(score) < 80):
            problem_entities += 1

    tier_label, tier_class = _readiness_tier(overall)
    return {
        "overall": overall,
        "tier": tier_label,
        "tier_class": tier_class,
        "entities_assessed": entities_assessed,
        "problem_entities": problem_entities,
        "mapped_entities": len(nodes) or entities_assessed,
        "relationship_count": len(edges),
        "dimensions": scores.get("dimensions") or {},
    }


def _pick_schema_map_preview(graph: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not nodes:
        return [], []

    priority = ["Account", "Contact", "Lead", "Opportunity", "Case"]
    by_id = {n.get("id"): n for n in nodes if n.get("id")}
    chain: List[Dict[str, Any]] = []
    for pid in priority:
        if pid in by_id and len(chain) < 3:
            chain.append(by_id[pid])

    if len(chain) < 3:
        degree: Dict[str, int] = {}
        for e in edges:
            degree[e.get("source_id", "")] = degree.get(e.get("source_id", ""), 0) + 1
            degree[e.get("target_id", "")] = degree.get(e.get("target_id", ""), 0) + 1
        for node in sorted(nodes, key=lambda n: degree.get(n.get("id", ""), 0), reverse=True):
            if len(chain) >= 3:
                break
            if not any(c.get("id") == node.get("id") for c in chain):
                chain.append(node)

    ids = {n.get("id") for n in chain}
    preview_edges = [
        e for e in edges if e.get("source_id") in ids and e.get("target_id") in ids
    ]
    return chain[:3], preview_edges


def _resolve_data_quality(report: Dict[str, Any]) -> Dict[str, Any]:
    sections = report.get("sections") or {}
    dq = sections.get("data_quality") or {}
    if dq.get("connector_totals") and dq.get("by_entity"):
        return dq

    totals = {
        "exact_duplicates": 0,
        "key_field_duplicates": 0,
        "total_missing": 0,
        "format_issue_count": 0,
    }
    by_entity: List[Dict[str, Any]] = []
    for ep in report.get("entities") or []:
        if ep.get("error"):
            by_entity.append(
                {
                    "entity_id": ep.get("entity_id"),
                    "label": ep.get("label") or ep.get("entity_id"),
                    "error": ep.get("error"),
                }
            )
            continue
        qr = ep.get("quality_report") or {}
        summary = qr.get("summary") or {}
        dups = qr.get("duplicates") or {}
        exact = (dups.get("exact_duplicates") or {}).get("count") or 0
        key_dup = (dups.get("key_field_duplicates") or {}).get("count") or 0
        miss = (qr.get("missing_values") or {}).get("total_missing") or 0
        fmt = sum(
            len(v) if isinstance(v, list) else 0 for v in (qr.get("format_issues") or {}).values()
        )
        totals["exact_duplicates"] += exact
        totals["key_field_duplicates"] += key_dup
        totals["total_missing"] += miss
        totals["format_issue_count"] += fmt
        by_entity.append(
            {
                "entity_id": ep.get("entity_id"),
                "label": ep.get("label") or ep.get("entity_id"),
                "score": summary.get("data_quality_score"),
                "records_sampled": ep.get("records_sampled") or 0,
                "duplicates": {"exact_duplicates": exact, "key_field_duplicates": key_dup},
                "missing_values": {"total_missing": miss},
                "format_issue_count": fmt,
                "recommendations": summary.get("recommendations") or ep.get("issues_summary") or [],
            }
        )
    return {"connector_totals": totals, "by_entity": by_entity}


def _group_gap_findings(findings: List[Dict[str, Any]]) -> List[Tuple[str, List[Dict[str, Any]]]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for g in findings or []:
        cat = g.get("category") or "other"
        groups.setdefault(cat, []).append(g)
    order = ["relationship", "metadata", "schema_staleness", "coverage", "other"]
    out: List[Tuple[str, List[Dict[str, Any]]]] = []
    for cat in order:
        if groups.get(cat):
            out.append((cat, groups[cat]))
    for cat, items in groups.items():
        if cat not in order:
            out.append((cat, items))
    return out


def _entity_label_map(entities: List[Dict[str, Any]]) -> Dict[str, str]:
    return {
        str(e["entity_id"]): str(e["label"])
        for e in entities or []
        if e.get("entity_id") and e.get("label")
    }


def _format_datetime(val: Any) -> str:
    if not val:
        return ""
    if hasattr(val, "strftime"):
        return val.strftime("%b %d, %Y, %I:%M %p")
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            return dt.strftime("%b %d, %Y, %I:%M %p")
        except ValueError:
            return val
    return str(val)


def _safe_filename_part(name: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", name).strip().replace(" ", "-")
    return cleaned[:60] or "report"


class ScoreRingFlowable(Flowable):
    """Circular readiness score gauge matching the dashboard ring."""

    def __init__(self, score: Optional[float], size: float = 64):
        Flowable.__init__(self)
        self.score = score
        self.size = size
        self.width = size
        self.height = size + 4

    def draw(self) -> None:
        canv = self.canv
        cx = self.size / 2
        cy = self.size / 2 + 2
        r = (self.size - 10) / 2
        value = 0 if self.score is None else int(round(min(100, max(0, float(self.score)))))

        canv.saveState()
        canv.setLineWidth(6)
        canv.setLineCap(1)
        canv.setStrokeColor(COLOR_RING_TRACK)
        canv.circle(cx, cy, r, stroke=1, fill=0)

        if value > 0:
            canv.setStrokeColor(COLOR_RING)
            extent = 360 * value / 100
            canv.arc(cx - r, cy - r, cx + r, cy + r, 90, -extent)

        canv.setFillColor(COLOR_TEXT)
        canv.setFont("Helvetica-Bold", 15)
        canv.drawCentredString(cx, cy - 5, str(value) if self.score is not None else "—")
        canv.restoreState()


class DimensionBarsFlowable(Flowable):
    """Horizontal dimension progress bars."""

    def __init__(self, dimensions: Dict[str, Any], width: float, row_height: float = 26):
        Flowable.__init__(self)
        self.dimensions = dimensions or {}
        self.width = width
        self.row_height = row_height
        self.height = max(row_height, len(self.dimensions) * row_height)

    def draw(self) -> None:
        canv = self.canv
        y = self.height - 18
        bar_max_w = self.width - 36

        for key, val in self.dimensions.items():
            label = DIMENSION_LABELS.get(key, key.replace("_", " ").title())
            short = label.split("—")[0].strip()
            try:
                pct = int(round(min(100, max(0, float(val)))))
            except (TypeError, ValueError):
                pct = 0

            canv.setFillColor(colors.HexColor("#475569"))
            canv.setFont("Helvetica", 8)
            canv.drawString(0, y + 10, short[:28])
            canv.setFillColor(COLOR_TEXT)
            canv.setFont("Helvetica-Bold", 8)
            canv.drawRightString(self.width, y + 10, f"{pct}%")

            track_y = y + 2
            canv.setFillColor(colors.HexColor("#f1f5f9"))
            canv.roundRect(0, track_y, bar_max_w, 6, 3, stroke=0, fill=1)
            fill_w = max(2, bar_max_w * pct / 100)
            canv.setFillColor(_bar_color(pct))
            canv.roundRect(0, track_y, fill_w, 6, 3, stroke=0, fill=1)
            y -= self.row_height


class SchemaMiniFlowFlowable(Flowable):
    """Mini schema entity chain with link counts."""

    def __init__(
        self,
        chain: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
        all_edges: List[Dict[str, Any]],
        width: float,
        node_count: int,
        edge_count: int,
    ):
        Flowable.__init__(self)
        self.chain = chain
        self.edges = edges
        self.all_edges = all_edges
        self.width = width
        self.node_count = node_count
        self.edge_count = edge_count
        self.height = 64 if chain else 20

    def draw(self) -> None:
        if not self.chain:
            self.canv.setFillColor(COLOR_MUTED)
            self.canv.setFont("Helvetica", 9)
            self.canv.drawString(0, 8, "Run assessment with schema sync to see relationships.")
            return

        link_counts: Dict[str, int] = {}
        for e in self.all_edges:
            link_counts[e.get("source_id", "")] = link_counts.get(e.get("source_id", ""), 0) + 1
            link_counts[e.get("target_id", "")] = link_counts.get(e.get("target_id", ""), 0) + 1

        box_w = min(88, (self.width - 40) / max(len(self.chain), 1))
        x = 0
        y = 28
        canv = self.canv

        for i, node in enumerate(self.chain):
            if i > 0:
                canv.setStrokeColor(colors.HexColor("#94a3b8"))
                canv.setDash(2, 2)
                canv.line(x + 4, y + 14, x + 14, y + 14)
                canv.setDash()
                x += 18

            nid = node.get("id", "")
            label = (node.get("label") or nid)[:14]
            links = link_counts.get(nid, 0)
            canv.setStrokeColor(COLOR_SCHEMA_BORDER)
            canv.setLineWidth(1.5)
            canv.setFillColor(colors.white)
            canv.roundRect(x, y, box_w, 36, 6, stroke=1, fill=1)
            canv.setFillColor(COLOR_TEXT)
            canv.setFont("Helvetica-Bold", 8)
            canv.drawCentredString(x + box_w / 2, y + 20, label)
            canv.setFillColor(COLOR_MUTED)
            canv.setFont("Helvetica", 7)
            canv.drawCentredString(x + box_w / 2, y + 8, f"{links} links")
            x += box_w + 4

        canv.setFillColor(COLOR_MUTED)
        canv.setFont("Helvetica", 8)
        canv.drawString(0, 4, f"{self.node_count} entities · {self.edge_count} relationships")


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(text, style)


def _badge_paragraph(text: str, bg: colors.Color, fg: colors.Color, style: ParagraphStyle) -> Paragraph:
    badge_style = ParagraphStyle(
        f"Badge_{text}",
        parent=style,
        fontSize=7,
        fontName="Helvetica-Bold",
        textColor=fg,
        backColor=bg,
        borderPadding=(4, 6, 4, 6),
        leading=9,
    )
    return Paragraph(text, badge_style)


def _card_box(content: List[Any], width: float, min_height: Optional[float] = None) -> Table:
    inner = Table([[item] for item in content], colWidths=[width - 2 * CARD_PAD])
    inner.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    outer = Table([[inner]], colWidths=[width])
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, -1), COLOR_CARD_BG),
        ("BOX", (0, 0), (-1, -1), 0.75, COLOR_BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), CARD_PAD),
        ("RIGHTPADDING", (0, 0), (-1, -1), CARD_PAD),
        ("TOPPADDING", (0, 0), (-1, -1), CARD_PAD),
        ("BOTTOMPADDING", (0, 0), (-1, -1), CARD_PAD),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    if min_height:
        style_cmds.append(("MINHEIGHT", (0, 0), (-1, -1), min_height))
    outer.setStyle(TableStyle(style_cmds))
    return outer


def _doc_section_heading(
    number: int,
    title: str,
    styles: Dict[str, ParagraphStyle],
) -> List[Any]:
    """Compact numbered section heading."""
    return [
        Spacer(1, 10 if number > 1 else 4),
        _p(f"<font color='#2563eb' size='8'>SECTION {number}</font>", styles["section_label"]),
        _p(f"<b>{title}</b>", styles["section"]),
        HRFlowable(width="100%", thickness=0.75, color=COLOR_BORDER, spaceBefore=2, spaceAfter=6),
    ]


def _append_section(
    story: List[Any],
    number: int,
    title: str,
    blocks: List[Any],
    styles: Dict[str, ParagraphStyle],
    *,
    keep_all: bool = False,
) -> None:
    """Append a section; avoid orphaned headings at page bottom."""
    if number > 1:
        story.append(CondPageBreak(MIN_SECTION_SPACE))
    heading = _doc_section_heading(number, title, styles)
    if not blocks:
        story.extend(heading)
        return
    if keep_all:
        story.append(KeepTogether(heading + blocks))
    else:
        story.append(KeepTogether(heading + blocks[:1]))
        if len(blocks) > 1:
            story.extend(blocks[1:])


def _metric_card(
    title: str,
    body: List[Any],
    width: float,
    styles: Dict[str, ParagraphStyle],
) -> Table:
    content = [_p(f"<font color='#64748b'><b>{title}</b></font>", styles["card_label"])] + body
    return _card_box(content, width, min_height=76)


def _section_card(title: str, body: List[Any], width: float, styles: Dict[str, ParagraphStyle]) -> Table:
    content = [_p(f"<b>{title}</b>", styles["card_title"])] + body
    return _card_box(content, width)


def _build_snapshot_metrics_row(
    metrics: Dict[str, Any],
    connector_label: str,
    styles: Dict[str, ParagraphStyle],
) -> Table:
    card_w = (CONTENT_WIDTH - 3 * CARD_GAP) / 4
    tier_bg, tier_fg = _tier_colors(metrics["tier_class"])
    overall = metrics["overall"]

    score_card = _metric_card(
        "Data readiness score",
        [
            ScoreRingFlowable(overall, size=50),
            Spacer(1, 2),
            _p("<font color='#94a3b8' size='8'>/ 100</font>", styles["muted"]),
            Spacer(1, 4),
            _badge_paragraph(metrics["tier"], tier_bg, tier_fg, styles["body"]),
        ],
        card_w,
        styles,
    )

    connector_card = _metric_card(
        "Connector",
        [
            Spacer(1, 4),
            _p(f"<font size='16'><b>{connector_label}</b></font>", styles["metric_value"]),
            Spacer(1, 6),
            _p(
                f"<font color='#64748b'>{metrics['entities_assessed']} entities assessed</font>",
                styles["muted"],
            ),
        ],
        card_w,
        styles,
    )

    issues_body: List[Any] = [
        Spacer(1, 4),
        _p(f"<font size='16' color='#dc2626'><b>{metrics['problem_entities']}</b></font>", styles["metric_value"]),
        Spacer(1, 6),
        _p(
            f"<font color='#64748b'>Of {metrics['entities_assessed']} assessed</font>",
            styles["muted"],
        ),
    ]
    if metrics["problem_entities"] > 0:
        issues_body.append(
            _badge_paragraph(
                "Review needed",
                colors.HexColor("#fee2e2"),
                colors.HexColor("#b91c1c"),
                styles["body"],
            )
        )
    issues_card = _metric_card("Entities with issues", issues_body, card_w, styles)

    mapped_body: List[Any] = [
        Spacer(1, 4),
        _p(
            f"<font size='16' color='#2563eb'><b>{metrics['mapped_entities']}</b></font>",
            styles["metric_value"],
        ),
        Spacer(1, 6),
        _p(
            f"<font color='#64748b'>{metrics['relationship_count']} relationships</font>",
            styles["muted"],
        ),
    ]
    if metrics["mapped_entities"] > 0 and metrics["problem_entities"] == 0:
        mapped_body.append(
            _badge_paragraph(
                "Good coverage",
                colors.HexColor("#dcfce7"),
                colors.HexColor("#166534"),
                styles["body"],
            )
        )
    mapped_card = _metric_card("Mapped in schema", mapped_body, card_w, styles)

    row = Table(
        [[score_card, connector_card, issues_card, mapped_card]],
        colWidths=[card_w] * 4,
        hAlign="LEFT",
    )
    row.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-2, -1), CARD_GAP),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return row


def _build_snapshot_detail_row(
    dimensions: Dict[str, Any],
    graph: Dict[str, Any],
    styles: Dict[str, ParagraphStyle],
) -> Table:
    """Readiness bars + schema mini-flow (no summary — that lives in Executive Summary)."""
    card_w = (CONTENT_WIDTH - CARD_GAP) / 2
    chain, preview_edges = _pick_schema_map_preview(graph)
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []

    if dimensions:
        readiness_body = [
            DimensionBarsFlowable(dimensions, card_w - 2 * CARD_PAD, row_height=24),
        ]
    else:
        readiness_body = [
            _p("<font color='#94a3b8'>No dimension scores in this report.</font>", styles["muted"])
        ]
    readiness_card = _section_card("Readiness at a glance", readiness_body, card_w, styles)

    schema_body = [
        SchemaMiniFlowFlowable(
            chain, preview_edges, edges, card_w - 2 * CARD_PAD, len(nodes), len(edges)
        )
    ]
    schema_card = _section_card("Schema map at a glance", schema_body, card_w, styles)

    row = Table([[readiness_card, schema_card]], colWidths=[card_w, card_w], hAlign="LEFT")
    row.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (0, -1), CARD_GAP),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return row


def _build_executive_summary_section(
    summary_text: str,
    risks: List[Any],
    styles: Dict[str, ParagraphStyle],
) -> Table:
    body: List[Any] = []
    if summary_text:
        body.append(_p(summary_text.replace("\n", "<br/>"), styles["body"]))
    else:
        body.append(_p("<font color='#94a3b8'>No summary narrative for this run.</font>", styles["muted"]))

    if risks:
        body.append(Spacer(1, 10))
        body.append(_p("<b>Key risks</b>", styles["card_title"]))
        for risk in risks[:8]:
            msg = risk if isinstance(risk, str) else risk.get("message") or str(risk)
            body.append(_p(f"• {msg}", styles["risk"]))

    return _section_card("Overview", body, CONTENT_WIDTH, styles)


def _quality_stat_tiles(totals: Dict[str, Any], styles: Dict[str, ParagraphStyle]) -> Table:
    tile_w = (CONTENT_WIDTH - 3 * CARD_GAP) / 4
    labels = [
        ("Exact duplicates", totals.get("exact_duplicates", 0)),
        ("Key duplicates", totals.get("key_field_duplicates", 0)),
        ("Missing values", totals.get("total_missing", 0)),
        ("Format issues", totals.get("format_issue_count", 0)),
    ]
    tiles = []
    for label, val in labels:
        tiles.append(
            _card_box(
                [
                    Spacer(1, 2),
                    _p(f"<font size='16'><b>{val}</b></font>", styles["metric_value"]),
                    Spacer(1, 4),
                    _p(f"<font color='#64748b' size='8'>{label}</font>", styles["muted"]),
                ],
                tile_w,
                min_height=52,
            )
        )
    row = Table([tiles], colWidths=[tile_w] * 4)
    row.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-2, -1), CARD_GAP),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return row


def build_assessment_pdf(
    report: Dict[str, Any],
    *,
    connector_type: str = "salesforce",
    connector_display_name: Optional[str] = None,
) -> bytes:
    """Return PDF bytes for the given assessment report."""
    report = dict(report)
    for key in ("scores", "sections", "entities", "connector_ref"):
        if key in report:
            report[key] = _parse_json_field(report[key])

    sections = report.get("sections") or {}
    metrics = _build_metrics(report)
    data_quality = _resolve_data_quality(report)
    gap_groups = _group_gap_findings((sections.get("data_gaps") or {}).get("findings") or [])
    label_map = _entity_label_map(report.get("entities") or [])
    graph = sections.get("schema_graph") or {}

    risks = sections.get("risks") or []
    if isinstance(risks, dict):
        risks = [risks]
    summary_text = sections.get("summary") or ""
    if not isinstance(summary_text, str):
        summary_text = str(summary_text)

    connector_label = CONNECTOR_LABELS.get(connector_type, "Connector")
    title_name = (connector_display_name or "").strip()
    report_title = (
        f"{title_name} — Data Assessment Report" if title_name else "Data Assessment Report"
    )
    generated_at = _format_datetime(report.get("created_at"))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.65 * inch,
        title=report_title,
    )

    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "ReportTitle", parent=base["Heading1"], fontSize=18, textColor=COLOR_TEXT, spaceAfter=4
        ),
        "subtitle": ParagraphStyle(
            "ReportSubtitle", parent=base["Normal"], fontSize=9, textColor=COLOR_MUTED, spaceAfter=2
        ),
        "section": ParagraphStyle(
            "SectionHeading",
            parent=base["Heading2"],
            fontSize=12,
            textColor=COLOR_TEXT,
            spaceBefore=0,
            spaceAfter=2,
            leading=14,
        ),
        "section_label": ParagraphStyle(
            "SectionLabel",
            parent=base["Normal"],
            fontSize=8,
            textColor=COLOR_PRIMARY,
            spaceBefore=0,
            spaceAfter=1,
            leading=10,
        ),
        "body": ParagraphStyle(
            "Body", parent=base["Normal"], fontSize=9, textColor=COLOR_TEXT, leading=13, spaceAfter=4
        ),
        "metric_value": ParagraphStyle(
            "MetricValue", parent=base["Normal"], fontSize=14, textColor=COLOR_TEXT, leading=16, spaceAfter=2
        ),
        "muted": ParagraphStyle(
            "Muted", parent=base["Normal"], fontSize=8, textColor=COLOR_MUTED, leading=11, spaceAfter=4
        ),
        "risk": ParagraphStyle(
            "Risk", parent=base["Normal"], fontSize=8, textColor=COLOR_MUTED, leading=11, leftIndent=6
        ),
        "card_label": ParagraphStyle(
            "CardLabel", parent=base["Normal"], fontSize=8, textColor=COLOR_MUTED, spaceAfter=6
        ),
        "card_title": ParagraphStyle(
            "CardTitle", parent=base["Normal"], fontSize=10, textColor=COLOR_TEXT, spaceAfter=8
        ),
        "footer": ParagraphStyle(
            "Footer", parent=base["Normal"], fontSize=7, textColor=COLOR_MUTED, alignment=TA_CENTER
        ),
    }

    story: List[Any] = []

    # Cover header
    story.append(_p(report_title, styles["title"]))
    if generated_at:
        story.append(_p(f"Generated on {generated_at}", styles["subtitle"]))
    story.append(_p(f"Connector: {connector_label}", styles["subtitle"]))
    if report.get("is_partial"):
        story.append(
            _badge_paragraph(
                "Partial run",
                colors.HexColor("#fef3c7"),
                colors.HexColor("#92400e"),
                styles["body"],
            )
        )
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=1, color=COLOR_BORDER, spaceAfter=6))

    # 1. Snapshot
    _append_section(
        story,
        1,
        "Snapshot",
        [
            _build_snapshot_metrics_row(metrics, connector_label, styles),
            Spacer(1, 6),
            _build_snapshot_detail_row(metrics["dimensions"], graph, styles),
        ],
        styles,
        keep_all=True,
    )

    # 2. Executive Summary
    _append_section(
        story,
        2,
        "Executive Summary",
        [_build_executive_summary_section(summary_text, risks, styles)],
        styles,
    )

    # 3. Readiness
    readiness_body: List[Any] = [
        _p(
            f"Overall readiness: <b>{int(round(metrics['overall']))}/100</b>"
            if metrics["overall"] is not None
            else "Overall readiness: <b>—</b>",
            styles["body"],
        ),
        Spacer(1, 6),
    ]
    if metrics["dimensions"]:
        readiness_body.append(
            DimensionBarsFlowable(metrics["dimensions"], CONTENT_WIDTH - 2 * CARD_PAD)
        )
    else:
        readiness_body.append(
            _p("<font color='#94a3b8'>No dimension scores in this report.</font>", styles["muted"])
        )
    _append_section(
        story,
        3,
        "Readiness",
        [_section_card("Dimension scores", readiness_body, CONTENT_WIDTH, styles)],
        styles,
    )

    # 4. Data quality
    quality_blocks: List[Any] = []
    totals = data_quality.get("connector_totals") or {}
    if totals:
        quality_blocks.append(_quality_stat_tiles(totals, styles))
        quality_blocks.append(Spacer(1, 6))

    by_entity = data_quality.get("by_entity") or []
    if by_entity:
        entity_rows = [["Entity", "Score", "Sampled", "Issues"]]
        for row in by_entity[:25]:
            name = row.get("label") or label_map.get(row.get("entity_id", ""), row.get("entity_id", "—"))
            if row.get("error"):
                entity_rows.append([name, "Error", "—", row.get("error", "")[:90]])
                continue
            score = row.get("score")
            score_str = f"{int(round(float(score)))}/100" if score is not None else "—"
            sampled = str(row.get("records_sampled") or 0)
            dups = row.get("duplicates") or {}
            miss = (row.get("missing_values") or {}).get("total_missing") or 0
            issue_parts = []
            if dups.get("exact_duplicates"):
                issue_parts.append(f"{dups['exact_duplicates']} exact dup")
            if dups.get("key_field_duplicates"):
                issue_parts.append(f"{dups['key_field_duplicates']} key dup")
            if miss:
                issue_parts.append(f"{miss} missing")
            if row.get("format_issue_count"):
                issue_parts.append(f"{row['format_issue_count']} format")
            entity_rows.append([name, score_str, sampled, ", ".join(issue_parts) if issue_parts else "—"])

        entity_table = Table(
            entity_rows, colWidths=[1.9 * inch, 0.75 * inch, 0.7 * inch, 3.65 * inch]
        )
        entity_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("TEXTCOLOR", (0, 0), (-1, 0), COLOR_TEXT),
                    ("GRID", (0, 0), (-1, -1), 0.5, COLOR_BORDER),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafbfc")]),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        quality_blocks.append(_section_card("By entity", [entity_table], CONTENT_WIDTH, styles))

        rec_items = []
        for row in by_entity[:8]:
            recs = row.get("recommendations") or []
            if recs and not row.get("error"):
                name = row.get("label") or row.get("entity_id")
                rec_items.append(_p(f"• <b>{name}:</b> {recs[0]}", styles["body"]))
        if rec_items:
            quality_blocks.append(Spacer(1, 6))
            quality_blocks.append(_section_card("Top recommendations", rec_items, CONTENT_WIDTH, styles))
    elif not totals:
        quality_blocks.append(
            _p("<font color='#94a3b8'>No per-entity quality data in this report.</font>", styles["muted"])
        )
    _append_section(story, 4, "Data Quality", quality_blocks, styles)

    # 5. Data gaps
    gap_blocks: List[Any] = []
    if not gap_groups:
        gap_blocks.append(
            _section_card(
                "Findings",
                [_p("<font color='#94a3b8'>No structural or metadata gaps flagged.</font>", styles["muted"])],
                CONTENT_WIDTH,
                styles,
            )
        )
    else:
        for idx, (category, items) in enumerate(gap_groups):
            cat_label = GAP_CATEGORY_LABELS.get(category, category.replace("_", " ").title())
            gap_items: List[Any] = []
            for g in items[:15]:
                eid = g.get("entity_id")
                prefix = ""
                if eid and eid != "*":
                    prefix = f"{label_map.get(eid, eid)}: "
                sev = (g.get("severity") or "low").upper()
                bg, fg = _severity_colors(g.get("severity") or "low")
                gap_items.append(
                    Table(
                        [
                            [
                                _badge_paragraph(sev, bg, fg, styles["body"]),
                                _p(f"{prefix}{g.get('message', '')}", styles["body"]),
                            ]
                        ],
                        colWidths=[0.65 * inch, CONTENT_WIDTH - 2 * CARD_PAD - 0.75 * inch],
                    )
                )
            gap_blocks.append(_section_card(cat_label, gap_items, CONTENT_WIDTH, styles))
            if idx < len(gap_groups) - 1:
                gap_blocks.append(Spacer(1, 6))
    _append_section(story, 5, "Data Gaps", gap_blocks, styles)

    # 6. Schema map
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    schema_blocks: List[Any] = []
    if nodes:
        chain, preview_edges = _pick_schema_map_preview(graph)
        schema_parts: List[Any] = [
            SchemaMiniFlowFlowable(chain, preview_edges, edges, CONTENT_WIDTH - 2 * CARD_PAD, len(nodes), len(edges)),
            Spacer(1, 6),
            _p(
                f"<font color='#64748b'>{len(nodes)} entities mapped · {len(edges)} relationships in synced schema.</font>",
                styles["muted"],
            ),
        ]
        link_counts: Dict[str, int] = {}
        for e in edges:
            link_counts[e.get("source_id", "")] = link_counts.get(e.get("source_id", ""), 0) + 1
            link_counts[e.get("target_id", "")] = link_counts.get(e.get("target_id", ""), 0) + 1
        node_rows = [["Entity", "Type", "Links"]]
        for node in nodes[:30]:
            nid = node.get("id", "")
            node_rows.append(
                [node.get("label") or nid, node.get("type") or "—", str(link_counts.get(nid, 0))]
            )
        schema_table = Table(node_rows, colWidths=[2.8 * inch, 1.6 * inch, 0.8 * inch])
        schema_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.5, COLOR_BORDER),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafbfc")]),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        schema_parts.append(schema_table)
        if len(nodes) > 30:
            schema_parts.append(
                _p(f"<font color='#64748b'>… and {len(nodes) - 30} more entities.</font>", styles["muted"])
            )
        schema_blocks.append(_section_card("Connected schema", schema_parts, CONTENT_WIDTH, styles))
    else:
        schema_blocks.append(
            _section_card(
                "Connected schema",
                [
                    _p(
                        "<font color='#94a3b8'>No schema graph in this report. Enable connector metadata sync and re-run assessment.</font>",
                        styles["muted"],
                    )
                ],
                CONTENT_WIDTH,
                styles,
            )
        )
    _append_section(story, 6, "Schema Map", schema_blocks, styles)

    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=0.5, color=COLOR_BORDER))
    story.append(_p("Generated by Archian AI", styles["footer"]))

    doc.build(story)
    return buffer.getvalue()


def pdf_download_filename(
    report: Dict[str, Any],
    *,
    connector_display_name: Optional[str] = None,
) -> str:
    """Suggested attachment filename for the PDF."""
    name_part = _safe_filename_part(connector_display_name or "connector")
    date_part = ""
    created = report.get("created_at")
    if created:
        if hasattr(created, "strftime"):
            date_part = created.strftime("%Y-%m-%d")
        elif isinstance(created, str) and len(created) >= 10:
            date_part = created[:10]
    suffix = f"-{date_part}" if date_part else ""
    return f"{name_part}-data-assessment{suffix}.pdf"
