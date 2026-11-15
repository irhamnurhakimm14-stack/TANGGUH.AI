"""Validasi massal: jalankan semua aturan ke satu tabel (hasil load_table)."""
from __future__ import annotations

import pandas as pd

from . import rules

REQUIRED_SK = {"nama": "Nama pemilik", "nik": "NIK", "no_kk": "Nomor KK",
               "kecamatan": "Kecamatan", "nagari": "Nagari", "jorong": "Jorong",
               "kerusakan": "Kerusakan", "penanganan": "Penanganan"}


def validate_dataframe(df: pd.DataFrame, required: dict | None = None) -> pd.DataFrame:
    """Kembalikan tabel temuan: id, nama, code, level, field, message, ref_id."""
    required = required or REQUIRED_SK
    recs = df.to_dict("records")
    rows = []
    for r in recs:
        for iss in (rules.validate_completeness(r, required) + rules.validate_nik(r.get("nik"))
                    + rules.validate_kk(r.get("no_kk")) + rules.validate_consistency(r)):
            rows.append({"id": r["id"], "nama": r.get("nama"), **iss})

    # duplikasi antar-baris dalam tabel yang sama (bandingkan hanya dengan baris sebelumnya)
    by_nik, by_kk = {}, {}
    for r in recs:
        nik, kk = rules.clean_digits(r.get("nik")), rules.clean_digits(r.get("no_kk"))
        if len(nik) == 16:
            by_nik.setdefault(nik, []).append(r["id"])
        if len(kk) == 16:
            by_kk.setdefault(kk, []).append(r["id"])
    for ids in by_nik.values():
        for rid in ids[1:]:
            nama = next(x["nama"] for x in recs if x["id"] == rid)
            rows.append({"id": rid, "nama": nama, **rules.issue(
                "DUP_NIK", "warning", "nik", f"NIK sama dengan {ids[0]}. Perlu pemeriksaan admin.", ids[0])})
    for ids in by_kk.values():
        for rid in ids[1:]:
            nama = next(x["nama"] for x in recs if x["id"] == rid)
            rows.append({"id": rid, "nama": nama, **rules.issue(
                "DUP_KK", "info", "no_kk", f"No. KK sama dengan {ids[0]} (bisa wajar: satu KK, beberapa kerusakan).", ids[0])})
    cols = ["id", "nama", "code", "level", "field", "message", "ref_id"]
    out = pd.DataFrame(rows, columns=cols)
    return out


def summarize_issues(issues: pd.DataFrame) -> pd.DataFrame:
    if issues.empty:
        return pd.DataFrame(columns=["code", "level", "jumlah"])
    return (issues.groupby(["code", "level"]).size().reset_index(name="jumlah")
            .sort_values("jumlah", ascending=False).reset_index(drop=True))


def compare_versions(a: pd.DataFrame, b: pd.DataFrame, key: str = "nik") -> pd.DataFrame:
    """Deteksi perubahan antar versi file (mis. AutoRecovered vs KONFIRMASI).
    Cocokkan lewat NIK bersih; laporkan baris yang hanya ada di salah satu versi atau
    berubah nama/KK/kerusakan."""
    def prep(d):
        d = d.copy()
        d["_k"] = d[key].map(rules.clean_digits)
        return d[d["_k"].str.len() > 0].drop_duplicates("_k").set_index("_k")
    A, B = prep(a), prep(b)
    rows = []
    for k in A.index.difference(B.index):
        rows.append({"nik": k, "jenis": "hanya_di_A", "nama_A": A.at[k, "nama"], "nama_B": None})
    for k in B.index.difference(A.index):
        rows.append({"nik": k, "jenis": "hanya_di_B", "nama_A": None, "nama_B": B.at[k, "nama"]})
    for k in A.index.intersection(B.index):
        for col in ("nama", "no_kk", "kerusakan", "penanganan", "nagari"):
            va, vb = str(A.at[k, col]).strip(), str(B.at[k, col]).strip()
            if va != vb:
                rows.append({"nik": k, "jenis": f"beda_{col}", "nama_A": va, "nama_B": vb})
    return pd.DataFrame(rows, columns=["nik", "jenis", "nama_A", "nama_B"])
