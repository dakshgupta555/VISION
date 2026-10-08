"""
Defensive ingestion: turn a CSV we have never seen into the twin's eight
canonical channels, and say exactly what was decided along the way.

    python -m dtwin.ingest path/to/file.csv            # print the mapping report
    python -m dtwin.ingest file.csv --map oil_temp=ColX --unit IR=mA --dayfirst yes --kva 63

Every guess is reported and every guess can be overridden, either on the
command line or with a sidecar file next to the CSV, <name>.mapping.json:

    {"map": {"oil_temp": "T1", "ambient_temp": "T2"},
     "units": {"IR": "mA"}, "dayfirst": true, "rating_kva": 63}

Pipeline
--------
  1. bytes -> text      utf-8 (with BOM) or latin-1
  2. dialect            separator by consistency of field counts; decimal comma
  3. header             first row with the modal field count that is mostly
                        non-numeric; anything above it is a preamble, which is
                        searched for a kVA rating and a transformer tag
  4. columns            name tokens first (I_R, Current_R, R_Phase_Current, IR,
                        Phase A, L1, OilTemperature, OTI, T_amb ...), fuzzy
                        match for misspellings, and content inference for
                        generic names (col1, Unnamed: 3, A..J)
  5. units              from the name when it says, otherwise from magnitude:
                        mA vs A, kV vs V, line-to-line vs line-to-neutral,
                        kelvin vs celsius, W vs kW
  6. time               one timestamp column, or date + time columns, or epoch
                        seconds/milliseconds; day-first vs month-first chosen
                        by which reading gives a regular cadence
  7. rows               unparseable timestamps and blank rows dropped, sorted,
                        duplicates merged, jittered cadence regularised
  8. holes              1-2 sample gaps interpolated; longer runs left missing
                        on purpose, because a channel missing for hours is the
                        dead-channel signal the validator looks for
  9. missing channels   reported as absent, never invented silently: no
                        ambient -> an assumed constant and the thermal
                        diagnosis switched off; no currents -> derived from
                        kVA/kW if present (balanced, so unbalance unavailable)
"""

import argparse
import difflib
import io
import json
import re
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

CURRENTS = ["IR", "IY", "IB"]
VOLTAGES = ["VR", "VY", "VB"]
TEMPS = ["oil_temp", "ambient_temp"]
CANON = CURRENTS + VOLTAGES + TEMPS

ASSUMED_AMBIENT_C = 30.0
NICE_CADENCES_MIN = [1, 2, 5, 10, 15, 20, 30, 60, 120]
SHORT_GAP_SAMPLES = 2


@dataclass
class Report:
    source: str = ""
    encoding: str = ""
    separator: str = ""
    decimal: str = "."
    header_row: int = 0
    preamble: list = field(default_factory=list)
    mapping: dict = field(default_factory=dict)      # canon -> {source, how, confidence}
    units: dict = field(default_factory=dict)        # canon -> {unit, factor, how}
    timestamp: dict = field(default_factory=dict)
    cadence: dict = field(default_factory=dict)
    rows: dict = field(default_factory=dict)
    filled: dict = field(default_factory=dict)
    absent: list = field(default_factory=list)
    derived: dict = field(default_factory=dict)
    ignored_columns: list = field(default_factory=list)
    nameplate: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    def as_dict(self):
        return asdict(self)

    def text(self):
        L = [f"INGEST  {self.source}",
             f"  dialect     sep {self.separator!r}  decimal {self.decimal!r}  encoding {self.encoding}"
             f"  header on line {self.header_row + 1}"
             + (f"  ({len(self.preamble)} preamble lines skipped)" if self.preamble else "")]
        if self.nameplate:
            L.append(f"  nameplate   {self.nameplate.get('rating_kva')} kVA  ({self.nameplate.get('how')})"
                     + (f"  tag {self.nameplate['tag']}" if self.nameplate.get("tag") else ""))
        ts = self.timestamp
        L.append(f"  timestamp   {ts.get('source')}  ->  {ts.get('how')}")
        c = self.cadence
        L.append(f"  cadence     {c.get('median_min')} min median"
                 + ("  regular" if c.get("regular") else "  IRREGULAR")
                 + (f"  -> resampled to {c['resampled_to_min']} min" if c.get("resampled_to_min") else "")
                 + (f"  ({c['gaps']} gaps > 3x cadence, {c['gap_hours']} h missing)" if c.get("gaps") else ""))
        L.append("  mapping")
        for k in CANON:
            if k in self.mapping:
                m, u = self.mapping[k], self.units.get(k, {})
                L.append(f"    {k:<13} <- {str(m['source'])!r:<26} {m['how']:<22} {m['confidence']:<6}"
                         f" unit {u.get('unit', '?'):<5} x{u.get('factor', 1):<9g} {u.get('how', '')}")
            elif k in self.derived:
                L.append(f"    {k:<13} <- DERIVED  {self.derived[k]}")
            else:
                L.append(f"    {k:<13} <- ABSENT")
        if self.ignored_columns:
            L.append(f"  ignored     {', '.join(map(str, self.ignored_columns))}")
        r = self.rows
        L.append(f"  rows        read {r.get('read')}  kept {r.get('kept')}  dropped "
                 + (", ".join(f"{v} {k}" for k, v in r.get("dropped", {}).items() if v) or "none"))
        if r.get("moved"):
            L.append(f"              {r['moved']} rows were out of order and were sorted")
        if any(self.filled.values()):
            L.append("  holes       interpolated (<= 2 samples): "
                     + ", ".join(f"{k} {v}" for k, v in self.filled.items() if v))
        for w in self.warnings:
            L.append(f"  WARNING     {w}")
        return "\n".join(L)


# --------------------------------------------------------------------------- #
# 1-3. text, dialect, header
# --------------------------------------------------------------------------- #
def _decode(raw: bytes):
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            txt = raw.decode(enc)
            if enc == "utf-16" and "\x00" in txt:
                continue
            return txt, enc
        except UnicodeError:
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1"


_NUM = re.compile(r"^\s*[-+]?(\d+([.,]\d*)?|[.,]\d+)([eE][-+]?\d+)?\s*[A-Za-z°%]*\s*$")


def _is_num(cell):
    return bool(_NUM.match(cell)) if cell.strip() else False


def _sniff(lines):
    body = [ln for ln in lines if ln.strip()][-200:]
    best, best_score = ",", -1
    for sep in (",", ";", "\t", "|"):
        counts = [ln.count(sep) for ln in body]
        if not counts or max(counts) == 0:
            continue
        mode = max(set(counts), key=counts.count)
        score = counts.count(mode) / len(counts) * (mode > 0) + 0.01 * mode
        if score > best_score:
            best, best_score = sep, score
    return best


def _split(line, sep):
    return next(iter(pd.read_csv(io.StringIO(line), sep=sep, header=None, dtype=str,
                                 keep_default_na=False).itertuples(index=False)), ())


def _find_header(lines, sep):
    nonblank = [i for i, ln in enumerate(lines) if ln.strip()]
    tail = nonblank[-200:]
    counts = {i: len(_split(lines[i], sep)) for i in tail}
    mode = max(set(counts.values()), key=list(counts.values()).count)
    for i in nonblank:
        cells = list(_split(lines[i], sep))
        if len(cells) != mode:
            continue
        filled = [c for c in cells if str(c).strip()]
        numeric = sum(_is_num(str(c)) for c in filled)
        dateish = sum(bool(re.search(r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}", str(c))) for c in filled)
        if filled and numeric + dateish < 0.5 * len(filled):
            return i, mode, True
        return i, mode, False                       # first data-shaped row is data: no header
    return 0, mode, True


def _preamble_nameplate(pre):
    txt = " ".join(pre)
    out = {}
    m = re.search(r"(\d+(?:\.\d+)?)\s*k\s*va\b", txt, re.I)
    if m:
        out["rating_kva"] = float(m.group(1))
    m = re.search(r"(?:transformer|tag|device|dt)[ _-]*(?:id|no|name)?\s*[:=,]\s*([A-Za-z0-9_\-/]+)", txt, re.I)
    if m:
        out["tag"] = m.group(1)
    return out


# --------------------------------------------------------------------------- #
# 4. column identification
# --------------------------------------------------------------------------- #
TIME_WORDS = {"timestamp", "datetime", "ts", "epoch", "unix", "logtime", "recorded"}
CUR_WORDS = {"current", "curr", "amp", "amps", "ampere", "amperes", "i", "irms", "load"}
VOLT_WORDS = {"voltage", "volt", "volts", "v", "u", "vrms", "kv"}
OIL_WORDS = {"oil", "topoil", "oti", "tto", "toil", "tot"}
AMB_WORDS = {"ambient", "amb", "air", "outdoor", "outside", "tamb", "ta", "env", "environment",
             "atmospheric", "atm", "weather"}
TEMP_WORDS = {"temp", "temperature", "t", "deg", "degc", "celsius", "tmp"}
POWER_WORDS = {"kva", "kw", "power", "apparent", "active", "va", "w", "mw", "s", "p"}
PHASE = {"r": ("RYB", 0), "red": ("RYB", 0), "y": ("RYB", 1), "yellow": ("RYB", 1),
         "blue": ("RYB", 2), "b": ("?", None), "a": ("ABC", 0), "c": ("ABC", 2),
         "l1": ("N", 0), "l2": ("N", 1), "l3": ("N", 2), "1": ("N", 0), "2": ("N", 1), "3": ("N", 2),
         "p1": ("N", 0), "p2": ("N", 1), "p3": ("N", 2), "ph1": ("N", 0), "ph2": ("N", 1), "ph3": ("N", 2)}
_COMPACT = re.compile(r"^(i|v|u)(r|y|b|a|c|l?[123])$")
_GENERIC = re.compile(r"^(col(umn)?|field|var|x|c|f|unnamed|v|value|data|ch(annel)?)?[\s_:#-]*\d+$|^[a-z]$", re.I)
ALIASES = {"IR": ["ir", "ia", "il1"], "IY": ["iy", "ib", "il2"], "IB": ["ib", "ic", "il3"],
           "VR": ["vr", "va", "vl1"], "VY": ["vy", "vb", "vl2"], "VB": ["vb", "vc", "vl3"],
           "oil_temp": ["oiltemp", "oiltemperature", "topoiltemp", "oti"],
           "ambient_temp": ["ambienttemp", "ambienttemperature", "ambtemp", "airtemp"],
           "timestamp": ["timestamp", "datetime", "date", "time"]}


def tokens(name):
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(name))
    s = s.replace("°", " deg ")
    return [t for t in re.split(r"[^A-Za-z0-9]+", s.lower()) if t]


def classify_name(name):
    """(kind, phase-info, unit-hint) from a column name alone."""
    t = tokens(name)
    ts = set(t)
    unit = None
    if ts & {"ma", "milliamp", "milliamps"}:
        unit = "mA"
    if "kv" in ts:
        unit = "kV"
    if ts & {"kelvin"} or (ts & (OIL_WORDS | AMB_WORDS | TEMP_WORDS) and t and t[-1] == "k"):
        unit = "K"
    if ts & {"f", "degf", "fahrenheit"} and ts & (OIL_WORDS | AMB_WORDS | TEMP_WORDS):
        unit = "F"
    if ts & {"kva"}:
        unit = "kVA"
    elif ts & {"kw"}:
        unit = "kW"

    if ts & TIME_WORDS or {"date", "time"} <= ts:
        return "timestamp", None, unit
    if ts == {"date"} or (t and t[0] == "date"):
        return "date", None, unit
    if ts == {"time"} or (t and t[0] == "time"):
        return "time", None, unit
    if ts & OIL_WORDS:
        return "oil_temp", None, unit
    if ts & AMB_WORDS:
        return "ambient_temp", None, unit

    kind, phase = None, None
    for tok in t:
        m = _COMPACT.match(tok)
        if m:
            kind = "current" if m.group(1) == "i" else "voltage"
            ph = m.group(2)
            phase = PHASE.get(ph if not ph[0].isdigit() else ph, PHASE.get("l" + ph[-1]))
            break
    if kind is None:
        if ts & CUR_WORDS and not ts & {"loadkva", "kva", "kw"}:
            kind = "current"
        elif ts & VOLT_WORDS:
            kind = "voltage"
        elif ts & {"kva", "kw", "power", "apparent", "active"}:
            return "power", None, unit
    if phase is None:
        cands = [tok for tok in t if tok in PHASE]
        # a trailing unit letter is a unit, not a phase: Current_B_A, Voltage_R_V
        if kind == "current" and len(cands) > 1 and cands[-1] == "a" and t[-1] == "a":
            cands = cands[:-1]
        if kind == "voltage" and t and t[-1] == "v":
            cands = [c for c in cands if c != "v"]
        if cands:
            phase = PHASE[cands[0]] if cands[0] != "b" else ("?", "b")
    if kind is None and phase is not None:
        return "phase", phase, unit                 # "Phase A", "L1": quantity from content
    if kind in ("current", "voltage"):
        return kind, phase, unit
    if ts & TEMP_WORDS:
        return "temperature", None, unit
    return None, None, unit


def _resolve_phases(items):
    """Pick the file's phase convention, then turn phase info into 0/1/2."""
    conv = {p[0] for _, p in items if p and p[0] != "?"}
    if "RYB" in conv:
        b = 2
    elif "ABC" in conv:
        b = 1
    else:
        b = 2
    out = []
    for col, p in items:
        if p is None:
            out.append((col, None))
        elif p[0] == "?":
            out.append((col, b))
        else:
            out.append((col, p[1]))
    return out


def _numeric(series, decimal):
    s = series.astype(str).str.strip()
    s = s.str.extract(r"([-+]?(?:\d+[.,]?\d*|[.,]\d+)(?:[eE][-+]?\d+)?)", expand=False)
    if decimal == ",":
        s = s.str.replace(".", "", regex=False).str.replace(",", ".", regex=False) \
            if s.str.contains(r"\d\.\d{3},", regex=True).any() else s.str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce")


# --------------------------------------------------------------------------- #
# 6. time
# --------------------------------------------------------------------------- #
def _regularity(t):
    t = t.dropna()
    if len(t) < 3:
        return 0.0
    d = np.diff(np.sort(t.values.astype("datetime64[s]").astype(np.int64)))
    d = d[d > 0]
    if len(d) == 0:
        return 0.0
    mode = pd.Series(d).mode().iloc[0]
    return float(np.mean(np.abs(d - mode) <= 0.02 * mode + 1))


def parse_time(raw, override_dayfirst=None):
    """Return (timestamps, description)."""
    s = raw.astype(str).str.strip()
    num = pd.to_numeric(s, errors="coerce")
    if num.notna().mean() > 0.9:
        med = num.median()
        if 1e9 <= med <= 4e9:
            return pd.to_datetime(num, unit="s"), "epoch seconds"
        if 1e12 <= med <= 4e12:
            return pd.to_datetime(num, unit="ms"), "epoch milliseconds"
        if 20000 <= med <= 80000:
            return pd.to_datetime(num, unit="D", origin="1899-12-30"), "Excel serial days"
    opts = [override_dayfirst] if override_dayfirst is not None else [False, True]
    best = None
    for dayfirst in opts:
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            try:
                t = pd.to_datetime(s, dayfirst=dayfirst, errors="coerce")
            except (ValueError, TypeError):
                t = pd.Series(pd.NaT, index=s.index)
            if t.isna().mean() > 0.2:
                t = pd.to_datetime(s, dayfirst=dayfirst, errors="coerce", format="mixed")
        if getattr(t.dt, "tz", None) is not None:
            t = t.dt.tz_localize(None)
        score = (t.notna().mean(), _regularity(t))
        if best is None or score > best[0]:
            best = (score, t, dayfirst)
    (ok, reg), t, dayfirst = best
    ambiguous = override_dayfirst is None and _ambiguous(s)
    how = f"text, {'day' if dayfirst else 'month'}-first"
    if override_dayfirst is not None:
        how += " (override)"
    elif ambiguous:
        how += f" (ambiguous; chosen because it gives a regular cadence, {100 * reg:.0f}% of steps equal)"
    return t, how


def _ambiguous(s):
    m = s.str.extract(r"^(\d{1,2})[/.-](\d{1,2})[/.-]\d{2,4}")
    if m.isna().all().all():
        return False
    a, b = pd.to_numeric(m[0], errors="coerce"), pd.to_numeric(m[1], errors="coerce")
    return bool(((a <= 12) & (b <= 12)).mean() > 0.5) and not ((a > 12).any() or (b > 12).any())


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def load(path, overrides=None, rating_kva=None):
    """Read any CSV. Returns (df, report). df has a DatetimeIndex named
    'timestamp' and the eight canonical columns; absent ones are NaN and
    listed in report.absent."""
    path = Path(path)
    ov = dict(overrides or {})
    side = path.with_suffix(".mapping.json")
    from_side = {}
    if side.exists():
        from_side = json.loads(side.read_text())
        ov = {**from_side, **ov}
    rep = Report(source=str(path))
    txt, rep.encoding = _decode(path.read_bytes())
    lines = txt.splitlines()
    rep.separator = ov.get("separator") or _sniff(lines)
    hdr, ncol, has_header = _find_header(lines, rep.separator)
    rep.header_row = hdr
    rep.preamble = [ln for ln in lines[:hdr] if ln.strip()]
    body = "\n".join(lines[hdr:])
    raw = pd.read_csv(io.StringIO(body), sep=rep.separator, header=0 if has_header else None,
                      dtype=str, skip_blank_lines=True, keep_default_na=True, on_bad_lines="skip",
                      engine="python")
    if not has_header:
        raw.columns = [f"col{i + 1}" for i in range(raw.shape[1])]
        rep.warnings.append("no header row found; columns identified from their content")
    raw.columns = [str(c).strip() for c in raw.columns]
    n_read = len(raw)

    # decimal comma: semicolon/tab files with numbers like 12,5
    sample = raw.head(200).astype(str)
    comma = sample.apply(lambda c: c.str.match(r"^\s*-?\d+,\d+\s*$").mean()).max()
    rep.decimal = ov.get("decimal") or ("," if rep.separator != "," and comma > 0.5 else ".")

    # ---- nameplate -----------------------------------------------------------
    np_info = _preamble_nameplate(rep.preamble)
    if rating_kva or ov.get("rating_kva"):
        how = "override" if rating_kva or (overrides or {}).get("rating_kva") else f"sidecar {side.name}"
        rep.nameplate = {"rating_kva": float(rating_kva or ov["rating_kva"]), "how": how}
    elif "rating_kva" in np_info:
        rep.nameplate = {"rating_kva": np_info["rating_kva"], "how": "read from preamble"}
    else:
        rep.nameplate = {"rating_kva": 100.0, "how": "NOT FOUND - assumed, set it"}
        rep.warnings.append("nameplate rating not in file: assumed 100 kVA. A wrong rating reads as a "
                            "thermal fault - set it with --kva or rating_kva in the sidecar")
    if np_info.get("tag"):
        rep.nameplate["tag"] = np_info["tag"]
    rated_i = rep.nameplate["rating_kva"] * 1000 / (np.sqrt(3) * 433.0)

    # ---- columns -------------------------------------------------------------
    forced = {k: v for k, v in ov.get("map", {}).items()}
    cls = {c: classify_name(c) for c in raw.columns}
    mapping, used = {}, set()

    def take(canon, col, how, conf):
        if canon in mapping or col in used:
            return
        mapping[canon] = {"source": col, "how": how, "confidence": conf}
        used.add(col)

    for canon, col in forced.items():
        if col in raw.columns:
            take(canon, col, "override", "user")
        else:
            rep.warnings.append(f"override {canon}={col!r}: no such column")

    # time columns
    ts_col = forced.get("timestamp")
    date_col = time_col = None
    if not ts_col:
        for c, (k, _, _) in cls.items():
            if k == "timestamp" and c not in used:
                ts_col = c
                break
        if not ts_col:
            dates = [c for c, (k, _, _) in cls.items() if k == "date"]
            times = [c for c, (k, _, _) in cls.items() if k == "time"]
            if dates and times:
                date_col, time_col = dates[0], times[0]
            elif dates or times:
                ts_col = (dates or times)[0]
    if not ts_col and not date_col:             # find a column whose content parses as time
        for c in raw.columns:
            if c in used:
                continue
            t, _ = parse_time(raw[c].head(200))
            if t.notna().mean() > 0.9 and t.nunique() > 0.9 * t.notna().sum():
                ts_col = c
                break
    if ts_col:
        used.add(ts_col)
    if date_col:
        used.update([date_col, time_col])

    # name-identified measurement columns
    vals = {c: _numeric(raw[c], rep.decimal) for c in raw.columns if c not in used}
    for kind_name, canon_list in (("current", CURRENTS), ("voltage", VOLTAGES)):
        items = [(c, cls[c][1]) for c in raw.columns if c not in used and cls[c][0] == kind_name]
        resolved = _resolve_phases(items)
        named = [(c, p) for c, p in resolved if p is not None]
        for c, p in named:
            take(canon_list[p], c, "name", "high")
        unphased = [c for c, p in resolved if p is None and c not in used]
        free = [k for k in canon_list if k not in mapping]
        for c, k in zip(unphased, free):
            take(k, c, "name, phase by order", "medium")
    for c in raw.columns:
        k = cls[c][0]
        if k in TEMPS and c not in used:
            take(k, c, "name", "high")

    # fuzzy match for misspellings
    for c in raw.columns:
        if c in used or cls[c][0] is not None:
            continue
        norm = "".join(tokens(c))
        for canon, al in ALIASES.items():
            if canon == "timestamp" or canon in mapping:
                continue
            if difflib.get_close_matches(norm, al, n=1, cutoff=0.8):
                take(canon, c, f"fuzzy ~{al[0]}", "medium")
                break

    # phase-only columns ("Phase A", "L1"): quantity from variability
    phase_only = _resolve_phases([(c, cls[c][1]) for c in raw.columns
                                  if c not in used and cls[c][0] == "phase"])
    for c, p in phase_only:
        v = vals[c].dropna()
        if len(v) == 0 or p is None:
            continue
        cv = v.std() / max(abs(v.mean()), 1e-9)
        kind = VOLTAGES if cv < 0.06 and v.median() > 0.1 else CURRENTS
        take(kind[p], c, f"phase name, {'voltage' if kind is VOLTAGES else 'current'} by cv {cv:.2f}",
             "medium")

    # content inference, only for generic names (never for named junk)
    generic = [c for c in raw.columns if c not in used and _GENERIC.match(c)
               and vals[c].notna().mean() > 0.8]
    _infer_by_content(generic, vals, mapping, used, take)
    temps_unknown = [c for c in raw.columns if c not in used and cls[c][0] == "temperature"]
    for c in sorted(temps_unknown, key=lambda c: -vals[c].median()):
        for k in TEMPS:
            if k not in mapping:
                take(k, c, "temperature, oil = warmer", "low")
                break

    power_cols = [c for c in raw.columns if c not in used and cls[c][0] == "power"]
    rep.ignored_columns = [c for c in raw.columns if c not in used and c not in power_cols]

    # ---- time ---------------------------------------------------------------
    dayfirst = ov.get("dayfirst")
    if date_col:
        t, how = parse_time(raw[date_col].astype(str) + " " + raw[time_col].astype(str), dayfirst)
        rep.timestamp = {"source": f"{date_col} + {time_col}", "how": how}
    elif ts_col:
        t, how = parse_time(raw[ts_col], dayfirst)
        rep.timestamp = {"source": ts_col, "how": how}
    else:
        raise ValueError("no timestamp column could be identified; set it with --map timestamp=COL")

    # ---- assemble, with units ------------------------------------------------
    out = pd.DataFrame(index=raw.index)
    for k in CANON:
        if k in mapping:
            v = vals.get(mapping[k]["source"])
            if v is None:
                v = _numeric(raw[mapping[k]["source"]], rep.decimal)
            unit, factor, how = _unit(k, v, cls[mapping[k]["source"]][2], rated_i,
                                      ov.get("units", {}).get(k))
            out[k] = v * factor if unit not in ("K", "F") else _temp_convert(v, unit)
            rep.units[k] = {"unit": unit, "factor": round(factor, 6), "how": how}
        else:
            out[k] = np.nan
    out["__t"] = t.values

    # ---- rows -----------------------------------------------------------------
    dropped = {}
    bad_t = out["__t"].isna()
    dropped["unparseable timestamp"] = int(bad_t.sum())
    out = out[~bad_t]
    meas = [k for k in CANON if k in mapping]
    blank = out[meas].isna().all(axis=1) if meas else pd.Series(False, index=out.index)
    dropped["blank row"] = int(blank.sum())
    out = out[~blank]
    moved = int((out["__t"].diff().dt.total_seconds() < 0).sum())
    out = out.sort_values("__t", kind="stable")
    dup = out["__t"].duplicated(keep=False)
    if dup.any():
        identical = int(out.duplicated(subset=["__t"] + meas).sum())
        n_extra = int(dup.sum() - out.loc[dup, "__t"].nunique())
        merged = out.groupby("__t", sort=True)[CANON].mean()
        dropped["duplicate timestamp (identical, removed)"] = identical
        dropped["duplicate timestamp (conflicting, averaged)"] = max(n_extra - identical, 0)
    else:
        merged = out.set_index("__t")[CANON]
    merged.index = pd.DatetimeIndex(merged.index).astype("datetime64[ns]")
    merged.index.name = "timestamp"
    df = merged

    # ---- cadence ------------------------------------------------------------
    d = df.index.to_series().diff().dt.total_seconds().dropna() / 60.0
    med = float(d.median()) if len(d) else np.nan
    regular = bool(len(d) and np.mean(np.abs(d - med) <= 0.02 * med + 1 / 60) >= 0.9)
    rep.cadence = {"median_min": round(med, 3), "regular": regular}
    target = None
    if med < 1:
        target = 1
    elif not regular:
        target = min(NICE_CADENCES_MIN, key=lambda x: abs(x - med))
    if target:
        df = _regularise(df, target)
        rep.cadence["resampled_to_min"] = target
        med = target
    gaps = d[d > 3 * med]
    if len(gaps):
        rep.cadence.update(gaps=int(len(gaps)), gap_hours=round(float(gaps.sum() / 60), 1))

    # ---- holes ----------------------------------------------------------------
    for k in meas:
        before = df[k].isna()
        filled = df[k].interpolate(method="time", limit=SHORT_GAP_SAMPLES, limit_area="inside")
        filled = filled.bfill(limit=SHORT_GAP_SAMPLES).ffill(limit=SHORT_GAP_SAMPLES)   # short edge holes
        # only fill runs that are entirely short; never shave the ends of a long outage
        run_id = (before != before.shift()).cumsum()
        run_len = before.groupby(run_id).transform("sum")
        ok = before & (run_len <= SHORT_GAP_SAMPLES)
        df.loc[ok, k] = filled[ok]
        rep.filled[k] = int((ok & df[k].notna()).sum())

    # ---- missing channels -----------------------------------------------------
    absent = [k for k in CANON if k not in mapping]
    if any(k in absent for k in CURRENTS) and power_cols:
        pc = power_cols[0]
        rep.warnings.append(f"phase currents missing: derived from {pc!r} assuming balanced load; "
                            "unbalance cannot be assessed")
        # handled after alignment below
        rep.derived["_power_source"] = pc
    for k in absent:
        if k == "ambient_temp":
            df[k] = float(ov.get("assumed_ambient_c", ASSUMED_AMBIENT_C))
            rep.derived[k] = (f"assumed constant {df[k].iloc[0]:.0f} C - thermal fault diagnosis "
                              "disabled (needs measured ambient)")
            rep.warnings.append("no ambient temperature column: thermal diagnosis (cooling, probe "
                                "offset) is OFF; hot-spot and ageing still use measured oil")
        elif k in VOLTAGES:
            pass
    if any(k in absent for k in VOLTAGES):
        rep.warnings.append("voltage channel(s) missing: "
                            + ", ".join(k for k in VOLTAGES if k in absent)
                            + " - nominal assumed; CT-vs-real-unbalance cannot be confirmed")
    if "oil_temp" in absent:
        rep.warnings.append("no oil temperature column: thermal diagnosis OFF; hot-spot is model-only")
    if "_power_source" in rep.derived:
        pc = rep.derived.pop("_power_source")
        s_kva = _power_kva(raw, pc, rep.decimal, t, rep.nameplate["rating_kva"], cls[pc][2])
        s_kva = s_kva.groupby(level=0).mean().reindex(df.index).interpolate(limit=SHORT_GAP_SAMPLES)
        v_ln = df[[k for k in VOLTAGES if k not in absent]].mean(axis=1) if \
            any(k not in absent for k in VOLTAGES) else 250.0
        i_ph = s_kva * 1000 / (3 * v_ln)
        for k in CURRENTS:
            if k in absent:
                df[k] = i_ph
                rep.derived[k] = f"from {pc!r} (balanced)"
    rep.absent = [k for k in absent if k not in rep.derived or k == "ambient_temp"]
    rep.mapping = mapping

    # ---- sanity ---------------------------------------------------------------
    cur = [k for k in CURRENTS if k in mapping]
    if cur:
        p99 = float(np.nanpercentile(df[cur].values, 99))
        if p99 > 3 * rated_i:
            rep.warnings.append(f"currents reach {p99:.0f} A, over 3x the {rated_i:.0f} A rated current "
                                f"of {rep.nameplate['rating_kva']:.0f} kVA: the nameplate is probably larger")
    if "oil_temp" in mapping:
        m = float(df.oil_temp.median())
        if not 15 <= m <= 110:
            rep.warnings.append(f"oil temperature median {m:.1f} C is implausible - check its unit")

    rep.rows = {"read": n_read, "kept": int(len(df)), "dropped": dropped, "moved": moved}
    return df, rep


def _infer_by_content(cols, vals, mapping, used, take):
    """Generic headers: currents and voltages come as correlated triples of
    similar magnitude (voltages barely move); temperatures are what remains,
    the oil being the warmer of the two."""
    if not cols:
        return
    X = pd.DataFrame({c: vals[c] for c in cols})
    med = X.median()
    cv = X.std() / X.mean().abs().clip(lower=1e-9)
    corr = X.corr().fillna(0)
    remaining = list(cols)
    for kind, canon in (("voltage", VOLTAGES), ("current", CURRENTS)):
        if all(k in mapping for k in canon):
            continue
        best = None
        for a in range(len(remaining)):
            for b in range(a + 1, len(remaining)):
                for c in range(b + 1, len(remaining)):
                    tri = [remaining[a], remaining[b], remaining[c]]
                    m = med[tri]
                    if m.min() <= 0 or m.max() / m.min() > 1.6:
                        continue
                    is_v = cv[tri].max() < 0.06
                    if (kind == "voltage") != is_v:
                        continue
                    r = min(corr.loc[tri[0], tri[1]], corr.loc[tri[0], tri[2]], corr.loc[tri[1], tri[2]])
                    if r < 0.6:
                        continue
                    if best is None or r > best[0]:
                        best = (r, tri)
        if best:
            for col, k in zip(best[1], canon):
                take(k, col, f"content: {kind} triple (r={best[0]:.2f})", "low")
            remaining = [c for c in remaining if c not in best[1]]
    temps = [c for c in remaining if -30 <= med[c] <= 400]
    temps = sorted(temps, key=lambda c: -med[c])[:2]
    if len(temps) == 2:
        take("oil_temp", temps[0], "content: warmer temperature", "low")
        take("ambient_temp", temps[1], "content: cooler temperature", "low")
    elif len(temps) == 1:
        take("oil_temp", temps[0], "content: only temperature", "low")


def _unit(canon, v, hint, rated_i, override):
    """(unit, factor, how). Temperature returns the source unit; conversion is
    done separately because it is affine."""
    med = float(v.median()) if v.notna().any() else np.nan
    p95 = float(v.quantile(0.95)) if v.notna().any() else np.nan
    if canon in CURRENTS:
        if override:
            return override, {"mA": 1e-3, "A": 1.0, "kA": 1e3}[override], "override"
        if hint == "mA":
            return "mA", 1e-3, "name says mA"
        if p95 > 30 * rated_i:
            return "mA", 1e-3, f"p95 {p95:.0f} is over 30x rated current"
        return "A", 1.0, "magnitude"
    if canon in VOLTAGES:
        if override:
            f = {"V": 1.0, "kV": 1e3, "V_LL": 1 / np.sqrt(3), "kV_LL": 1e3 / np.sqrt(3)}[override]
            return override, f, "override"
        f, how, unit = 1.0, "magnitude", "V"
        if hint == "kV" or med < 2:
            f, how, unit = 1e3, ("name says kV" if hint == "kV" else f"median {med:.3f} looks like kV"), "kV"
        m = med * f
        if 340 <= m <= 480:
            return unit + "_LL", f / np.sqrt(3), how + f"; {m:.0f} V is line-to-line, /sqrt3 to phase"
        return unit, f, how
    if canon in TEMPS:
        if override:
            return override, 1.0, "override"
        if hint in ("K", "F"):
            return hint, 1.0, f"name says {hint}"
        if med > 200:
            return "K", 1.0, f"median {med:.0f} looks like kelvin"
        return "C", 1.0, "magnitude"
    return "?", 1.0, ""


def _temp_convert(v, unit):
    return v - 273.15 if unit == "K" else (v - 32.0) / 1.8


def _power_kva(raw, col, decimal, t, rating_kva, hint):
    p = _numeric(raw[col], decimal)
    if hint == "kW" or "kw" in tokens(col):
        p = p / 0.9                                   # assumed power factor
    if p.median() > 50 * rating_kva:
        p = p / 1000.0                                # W or VA
    return pd.Series(p.values, index=pd.DatetimeIndex(t.values)).loc[lambda s: s.index.notna()]


def _regularise(df, minutes):
    """Put irregular or sub-minute data on a regular grid. Each grid point takes
    the time-weighted interpolation of its neighbours, but only where real
    samples exist within 1.5 cadences, so gaps are not bridged."""
    step = pd.Timedelta(minutes=minutes)
    if df.index.to_series().diff().median() < 0.5 * step:      # much finer: average into bins
        return df.resample(step).mean().dropna(how="all")
    grid = pd.date_range(df.index[0].ceil(step), df.index[-1].floor(step), freq=step)
    both = df.reindex(df.index.union(grid)).interpolate(method="time", limit_area="inside")
    out = both.reindex(grid)
    near = pd.Series(df.index, index=df.index).reindex(grid, method="nearest")
    far = (near - grid.to_series()).abs() > 1.5 * step
    out[far.values] = np.nan
    out.index.name = "timestamp"
    return out.dropna(how="all")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("csv")
    ap.add_argument("--map", action="append", default=[], help="canon=SourceColumn")
    ap.add_argument("--unit", action="append", default=[], help="canon=unit (A, mA, V, kV, V_LL, C, K, F)")
    ap.add_argument("--dayfirst", choices=["yes", "no"])
    ap.add_argument("--kva", type=float)
    ap.add_argument("--save-sidecar", action="store_true", help="write these overrides to <csv>.mapping.json")
    a = ap.parse_args(argv)
    ov = {"map": dict(x.split("=", 1) for x in a.map), "units": dict(x.split("=", 1) for x in a.unit)}
    if a.dayfirst:
        ov["dayfirst"] = a.dayfirst == "yes"
    if a.kva:
        ov["rating_kva"] = a.kva
    df, rep = load(a.csv, ov)
    print(rep.text())
    print(f"\n  first rows:\n{df.head(3).round(2).to_string()}")
    if a.save_sidecar:
        side = Path(a.csv).with_suffix(".mapping.json")
        side.write_text(json.dumps({k: v for k, v in ov.items() if v}, indent=2))
        print(f"\n  overrides saved to {side}")


if __name__ == "__main__":
    main()
