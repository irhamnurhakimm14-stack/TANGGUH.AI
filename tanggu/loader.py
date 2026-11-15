"""Baca lampiran SK (xlsx) jadi tabel bersih."""
from __future__ import annotations

import pandas as pd

COLS = ["no", "nama", "nik", "no_kk", "kecamatan", "nagari", "jorong", "kerusakan", "penanganan"]


def load_sk_excel(path, sheet: str = "LAMPIRAN UJI PUBLIK") -> pd.DataFrame:
    """Baca sheet lampiran SK. Header dicari otomatis (baris yang kolom pertamanya 'No.')."""
    raw = pd.read_excel(path, sheet_name=sheet, header=None, dtype=str)
    hdr = raw.index[raw.iloc[:, 0].astype(str).str.strip() == "No."]
    if len(hdr) == 0:
        raise ValueError(f"Header 'No.' tidak ditemukan di sheet {sheet!r}")
    d = raw.iloc[hdr[0] + 1:, :9].copy()
    d.columns = COLS
    d = d[d["no"].astype(str).str.fullmatch(r"\d+")].reset_index(drop=True)  # buang baris tanda tangan dll.
    d.insert(0, "id", [f"TG-{int(n):03d}" for n in d["no"]])
    return d.drop(columns="no")


def load_rekap(path) -> pd.DataFrame:
    return pd.read_csv(path)
