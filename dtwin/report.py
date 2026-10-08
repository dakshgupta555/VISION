"""
Downloadable condition report (PDF), built from any loaded dataset.

    python -m dtwin.report showcase                    # -> results/showcase_report.pdf
    python -m dtwin.report showcase --t "2026-04-23 15:00" -o out.pdf

The report describes the twin as of an instant (default: the last sample), so
a report taken mid-replay matches what the dashboard shows at that cursor.
Charts use reportlab's own graphics; no plotting library is needed.

Sections: header and data source, summary, insulation life, fault log,
thermal trend, cooling condition, protection-setting comparison, and the
ingestion mapping report as an appendix.
"""

import argparse
import io
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

INK = colors.HexColor("#0b0f14")
ACCENT = colors.HexColor("#0891b2")
DIM = colors.HexColor("#64748b")
OK, WARN, ALARM = colors.HexColor("#16a34a"), colors.HexColor("#d97706"), colors.HexColor("#dc2626")
GRID = colors.HexColor("#cbd5e1")

SS = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=SS["Heading1"], fontSize=16, spaceAfter=4, textColor=INK)
H2 = ParagraphStyle("h2", parent=SS["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4, textColor=INK)
BODY = ParagraphStyle("body", parent=SS["BodyText"], fontSize=8.5, leading=11)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=7.5, leading=9.5, textColor=DIM)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=7.5, leading=9)
MONO = ParagraphStyle("mono", parent=BODY, fontName="Courier", fontSize=7.5, leading=9)

_GLYPHS = {"₹": "Rs ", "−": "-", "≤": "<=", "≥": ">=", "→": "->", "×": "x", "–": "-", "—": "-",
           "√": "sqrt", "²": "2", "₂": "2", "σ": "sd", "▸": ">", "▾": "v", "◂": "<"}


def _s(x):
    """Text safe for the base-14 fonts (plain table cells)."""
    t = "" if x is None else str(x)
    for a, b in _GLYPHS.items():
        t = t.replace(a, b)
    return t


def _e(x):
    """The same, escaped for a Paragraph, which parses markup."""
    return _s(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _f(v, d=1):
    return "-" if v is None or (isinstance(v, float) and not np.isfinite(v)) else f"{v:.{d}f}"


def _table(rows, widths, header=True, zebra=True, align_right=()):
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    st = [("FONT", (0, 0), (-1, -1), "Helvetica", 7.5),
          ("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("LINEBELOW", (0, 0), (-1, 0), 0.6, INK) if header else ("TOPPADDING", (0, 0), (0, 0), 2),
          ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("TOPPADDING", (0, 0), (-1, -1), 2)]
    if header:
        st.append(("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5))
    if zebra:
        for r in range(1, len(rows)):
            if r % 2 == 0:
                st.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#f1f5f9")))
    for c in align_right:
        st.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
    t.setStyle(TableStyle(st))
    return t


def _chart(title, series, y_label, ref_lines=(), height=62 * mm, width=170 * mm):
    """series: list of (name, xs, ys, color, width, dashed). Title, then a
    legend row, then the plot."""
    d = Drawing(width, height)
    lp = LinePlot()
    lp.x, lp.y = 30, 16
    lp.width, lp.height = width - 40, height - 46
    data, styles = [], []
    xmin = min(min(s[1]) for s in series)
    xmax = max(max(s[1]) for s in series)
    for name, xs, ys, col, w, dash in series:
        data.append(list(zip(xs, ys)))
        styles.append((col, w, dash))
    for y, col, name in ref_lines:
        data.append([(xmin, y), (xmax, y)])
        styles.append((col, 0.6, (3, 2)))
    lp.data = data
    for k, (col, w, dash) in enumerate(styles):
        lp.lines[k].strokeColor = col
        lp.lines[k].strokeWidth = w
        if dash:
            lp.lines[k].strokeDashArray = dash
    lp.xValueAxis.valueMin, lp.xValueAxis.valueMax = xmin, xmax
    lp.xValueAxis.labels.fontSize = 6.5
    lp.yValueAxis.labels.fontSize = 6.5
    lp.xValueAxis.labelTextFormat = "d%.0f"
    lp.yValueAxis.gridStrokeColor = GRID
    lp.yValueAxis.visibleGrid = True
    d.add(lp)
    d.add(String(0, height - 9, _s(title), fontName="Helvetica-Bold", fontSize=8.5, fillColor=INK))
    d.add(String(0, lp.y + lp.height + 4, _s(y_label), fontName="Helvetica", fontSize=6.5, fillColor=DIM))
    lg = Legend()
    lg.x, lg.y = 30, height - 15
    lg.boxAnchor = "nw"
    lg.alignment = "right"
    lg.columnMaximum = 1
    lg.fontName = "Helvetica"
    lg.fontSize = 6.5
    lg.dx, lg.dy = 8, 3
    lg.dxTextSpace = 3
    lg.deltax = 88
    lg.strokeWidth = 0
    lg.colorNamePairs = [(s[3], _s(s[0])) for s in series] + [(c, _s(n)) for _, c, n in ref_lines]
    d.add(lg)
    return d


def _xy(index, values, t0, max_points=700):
    v = np.asarray(values, float)
    x = (index - t0).total_seconds().values / 86400.0
    step = max(1, len(v) // max_points)
    x, v = x[::step], v[::step]
    m = np.isfinite(v)
    return list(x[m]), list(v[m])


def build_pdf(tw, t=None) -> bytes:
    story, footer, meta = build_story(tw, t)
    return render(story, footer, meta)


def render(story, footer, meta) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm, **meta)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


def build_story(tw, t=None):
    i = tw.locate(t)
    s = tw.state(i)
    r, np_, rep = tw.rows, tw.np_, tw.report
    ts = r.index[i]
    t0 = r.index[0]
    E = []

    # ---- header -------------------------------------------------------------
    E.append(Paragraph(_e(f"Transformer condition report - {np_.tag}"), H1))
    E.append(Paragraph(_e(f"As of {ts:%d %b %Y %H:%M}, covering {t0:%d %b %Y %H:%M} to "
                          f"{ts:%d %b %Y %H:%M} ({(ts - t0).total_seconds() / 86400:.1f} days). "
                          f"Generated {datetime.now():%d %b %Y %H:%M}."), SMALL))
    E.append(Spacer(1, 4))
    ds = s["data_source"]
    E.append(_table([
        ["Nameplate", "", "Data source", ""],
        ["Rating", _s(f"{np_.rating_kva:.0f} kVA ({ds['rating_how']})"), "File", _s(Path(rep.source).name)],
        ["Voltage", f"{np_.hv_kv:g} kV / {np_.lv_v:g} V, {np_.cooling}", "Rows",
         _s(f"read {ds['rows']['read']}, kept {ds['rows']['kept']}")],
        ["Rated current", f"{np_.rated_current_a:.1f} A", "Cadence",
         _s(f"{ds['cadence_min']} min" + (f", resampled to {ds['resampled_to_min']} min"
                                          if ds.get("resampled_to_min") else ""))],
        ["Top-oil / hot-spot rise", f"{np_.top_oil_rise_rated_k:g} K / {np_.hotspot_rise_rated_k:g} K",
         "Absent channels", _s(", ".join(ds["absent"]) or "none")],
        ["Oil time constant", f"{np_.tau_oil_h:g} h", "Thermal diagnosis",
         "available" if ds["thermal_available"] else "OFF (missing channel)"],
    ], [34 * mm, 52 * mm, 30 * mm, 58 * mm], header=True, zebra=False))
    for w in ds["warnings"]:
        E.append(Paragraph(_e("Data warning: " + w), ParagraphStyle("w", parent=SMALL, textColor=WARN)))

    # ---- summary ------------------------------------------------------------
    lvl = s["status"]["level"]
    tone = {"ok": OK, "warn": WARN, "alarm": ALARM}[lvl]
    E.append(Paragraph("Summary", H2))
    top = s["alerts_live"][0]["headline"] if s["alerts_live"] else "No active alerts."
    label = {"ok": "NORMAL", "warn": "ATTENTION", "alarm": "ALARM"}[lvl]
    E.append(Paragraph(f"<b>Status: {label}</b> &nbsp; Health index {_f(s['health']['index'], 0)}/100"
                       f" &nbsp; Active alerts {len(s['alerts_live'])}",
                       ParagraphStyle("st", parent=BODY, fontSize=10, textColor=tone)))
    E.append(Paragraph(_e(top), BODY))
    for a in s["alerts_live"][1:]:
        E.append(Paragraph(_e("Also: " + a["headline"]), BODY))
    E.append(Spacer(1, 4))
    rows = [["Health component", "Value", "Score", "100 at", "0 at (alarm)", "Weight"]]
    for c in s["health"]["components"]:
        rows.append([_s(c["label"]), _s(f"{_f(c['value'], 3)} {c['unit']}"), _f(c["score"], 0),
                     _s(f"<= {c['good']:g}"), _s(f"{c['bad']:g}"), f"{c['weight']:.2f}"])
    E.append(_table(rows, [58 * mm, 28 * mm, 16 * mm, 22 * mm, 26 * mm, 16 * mm], align_right=(1, 2, 5)))
    cd = s["countdown"]
    h, fx = cd["hotspot"], cd["fixed"]
    E.append(Spacer(1, 3))
    E.append(Paragraph(_e(
        f"Projection at present load {100 * cd['load_pu']:.0f}% and ambient {cd['ambient_c']:.1f} C "
        f"(cooling factor {cd['cooling_factor_used']:.3f}): hot-spot settles at {h['settles_at_c']} C, "
        + ("limit exceeded now" if h["already_above"] else
           f"reaches {h['limit_c']:.0f} C in {h['minutes']:.0f} min" if h["minutes"] is not None
           else f"stays under {h['limit_c']:.0f} C")
        + (f"; shed {h['shed_pct']:.0f}% to stay under it." if h["shed_pct"] else ".")
        + f" Existing {fx['limit_c']:.0f} C oil trip, for comparison: "
        + ("already exceeded." if fx["already_above"] else
           f"{fx['minutes']:.0f} min." if fx["minutes"] is not None else "not reached.")), BODY))
    E.append(Paragraph("Recommendations", H2))
    for rec in s["recommendations"]:
        E.append(Paragraph(_e(f"P{rec['priority']}  {rec['text']}  ({rec['because']})"), BODY))

    # ---- insulation life ----------------------------------------------------
    L = s["life"]
    E.append(Paragraph("Insulation life consumed", H2))
    lrows = [["Window", "Aged hours", "Real hours", "Rate (x rated)", "Value (Rs)"]]
    for lab, key in (("Last hour", "last_1h"), ("Last 24 h", "last_24h"), ("Whole period", "since_start")):
        x = L[key]
        lrows.append([lab, _f(x["aged_h"], 2), _f(x["real_h"], 1), _f(x["rate_pu"], 3), _f(x["inr"], 0)])
    E.append(_table(lrows, [40 * mm, 30 * mm, 30 * mm, 30 * mm, 30 * mm], align_right=(1, 2, 3, 4)))
    E.append(Paragraph(_e(f"IEEE C57.91 ageing acceleration, thermally upgraded paper, reference hot-spot "
                          f"{np_.reference_hotspot_c:.0f} C. Rs {L['replacement_cost_inr']:,.0f} replacement "
                          f"cost / {L['normal_life_hours']:,.0f} h rated life = Rs "
                          f"{L['cost_per_aged_hour_inr']:.2f} per aged hour."), SMALL))

    # ---- fault log ----------------------------------------------------------
    E.append(Paragraph("Fault log", H2))
    log = [a for a in tw.alerts if a["start"] <= ts]
    if not log:
        E.append(Paragraph("No events in the period.", BODY))
    else:
        frows = [["Event", "Family", "Start", "End", "Diagnosis", "Action"]]
        for a in log:
            end = "active" if a["end"] >= ts else f"{a['end']:%d %b %H:%M}"
            frows.append([Paragraph(_e(a["what"]), CELL), a["family"], f"{a['start']:%d %b %H:%M}", end,
                          Paragraph(_e(a["why"]), CELL), Paragraph(_e(a["action"]), CELL)])
        E.append(_table(frows, [34 * mm, 13 * mm, 19 * mm, 19 * mm, 50 * mm, 39 * mm]))
        E.append(Paragraph(_e(f"{len(log)} episodes. The fixed {np_.trip_oil_temp_c:.0f} C oil-trip "
                              "comparison is not an event and is not listed."), SMALL))

    # ---- charts -------------------------------------------------------------
    E.append(PageBreak())
    E.append(Paragraph("Thermal trend", H2))
    w = r.iloc[:i + 1]
    series = [("oil measured", *_xy(w.index, w.oil_c, t0), ACCENT, 0.8, None),
              ("oil twin", *_xy(w.index, w.pred_oil_c, t0), INK, 0.5, (2, 1)),
              ("hot-spot", *_xy(w.index, w.hotspot_c, t0), colors.HexColor("#94a3b8"), 0.5, None),
              ("ambient", *_xy(w.index, w.ambient_c, t0), colors.HexColor("#475569"), 0.5, None)]
    series = [x for x in series if len(x[1])]
    E.append(_chart("Top oil measured vs twin prediction, hot-spot and ambient (C)", series, "C",
                    ref_lines=[(np_.trip_oil_temp_c, DIM, f"fixed trip {np_.trip_oil_temp_c:.0f} C")]))
    E.append(Paragraph("Measured top oil should track the twin's prediction. A widening gap with load is a "
                       "cooling problem; a constant gap is an instrument offset. Days since the start of the "
                       "record on the horizontal axis.", SMALL))
    E.append(Paragraph("Cooling condition", H2))
    if ds["thermal_available"] and w.slope.notna().any():
        E.append(_chart("Rise-fit slope: measured oil rise / predicted oil rise (1.00 = as designed)",
                        [("slope", *_xy(w.index, w.slope, t0), ACCENT, 0.8, None)], "slope",
                        ref_lines=[(1.0, DIM, "as designed"), (1.06, ALARM, "cooling alarm 1.06")]))
        E.append(Paragraph("Above 1.06 for three consecutive hourly fits is the cooling alarm. Brief spikes "
                           "that coincide with a temperature step are the 24 h fit window straddling an "
                           "instrument change (see the fault log), not a change in the transformer.", SMALL))
        E.append(_chart("Thermal residual: measured - twin top oil (K)",
                        [("residual", *_xy(w.index, w.residual_k, t0), ACCENT, 0.6, None)], "K",
                        ref_lines=[(0.0, DIM, "zero")], height=45 * mm))
    else:
        E.append(Paragraph("Not available: the file has no measured ambient or oil temperature, so the "
                           "cooling condition cannot be judged.", BODY))

    # ---- protection comparison ---------------------------------------------
    A = s["ambient_table"]
    prot = [Paragraph("Protection-setting comparison", H2),
            Paragraph(_e(f"Steady load each protection philosophy permits. A fixed {A['fixed_trip_c']:.0f} C "
                         "oil trip carries the ambient inside it; a trip on rise over measured ambient "
                         f"({A['rise_trip_k']:.0f} K, the protection the {A['fixed_trip_c']:.0f} C setting gives "
                         f"at 30 C) or on hot-spot ({A['hotspot_limit_c']:.0f} C) does not."), BODY)]
    prow = [["Ambient", f"Fixed {A['fixed_trip_c']:.0f} C oil", f"Rise {A['rise_trip_k']:.0f} K",
             f"Hot-spot {A['hotspot_limit_c']:.0f} C"]]
    for x in A["rows"]:
        prow.append([f"{x['ambient_c']} C" + ("  (now)" if x["current"] else ""),
                     f"{x['fixed_trip_pu']:.2f} pu", f"{x['rise_trip_pu']:.2f} pu", f"{x['hotspot_pu']:.2f} pu"])
    prot.append(_table(prow, [40 * mm, 40 * mm, 40 * mm, 40 * mm], align_right=(1, 2, 3)))
    E.append(KeepTogether(prot))

    # ---- appendix: mapping report ------------------------------------------
    E.append(PageBreak())
    E.append(Paragraph("Appendix - ingestion mapping report", H2))
    E.append(Paragraph(_e(f"Source {Path(rep.source).name}. Separator '{rep.separator}', decimal '{rep.decimal}', "
                          f"encoding {rep.encoding}, header on line {rep.header_row + 1}"
                          + (f", {len(rep.preamble)} preamble lines skipped" if rep.preamble else "")
                          + f". Timestamp: {rep.timestamp.get('source')} -> {rep.timestamp.get('how')}."),
                       BODY))
    mrows = [["Channel", "Source column", "Matched by", "Conf.", "Unit", "Factor", "Unit reason"]]
    for k in ["IR", "IY", "IB", "VR", "VY", "VB", "oil_temp", "ambient_temp"]:
        if k in rep.mapping:
            m, u = rep.mapping[k], rep.units.get(k, {})
            mrows.append([k, Paragraph(_e(m["source"]), CELL), Paragraph(_e(m["how"]), CELL), m["confidence"],
                          _s(u.get("unit")), f"{u.get('factor', 1):g}", Paragraph(_e(u.get("how", "")), CELL)])
        else:
            mrows.append([k, Paragraph(_e("DERIVED: " + rep.derived[k]) if k in rep.derived else "ABSENT", CELL),
                          "", "", "", "", ""])
    E.append(_table(mrows, [20 * mm, 30 * mm, 34 * mm, 12 * mm, 12 * mm, 14 * mm, 52 * mm]))
    E.append(Spacer(1, 4))
    dropped = ", ".join(f"{v} {k}" for k, v in rep.rows.get("dropped", {}).items() if v) or "none"
    filled = ", ".join(f"{k} {v}" for k, v in rep.filled.items() if v) or "none"
    for line in (f"Rows read {rep.rows.get('read')}, kept {rep.rows.get('kept')}; dropped: {dropped}.",
                 f"Out-of-order rows sorted: {rep.rows.get('moved', 0)}.",
                 f"Cadence {rep.cadence.get('median_min')} min median, "
                 + ("regular" if rep.cadence.get("regular") else "irregular")
                 + (f", resampled to {rep.cadence['resampled_to_min']} min" if rep.cadence.get("resampled_to_min") else "")
                 + (f"; {rep.cadence['gaps']} gaps over 3 samples ({rep.cadence['gap_hours']} h)"
                    if rep.cadence.get("gaps") else "") + ".",
                 f"Short holes interpolated (<= 2 samples): {filled}.",
                 f"Columns ignored: {', '.join(map(str, rep.ignored_columns)) or 'none'}."):
        E.append(Paragraph(_e(line), BODY))
    for wn in rep.warnings:
        E.append(Paragraph(_e("Warning: " + wn), ParagraphStyle("w2", parent=BODY, textColor=WARN)))
    E.append(Spacer(1, 6))
    E.append(Paragraph("Correct any wrong guess with python -m dtwin.ingest FILE --map CHANNEL=COLUMN "
                       "--unit CHANNEL=UNIT --dayfirst yes|no --kva RATING --save-sidecar.", SMALL))

    def footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(DIM)
        canvas.drawString(18 * mm, 9 * mm, _s(f"{np_.tag} - {np_.rating_kva:.0f} kVA - as of {ts:%d %b %Y %H:%M}"))
        canvas.drawRightString(A4[0] - 18 * mm, 9 * mm, f"page {doc_.page}")
        canvas.restoreState()

    return E, footer, {"title": f"{np_.tag} condition report", "author": "Transformer digital twin"}


def main():
    from .api.engine import get_twin
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--t", default=None, help="timestamp or sample index; default the last sample")
    ap.add_argument("--kva", type=float, default=None)
    ap.add_argument("-o", "--out", default=None)
    a = ap.parse_args()
    t = int(a.t) if a.t and a.t.isdigit() else a.t
    pdf = build_pdf(get_twin(a.scenario, a.kva), t)
    out = Path(a.out or f"results/{a.scenario}_report.pdf")
    out.write_bytes(pdf)
    print(f"wrote {out} ({len(pdf) / 1024:.0f} kB)")


if __name__ == "__main__":
    main()
