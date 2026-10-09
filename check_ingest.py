"""
Mangle showcase.csv in every way a stranger's file might differ, and confirm
each version still loads, maps correctly, carries the same values, and still
produces the two acceptance diagnoses.

    python check_ingest.py            # table of pass/fail
    python check_ingest.py -v         # plus every mapping report

Checks per case
  load       ingest.load() returns without error
  mapping    every channel mapped to the column it really came from, or
             reported absent when it was removed
  values     after unit conversion, values match the clean file (median
             absolute difference within tolerance on shared timestamps; for
             jittered times the tolerance is 2/15 of the median sample-to-sample
             change, the most regridding a +/-2 min shift can introduce)
  diagnosis  validator on the result still catches the cooling fault and
             still calls the probe drift a sensor fault (not cooling); with
             ambient removed, thermal diagnosis must say "unavailable" and
             the overload must still be caught
  rows       kept-row count is what the mangle implies
  extra      case-specific: sheet chosen and why, rating source, decoys
             excluded by rule, conflicts warned, missing markers never zero

The last two cases are the blind files (tests/fixtures/blind/), kept as
permanent regressions: an Excel workbook with the nameplate on its first
sheet, and its cp1252 CSV export.
"""

import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from dtwin.ingest import load, CANON
from dtwin.physics.nameplate import preset
from dtwin.analytics.validation import validate

SRC = Path("data/showcase.csv")
TRUTH = pd.read_csv("data/showcase_truth.csv", parse_dates=["timestamp"], index_col="timestamp")
RNG = np.random.default_rng(20261010)
TMP = Path(tempfile.mkdtemp(prefix="ingest_"))
VERBOSE = "-v" in sys.argv

BASE = pd.read_csv(SRC)                      # the raw file as written
IDENT = {k: k for k in CANON}
TOL = {"I": 0.02, "V": 0.6, "T": 0.06}       # A, V, K - median abs difference


def tol_for(k, scale=1.0):
    return scale * (TOL["I"] if k.startswith("I") else TOL["V"] if k.startswith("V") else TOL["T"])


def write(name, df=None, text=None, **kw):
    p = TMP / f"{name}.csv"
    if text is not None:
        p.write_text(text)
    else:
        df.to_csv(p, index=False, **kw)
    return p


# --------------------------------------------------------------------------- #
# The mangles. Each returns (path, expected mapping canon->source or None for
# absent, expected kept rows, value tolerance scale, notes)
# --------------------------------------------------------------------------- #
RENAME = {"IR": "I_R", "IY": "Current_Y", "IB": "B_Phase_Current", "VR": "V_R", "VY": "Voltage_Y",
          "VB": "VB_Volts", "oil_temp": "Top_Oil_C", "ambient_temp": "Amb_Temp"}


def m_rename():
    d = BASE.rename(columns={**RENAME, "timestamp": "DateTime"})
    return write("rename", d), RENAME, 2880, 1


def m_units_magnitude():
    d = BASE.copy()
    for c in ("IR", "IY", "IB"):
        d[c] = (d[c] * 1000).round(0)
    for c in ("VR", "VY", "VB"):
        d[c] = (d[c] / 1000).round(6)
    for c in ("oil_temp", "ambient_temp"):
        d[c] = (d[c] + 273.15).round(3)
    return write("units_by_magnitude", d), IDENT, 2880, 1


def m_units_named():
    d = BASE.copy()
    ren = {"IR": "IR_mA", "IY": "IY_mA", "IB": "IB_mA", "VR": "VR_kV", "VY": "VY_kV", "VB": "VB_kV",
           "oil_temp": "Oil_Temp_K", "ambient_temp": "Ambient_K"}
    for c in ("IR", "IY", "IB"):
        d[c] = d[c] * 1000
    for c in ("VR", "VY", "VB"):
        d[c] = d[c] / 1000
    for c in ("oil_temp", "ambient_temp"):
        d[c] = d[c] + 273.15
    return write("units_in_names", d.rename(columns=ren)), ren, 2880, 1


def m_shuffle():
    return write("shuffled", BASE.sample(frac=1, random_state=1)), IDENT, 2880, 1


def m_holes():
    d = BASE.copy()
    cols = ["IR", "IY", "IB", "VR", "VY", "VB", "oil_temp", "ambient_temp"]
    mask = RNG.random((len(d), len(cols))) < 0.10
    for j, c in enumerate(cols):
        d.loc[mask[:, j], c] = np.nan
    return write("holes_10pct", d), IDENT, 2880, 1.5


def m_hourly():
    return write("cadence_1h", BASE.iloc[::4]), IDENT, 720, 1


def m_drop_ambient():
    m = dict(IDENT, ambient_temp=None)
    return write("no_ambient", BASE.drop(columns=["ambient_temp"])), m, 2880, 1


def m_duplicates():
    d = BASE.copy()
    exact = d.sample(frac=0.05, random_state=2)
    noisy = d.sample(frac=0.02, random_state=3).copy()
    noisy["IR"] = noisy["IR"] * 1.002
    return write("duplicates", pd.concat([d, exact, noisy]).sample(frac=1, random_state=4)), IDENT, 2880, 1


def m_ddmm():
    d = BASE.copy()
    d["timestamp"] = pd.to_datetime(d.timestamp).dt.strftime("%d/%m/%Y %H:%M")
    return write("date_ddmmyyyy", d), IDENT, 2880, 1


def m_mmdd():
    d = BASE.copy()
    d["timestamp"] = pd.to_datetime(d.timestamp).dt.strftime("%m/%d/%Y %I:%M %p")
    return write("date_mmddyyyy_ampm", d), IDENT, 2880, 1


def m_junk():
    d = BASE.copy()
    n = len(d)
    junk = {"Frequency_Hz": 50 + RNG.normal(0, 0.05, n), "PF": RNG.uniform(0.8, 0.99, n),
            "Status": RNG.choice(["OK", "TRIP", "ALM"], n), "Notes": "",
            "Signal_dBm": RNG.integers(-110, -60, n), "Battery_V": 12 + RNG.normal(0, 0.1, n),
            "kWh_total": np.cumsum(RNG.uniform(5, 20, n)), "Temp_Cabinet": 35 + RNG.normal(0, 2, n),
            "Fault_Code": RNG.integers(0, 9, n), "GSM_CSQ": RNG.integers(5, 31, n)}
    for k, v in junk.items():
        d[k] = v
    cols = list(d.columns)
    RNG.shuffle(cols)
    return write("junk_columns", d[cols]), IDENT, 2880, 1


def m_generic():
    d = BASE.copy()
    d.columns = [f"col{i + 1}" for i in range(d.shape[1])]
    names = dict(zip(BASE.columns, d.columns))
    return write("generic_header", d), {k: names[k] for k in CANON}, 2880, 1


def m_semicolon():
    return write("semicolon", BASE, sep=";"), IDENT, 2880, 1


def m_semicolon_comma():
    return write("semicolon_decimal_comma", BASE, sep=";", decimal=","), IDENT, 2880, 1


def m_preamble():
    pre = ("CPRI Distribution Transformer Monitor - data export\n"
           "Transformer ID: DT-001\nRating: 100 kVA, 11/0.433 kV\nExported: 2026-05-01 09:12\n\n")
    return write("preamble", text=pre + BASE.to_csv(index=False)), IDENT, 2880, 1


def m_epoch():
    d = BASE.copy()
    d["timestamp"] = (pd.to_datetime(d.timestamp) - pd.Timestamp("1970-01-01")) // pd.Timedelta("1s")
    return write("epoch_seconds", d.rename(columns={"timestamp": "unix_time"})), IDENT, 2880, 1


def m_split_datetime():
    d = BASE.copy()
    t = pd.to_datetime(d.pop("timestamp"))
    d.insert(0, "Time", t.dt.strftime("%H:%M:%S"))
    d.insert(0, "Date", t.dt.strftime("%d-%m-%Y"))
    return write("date_time_split", d), IDENT, 2880, 1


def m_l123():
    ren = {"IR": "L1", "IY": "L2", "IB": "L3", "VR": "U1", "VY": "U2", "VB": "U3",
           "oil_temp": "OTI", "ambient_temp": "T_amb"}
    return write("L123_naming", BASE.rename(columns=ren)), ren, 2880, 1


def m_line_line():
    d = BASE.copy()
    ren = {"VR": "V_RY", "VY": "V_YB", "VB": "V_BR"}
    for c in ("VR", "VY", "VB"):
        d[c] = d[c] * np.sqrt(3)
    return write("line_to_line_volts", d.rename(columns=ren)), {**IDENT, **ren}, 2880, 1


def m_jitter():
    d = BASE.copy()
    t = pd.to_datetime(d.timestamp) + pd.to_timedelta(RNG.uniform(-120, 120, len(d)), unit="s")
    d["timestamp"] = t.dt.strftime("%Y-%m-%d %H:%M:%S")
    # values are kept but times move by up to 2 min, so regridding mixes in up
    # to 2/15 of the neighbouring sample: tolerance is derived from that, below
    return write("irregular_jitter", d), IDENT, 2880, "interp"


def m_blank_rows():
    lines = BASE.to_csv(index=False).splitlines()
    out = []
    for i, ln in enumerate(lines):
        out.append(ln)
        if i and i % 97 == 0:
            out.append(",,,,,,,,,")
        if i and i % 211 == 0:
            out.append("")
    out += ["", "END OF FILE", "Total records: 2880"]
    return write("blank_rows_trailer", text="\n".join(out)), IDENT, 2880, 1


def m_everything():
    d = BASE.rename(columns={**RENAME, "timestamp": "DateTime"}).copy()
    for c in ("I_R", "Current_Y", "B_Phase_Current"):
        d[c] = (d[c] * 1000).round(0)
    for c in ("Top_Oil_C",):
        pass
    d["DateTime"] = pd.to_datetime(d.DateTime).dt.strftime("%d/%m/%Y %H:%M")
    mask = RNG.random((len(d), 8)) < 0.10
    for j, c in enumerate(RENAME.values()):
        d.loc[mask[:, j], c] = np.nan
    d["Frequency_Hz"] = 50.0
    d["Battery_V"] = 12.1
    d["Status"] = "OK"
    d = d.sample(frac=1, random_state=9)
    pre = "Export from CPRI DTM\nTransformer ID: DT-001\nRating: 100 kVA\n\n"
    return write("everything_at_once", text=pre + d.to_csv(index=False, sep=";", decimal=",")), \
        RENAME, 2880, 1.5


# ---- Excel and the failure classes the blind test exposed -----------------
def _xlsx(name, sheets):
    """sheets: {sheet name: DataFrame or list of rows}, written in order."""
    import openpyxl
    p = TMP / f"{name}.xlsx"
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, data in sheets.items():
        ws = wb.create_sheet(title)
        if isinstance(data, pd.DataFrame):
            ws.append(list(data.columns))
            for row in data.itertuples(index=False):
                ws.append([None if (isinstance(v, float) and v != v) else v for v in row])
        else:
            for row in data:
                ws.append(row)
    wb.save(p)
    return p


def m_excel():
    d = BASE.copy()
    d["timestamp"] = pd.to_datetime(d.timestamp)
    return _xlsx("excel_plain", {"Sheet1": d}), IDENT, 2880, 1


def m_excel_native_split():
    d = BASE.copy()
    t = pd.to_datetime(d.pop("timestamp"))
    d.insert(0, "Time", [x.time() for x in t])
    d.insert(0, "Date", [x.date() for x in t])
    info = [["Transformer nameplate"], ["Transformer ID", "DT-001"], ["Rating (kVA)", 100],
            ["HV / LV", "11/0.433 kV"], ["Cooling", "ONAN"]]
    def extra(rep, df):
        ok = rep.sheet == "Data" and rep.nameplate["how"].startswith("read from sheet 'Nameplate'") \
            and rep.timestamp["how"].startswith("Excel date cells")
        return ok, f"sheet {rep.sheet!r}, rating {rep.nameplate['how']}, time {rep.timestamp['how']}"
    return _xlsx("excel_native_date_time", {"Nameplate": info, "Data": d}), IDENT, 2880, 1, extra


def m_excel_many_sheets():
    d = BASE.copy()
    d["timestamp"] = pd.to_datetime(d.timestamp)
    readme = [["README"], ["This workbook has training and test data."], ["Columns: see header."]]
    p = _xlsx("excel_readme_train_test", {"README": readme, "training": d.iloc[:2000], "test": d.iloc[2000:]})
    def extra(rep, df):
        warned = any("data-like sheets" in w for w in rep.warnings)
        _, rep2 = load(p, {"sheet": "test"})
        ok = warned and rep.sheet == "training" and rep2.sheet == "test" and rep2.rows["kept"] == 880
        return ok, f"chose {rep.sheet!r}, warned {warned}; --sheet test -> {rep2.sheet!r} {rep2.rows['kept']} rows"
    # a slice of the record: tests sheet choice, not diagnosis (no detectable fault in it)
    return p, IDENT, 2000, 1, extra, False


def m_missing_markers():
    d = BASE.copy()
    cols = ["IR", "IY", "IB", "VR", "VY", "VB", "oil_temp", "ambient_temp"]
    marks = ["--", "-", "ERR", "NA", "#N/A", "NaN"]
    hit = RNG.random((len(d), len(cols))) < 0.03
    d[cols] = d[cols].astype(object)
    for j, c in enumerate(cols):
        idx = np.where(hit[:, j])[0]
        d.loc[d.index[idx], c] = [marks[k % len(marks)] for k in range(len(idx))]
    n_marks = int(hit.sum())
    def extra(rep, df):
        counted = sum(rep.missing_markers.values())
        zeros = int((df[cols] == 0).sum().sum())
        return counted == n_marks and zeros == 0, f"{counted} of {n_marks} markers read as missing, {zeros} zeros"
    return write("missing_markers", d), IDENT, 2880, 1.5, extra


def m_decoys_first():
    d = BASE.copy()
    n = len(d)
    decoys = {"kVA": RNG.uniform(10, 90, n), "IN (A)": RNG.uniform(0, 9, n), "WTI (°C)": d.oil_temp + 8,
              "PF": 0.9, "Freq (Hz)": 50.0, "kVAr": RNG.uniform(1, 9, n), "HV_Current_R": RNG.uniform(1, 5, n),
              "HV Voltage (kV)": 11.0, "Status": "OK", "Battery_V": 12.1}
    front = pd.DataFrame(decoys)
    out = pd.concat([d[["timestamp"]], front, d.drop(columns="timestamp")], axis=1)
    def extra(rep, df):
        ok = set(decoys) <= set(rep.decoys) and not rep.conflicts
        return ok, f"{len(set(decoys) & set(rep.decoys))}/{len(decoys)} decoys excluded, conflicts {len(rep.conflicts)}"
    return write("decoys_before_real", out), IDENT, 2880, 1, extra


def m_competing():
    d = BASE.copy()
    d.insert(2, "I_R", d.IR)                      # the same channel exported twice
    def extra(rep, df):
        ok = any("IR" in c for c in rep.conflicts)
        return ok, rep.conflicts[0][:80] if rep.conflicts else "no conflict reported"
    return write("competing_columns", d), {**IDENT, "IR": "I_R"}, 2880, 1, extra


def m_cp1252_title_rows():
    d = BASE.rename(columns={"oil_temp": "Oil Temp (°C)", "ambient_temp": "Ambient (°C)"})
    pad = "," * (d.shape[1] - 1)
    pre = f"Distribution Transformer Monitoring Log{pad}\nAsset: DT-001   Feeder: F-07{pad}\nExport interval: 15 min{pad}\n{pad}\n"
    p = TMP / "cp1252_title_rows.csv"
    p.write_bytes((pre + d.to_csv(index=False)).encode("cp1252"))
    return p, {**IDENT, "oil_temp": "Oil Temp (°C)", "ambient_temp": "Ambient (°C)"}, 2880, 1


BLIND = Path("tests/fixtures/blind")
BLIND_MAP = {"IR": "IR (A)", "IY": "IY (A)", "IB": "IB (A)", "VR": "VR (V)", "VY": "VY (V)", "VB": "VB (V)",
             "oil_temp": "OTI (°C)", "ambient_temp": "Amb. Temp (°C)"}
BLIND_DECOYS = {"kVA", "IN (A)", "WTI (°C)", "PF", "Freq (Hz)", "Status"}


def _blind_extra(want_rating_how):
    def extra(rep, df):
        ok = BLIND_DECOYS <= set(rep.decoys) and rep.cadence.get("gaps") == 1 \
            and sum(rep.missing_markers.values()) > 0 and rep.nameplate["how"].startswith(want_rating_how)
        return ok, (f"decoys {len(BLIND_DECOYS & set(rep.decoys))}/6, outage {rep.cadence.get('gap_hours')} h, "
                    f"markers {sum(rep.missing_markers.values())}, rating {rep.nameplate['rating_kva']:g} ({rep.nameplate['how']})")
    return extra


def m_blind_xlsx():
    return BLIND / "TF-JPR-0417_log.xlsx", BLIND_MAP, 2856, 1.5, _blind_extra("read from sheet 'Info'")


def m_blind_csv():
    return BLIND / "TF-JPR-0417_log.csv", BLIND_MAP, 2856, 1.5, _blind_extra("NOT FOUND")


CASES = [m_rename, m_units_magnitude, m_units_named, m_shuffle, m_holes, m_hourly, m_drop_ambient,
         m_duplicates, m_ddmm, m_mmdd, m_junk, m_generic, m_semicolon, m_semicolon_comma, m_preamble,
         m_epoch, m_split_datetime, m_l123, m_line_line, m_jitter, m_blank_rows, m_everything,
         m_excel, m_excel_native_split, m_excel_many_sheets, m_missing_markers, m_decoys_first,
         m_competing, m_cp1252_title_rows, m_blind_xlsx, m_blind_csv]


# --------------------------------------------------------------------------- #
def diagnosis(df, rep):
    rows, _, _ = validate(df, preset(rep.nameplate["rating_kva"]), absent=rep.absent)
    t = TRUTH.reindex(rows.index, method="nearest", tolerance=pd.Timedelta("8min"))
    cool = t.fault_label.str.contains("cooling", na=False).values
    drift = t.fault_label.str.contains("sensor_drift", na=False).values
    over = t.fault_label.str.contains("overload", na=False).values
    st = rows.thermal_status.values
    if "ambient_temp" in rep.absent:
        ok = (st == "unavailable").all() and rows.overload.values[over].any()
        return ok, "thermal unavailable, overload caught" if ok else f"thermal {set(st)}"
    caught = (st[cool] == "asset_cooling").any()
    drift_sensor = (st[drift] == "sensor_temp_offset").any()
    drift_wrong = (st[drift] == "asset_cooling").mean()
    ok = caught and drift_sensor and drift_wrong == 0
    return ok, (f"cooling {'caught' if caught else 'MISSED'}, drift "
                f"{'sensor' if drift_sensor else 'NOT sensor'}"
                + (f", {100 * drift_wrong:.0f}% called cooling" if drift_wrong else ""))


def main():
    base_df, _ = load(SRC)
    results = []
    for fn in CASES:
        name = fn.__name__[2:]
        row = {"case": name}
        try:
            spec = fn()
            path, expect, rows_expected, scale = spec[:4]
            extra = spec[4] if len(spec) > 4 else None
            diagnose = spec[5] if len(spec) > 5 else True
        except Exception as e:                                   # the mangle itself failed
            results.append({**row, "load": f"MANGLE ERROR {e}"})
            continue
        try:
            df, rep = load(path)
            row["load"] = "ok"
        except Exception as e:
            row.update(load=f"FAIL {type(e).__name__}: {e}", mapping="-", values="-", diagnosis="-", rows="-")
            results.append(row)
            continue
        if VERBOSE:
            print("\n" + rep.text())

        bad_map = []
        for k, src in expect.items():
            if src is None:
                if k not in rep.absent:
                    bad_map.append(f"{k} should be absent")
            elif rep.mapping.get(k, {}).get("source") != src:
                bad_map.append(f"{k}<-{rep.mapping.get(k, {}).get('source')} (want {src})")
        row["mapping"] = "ok" if not bad_map else "FAIL " + "; ".join(bad_map)

        common = df.index.intersection(base_df.index)
        bad_val = []
        for k, src in expect.items():
            if src is None:
                continue
            a, b = df.loc[common, k], base_df.loc[common, k]
            m = a.notna() & b.notna()
            if m.sum() == 0:
                bad_val.append(f"{k}: no overlap")
                continue
            err = float((a[m] - b[m]).abs().median())
            if scale == "interp":     # max jitter / cadence = 2 min / 15 min of a typical step
                lim = (2.0 / 15.0) * float(base_df[k].diff().abs().median())
            else:
                lim = tol_for(k, scale)
            if err > lim:
                bad_val.append(f"{k} err {err:.3g}")
        row["values"] = "ok" if not bad_val and len(common) else ("FAIL " + "; ".join(bad_val) if bad_val
                                                                   else "FAIL no shared timestamps")
        if diagnose:
            ok, note = diagnosis(df, rep)
            row["diagnosis"] = ("ok: " if ok else "FAIL: ") + note
        else:
            row["diagnosis"] = "ok: n/a (partial record)"
        if extra:
            ok_x, note_x = extra(rep, df)
            row["extra"] = ("ok: " if ok_x else "FAIL: ") + note_x
        else:
            row["extra"] = "ok"
        kept = rep.rows["kept"]
        row["rows"] = "ok" if abs(kept - rows_expected) <= max(2, 0.01 * rows_expected) else \
            f"FAIL kept {kept}, want {rows_expected}"
        row["notes"] = "; ".join(filter(None, [
            f"resampled to {rep.cadence['resampled_to_min']} min" if rep.cadence.get("resampled_to_min") else "",
            f"dropped {sum(rep.rows['dropped'].values())}" if sum(rep.rows["dropped"].values()) else "",
            f"filled {sum(rep.filled.values())}" if sum(rep.filled.values()) else "",
            f"absent {rep.absent}" if rep.absent else "",
            rep.timestamp.get("how", "") if "ambiguous" in rep.timestamp.get("how", "") or "epoch" in rep.timestamp.get("how", "") else ""]))
        results.append(row)

    out = pd.DataFrame(results).fillna("-")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 70)
    print(out.to_string(index=False))
    checks = ["load", "mapping", "values", "diagnosis", "rows", "extra"]
    passed = out.apply(lambda r: all(str(r[c]).startswith("ok") for c in checks), axis=1)
    print(f"\n  {int(passed.sum())} / {len(out)} cases pass every check   (files in {TMP})")
    return 0 if passed.all() else 1


if __name__ == "__main__":
    sys.exit(main())
