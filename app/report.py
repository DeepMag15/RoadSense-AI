"""PDF inspection report (reportlab + matplotlib)."""
from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,  # noqa: E402
                                Spacer, Table, TableStyle)

from .config import CLASSES, OUTPUT_DIR, SEVERITY_LEVELS  # noqa: E402

INK = colors.HexColor("#0b0b0b")
MUTED = colors.HexColor("#52514e")
LINE = colors.HexColor("#e4e2dc")
ACCENT = colors.HexColor("#1c5cab")

ss = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=ss["Title"], fontSize=20, leading=24, textColor=INK, alignment=0, spaceAfter=2)
H2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=13, textColor=INK, spaceBefore=10, spaceAfter=6)
BODY = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9, leading=12, textColor=INK)
KPI = ParagraphStyle("k", parent=BODY, leading=25)
SMALL = ParagraphStyle("s", parent=BODY, fontSize=8, leading=10, textColor=MUTED)


def _chart_png(fig) -> io.BytesIO:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=180, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def _bar_charts(summary) -> io.BytesIO:
    fig, axes = plt.subplots(1, 2, figsize=(9, 2.8))
    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)
        ax.spines[["left", "bottom"]].set_color("#c3c2b7")
        ax.tick_params(colors="#52514e", labelsize=8)
        ax.grid(axis="x", color="#ecebe6", linewidth=0.8)
        ax.set_axisbelow(True)
    cls = list(CLASSES)
    vals = [summary["by_class"].get(c, 0) for c in cls]
    axes[0].barh([CLASSES[c]["label"] for c in cls][::-1], vals[::-1],
                 color=[CLASSES[c]["color"] for c in cls][::-1], height=0.6)
    axes[0].set_title("Defects by type", fontsize=10, loc="left", color="#0b0b0b")
    sev = list(SEVERITY_LEVELS)[::-1]  # Minor, Moderate, Critical
    sv = [summary["by_severity"].get(s, 0) for s in sev]
    axes[1].barh(sev, sv, color=[SEVERITY_LEVELS[s]["color"] for s in sev], height=0.6)
    axes[1].set_title("Defects by severity", fontsize=10, loc="left", color="#0b0b0b")
    for ax, v in ((axes[0], vals[::-1]), (axes[1], sv)):
        for i, x in enumerate(v):
            ax.text(x + max(max(v), 1) * 0.02, i, str(x), va="center", fontsize=8, color="#0b0b0b")
        ax.set_xlim(0, max(max(v), 1) * 1.15)
    fig.tight_layout()
    return _chart_png(fig)


def _timeline_chart(timeline) -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(9, 2.2))
    t = [p["t"] for p in timeline]
    bottom = [0] * len(t)
    width = (t[1] - t[0]) * 0.9 if len(t) > 1 else 0.5
    for s in list(SEVERITY_LEVELS)[::-1]:
        v = [p[s] for p in timeline]
        ax.bar(t, v, bottom=bottom, width=width, color=SEVERITY_LEVELS[s]["color"], label=s, linewidth=0)
        bottom = [a + b for a, b in zip(bottom, v)]
    ax.set_xlabel("Video time (s)", fontsize=8, color="#52514e")
    ax.set_ylabel("Defects in view", fontsize=8, color="#52514e")
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(colors="#52514e", labelsize=8)
    ax.legend(fontsize=7, frameon=False, ncol=3, loc="upper right")
    fig.tight_layout()
    return _chart_png(fig)


def _kv_table(rows, col_widths):
    t = Table(rows, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
        ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
    ]))
    return t


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 10 * mm, "RoadSense AI - automated road damage inspection report")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build_pdf(insp: dict, frames: list[dict], dets: list[dict]) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm, title=f"Inspection {insp['id']}")
    s = insp["summary"] or {}
    story = [Paragraph("Road Damage Inspection Report", H1),
             Paragraph(f"{insp.get('name') or 'Inspection'} &middot; ID {insp['id']} &middot; {insp['created_at']}", SMALL),
             Spacer(1, 8)]

    sev = s.get("by_severity", {})
    rci_color = _rci_color(s.get("rci", 100))
    headline = Table([[
        Paragraph(f"<font size=8 color='#52514e'>ROAD CONDITION INDEX</font><br/>"
                  f"<font size=22 color='{rci_color}'><b>{s.get('rci', 0):.0f}</b></font>"
                  f"<font size=9> / 100</font><br/><font size=9>{s.get('rating', '')}</font>", KPI),
        Paragraph(f"<font size=8 color='#52514e'>DEFECTS DETECTED</font><br/><font size=22><b>{s.get('total', 0)}</b></font>", KPI),
        Paragraph(f"<font size=8 color='#52514e'>SEVERITY</font><br/>"
                  f"<font color='#d03b3b'>&#9632;</font> Critical <b>{sev.get('Critical', 0)}</b><br/>"
                  f"<font color='#fab219'>&#9632;</font> Moderate <b>{sev.get('Moderate', 0)}</b><br/>"
                  f"<font color='#0ca30c'>&#9632;</font> Minor <b>{sev.get('Minor', 0)}</b>", BODY),
        Paragraph(f"<font size=8 color='#52514e'>REPAIR PRIORITY</font><br/><b>{s.get('priority', '')}</b>", BODY),
    ]], colWidths=[44 * mm, 36 * mm, 38 * mm, 56 * mm])
    headline.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.6, LINE),
                                  ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 6),
                                  ("BOTTOMPADDING", (0, 0), (-1, -1), 8), ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
    story += [headline, Spacer(1, 10)]

    meta = [["Road / segment", insp.get("road_name") or "-"], ["Location", insp.get("location") or "-"],
            ["GPS", f"{insp['lat']:.5f}, {insp['lon']:.5f}" if insp.get("lat") is not None else "-"],
            ["Media", f"{insp['media_type']} - {s.get('frames_analyzed', 0)} frame(s) analysed, "
                      f"{s.get('frames_with_damage', 0)} with damage"],
            ["Settings", f"confidence >= {insp['settings'].get('conf')}, view = {insp['settings'].get('view')}"],
            ["Models", "YOLOv8s RDD2022 detector + YOLOv8 pothole detector + YOLOv8s-seg pothole segmenter"]]
    if s.get("video"):
        v = s["video"]
        meta.insert(4, ["Video", f"{v['duration']} s @ {v['fps']} fps, {v['sampled_frames']} frames sampled, "
                                 f"{v['tracks_confirmed']} unique defects tracked"])
    story += [Paragraph("Inspection details", H2), _kv_table(meta, [40 * mm, 134 * mm])]

    story += [Paragraph("Damage analysis", H2), Image(_bar_charts(s), width=174 * mm, height=54 * mm)]
    if s.get("timeline"):
        story += [Paragraph("Damage along the route", H2), Image(_timeline_chart(s["timeline"]), width=174 * mm, height=43 * mm)]

    # class x severity matrix
    rows = [["Damage type", "Code", "Critical", "Moderate", "Minor", "Total"]]
    for c, spec in CLASSES.items():
        m = s.get("matrix", {}).get(c, {})
        rows.append([spec["label"], spec["code"], m.get("Critical", 0), m.get("Moderate", 0), m.get("Minor", 0),
                     sum(m.values()) if m else 0])
    t = Table(rows, colWidths=[52 * mm, 20 * mm, 25 * mm, 25 * mm, 25 * mm, 25 * mm])
    t.setStyle(TableStyle([("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5), ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
                           ("TEXTCOLOR", (0, 0), (-1, 0), MUTED), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
                           ("ALIGN", (2, 0), (-1, -1), "RIGHT"), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story += [Spacer(1, 6), t]

    # repair work list
    story += [PageBreak(), Paragraph("Repair work list (most severe first)", H2)]
    rows = [["#", "Type", "Severity", "Score", "Frame / time", "Recommended action"]]
    fmap = {f["id"]: f for f in frames}
    for i, d in enumerate(sorted(dets, key=lambda d: -d["score"])[:60], 1):
        f = fmap.get(d["frame_id"], {})
        where = f"{d['t_sec']:.1f} s" if d.get("t_sec") is not None else (f.get("source_name") or "")[:22]
        rows.append([i, CLASSES[d["cls"]]["label"], d["severity"], f"{d['score']:.0f}", where,
                     Paragraph(d["recommendation"], SMALL)])
    t = Table(rows, colWidths=[8 * mm, 32 * mm, 20 * mm, 13 * mm, 36 * mm, 65 * mm], repeatRows=1)
    style = [("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8), ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
             ("TEXTCOLOR", (0, 0), (-1, 0), MUTED), ("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE),
             ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
    for r, row in enumerate(rows[1:], 1):
        style.append(("TEXTCOLOR", (2, r), (2, r), colors.HexColor(_sev_text(row[2]))))
        style.append(("FONT", (2, r), (2, r), "Helvetica-Bold", 8))
    t.setStyle(TableStyle(style))
    story.append(t)

    # annotated evidence (frames with the worst defects first)
    worst = {}
    for d in dets:
        worst[d["frame_id"]] = max(worst.get(d["frame_id"], 0), d["score"])
    ranked = sorted((f for f in frames if f["id"] in worst), key=lambda f: -worst[f["id"]])[:8]
    if ranked:
        story += [PageBreak(), Paragraph("Annotated evidence", H2)]
        for f in ranked:
            p = OUTPUT_DIR / f["annotated_path"]
            ratio = f["height"] / f["width"]
            w = 174 * mm
            h = min(w * ratio, 105 * mm)
            w = h / ratio
            cap = f"{f.get('source_name') or ''}" + (f" @ {f['t_sec']:.1f} s" if f.get("t_sec") is not None else "")
            story.append(KeepTogether([Image(str(p), width=w, height=h), Paragraph(cap, SMALL), Spacer(1, 8)]))

    story += [Spacer(1, 6), Paragraph(
        "Methodology: defects are detected by a YOLOv8s model fine-tuned on the RDD2022 dataset (D00/D10/D20/D40). "
        "A second YOLOv8 pothole detector covers close-up and handheld photos, and a YOLOv8-seg model verifies "
        "and outlines every pothole. "
        "Severity (0-100) combines damage type, perspective-corrected extent, pothole depth cue / crack width, "
        "and detector confidence; &ge;60 = Critical, 30-59 = Moderate, &lt;30 = Minor. The Road Condition Index is a "
        "PCI-style aggregate (100 = perfect). Results are decision support and should be confirmed in the field.", SMALL)]

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def _rci_color(rci: float) -> str:
    return "#0ca30c" if rci >= 70 else "#c98500" if rci >= 40 else "#d03b3b"


def _sev_text(s: str) -> str:
    return {"Critical": "#b42f2f", "Moderate": "#9a6a00", "Minor": "#0a7d0a"}.get(s, "#0b0b0b")
