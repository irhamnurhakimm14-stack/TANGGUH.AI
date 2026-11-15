"""Memuat dan menormalkan data Excel/PDF kalian ke satu skema standar.

Skema standar (kolom DataFrame):
  nama, nik, no_kk, kecamatan, nagari, jorong, kerusakan, penanganan
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from . import rules

STD_COLS = ["nama", "nik", "no_kk", "kecamatan", "nagari", "jorong", "kerusakan", "penanganan"]

RENAME = {
    "nama pemilik": "nama", "nama_lgkp": "nama", "nama": "nama",
    "nik": "nik", "nomor kk": "no_kk", "no_kk": "no_kk", "no kk": "no_kk",
    "kecamatan": "kecamatan", "nama_kec": "kecamatan",
    "nagari": "nagari", "nama_kel": "nagari", "jorong": "jorong", "alamat": "jorong",
    "kerusakan": "kerusakan", "penanganan": "penanganan",
}


def _find_header_row(raw: pd.DataFrame) -> int:
    for i in range(min(len(raw), 30)):
        vals = [str(v).strip().lower() for v in raw.iloc[i].tolist()]
        if "nik" in vals and any(v in vals for v in ("nama pemilik", "nama_lgkp", "nama")):
            return i
    raise ValueError("Baris header (NIK, Nama Pemilik, ...) tidak ditemukan")


def load_table(path, sheet=0) -> pd.DataFrame:
    """Baca satu sheet SK/rekap Excel (.xlsx/.xls), cari header otomatis,
    buang baris tanda tangan/kosong, dan kembalikan skema standar."""
    path = Path(path)
    raw = pd.read_excel(path, sheet_name=sheet, header=None, dtype=str)
    h = _find_header_row(raw)
    df = raw.iloc[h + 1:].copy()
    df.columns = [str(c).strip().lower() for c in raw.iloc[h]]
    df = df.rename(columns={c: RENAME[c] for c in df.columns if c in RENAME})
    df = df.loc[:, ~df.columns.duplicated()]
    for c in STD_COLS:
        if c not in df.columns:
            df[c] = None
    # buang baris tanpa nama (tanda tangan, kosong) dan baris total
    df = df[df["nama"].notna() & ~df["nama"].astype(str).str.contains(r"^(?:jumlah|total)\b", case=False, regex=True)]
    df = df[df["nik"].notna() | df["kerusakan"].notna() | df["no_kk"].notna()]
    out = df[STD_COLS].reset_index(drop=True)
    out.insert(0, "id", [f"R{i + 1:04d}" for i in range(len(out))])
    return out


KECAMATAN = (
    "Junjung Sirih|Kubung|X Koto Singkarak|Lembah Gumanti|Gunung Talang|Bukit Sundi|"
    "Hiliran Gumanti|Tigo Lurah|Payung Sekaki|Lembang Jaya|Pantai Cermin|"
    "IX Koto Sungai Lasi|X Koto Diatas|Danau Kembar|Kota Solok"
)


def parse_rekap_pdf(path, page: int = 1) -> pd.DataFrame:
    """Baca tabel REKAP per nagari dari PDF 'DATA_BENCANA__REKAP' (halaman 1).

    Kolom hasil: nagari, jumlah, rb, rs, rr, hanyut.
    Butuh `pdftotext` (paket poppler). Bila tidak ada, pakai CSV hasil ekspor
    (data/rekap_solok_2025.csv) lewat load_rekap_csv().
    """
    import subprocess

    txt = subprocess.run(
        ["pdftotext", "-layout", "-f", str(page), "-l", str(page), str(path), "-"],
        capture_output=True, text=True, check=True,
    ).stdout
    rows = []
    for line in txt.splitlines():
        if "total" in line.lower():
            continue
        toks = line.split()
        nums_rev = []
        while toks and re.fullmatch(r"-|\d+", toks[-1]):
            t = toks.pop()
            nums_rev.append(0 if t == "-" else int(t))
        nums = nums_rev[::-1]
        if len(nums) < 10 or not toks:
            continue
        name = re.sub(r"^\d+\s+", "", " ".join(toks))
        name = re.sub(rf"^(?:{KECAMATAN})\s+", "", name, flags=re.I)
        jumlah, rb, rs, rr, hanyut = nums[:5]
        rows.append({"nagari": name.strip(), "jumlah": jumlah, "rb": rb, "rs": rs, "rr": rr, "hanyut": hanyut})
    return pd.DataFrame(rows)


def load_rekap_csv(path) -> pd.DataFrame:
    return pd.read_csv(path)


def canonical_names(names, ref_names, cutoff: float = 0.8) -> dict:
    """Petakan ejaan nagari di detail ke ejaan di rekap (mis. 'Aie Dingin' -> 'Aia Dingin')."""
    from difflib import get_close_matches

    ref = {rules.norm_text(r): r for r in ref_names}
    out = {}
    for n in set(names):
        k = rules.norm_text(n)
        m = get_close_matches(k, list(ref), n=1, cutoff=cutoff)
        out[n] = ref[m[0]] if m else n
    return out


def compare_with_rekap(detail: pd.DataFrame, rekap: pd.DataFrame) -> pd.DataFrame:
    """Bandingkan jumlah baris detail per nagari/kategori dengan angka rekap.
    Kolom selisih_* bernilai (detail - rekap); bukan 0 berarti rekap & detail tidak cocok."""
    d = detail.copy()
    d["kat"] = d["kerusakan"].map(rules.normalize_kerusakan)
    d["nagari_k"] = d["nagari"].map(canonical_names(d["nagari"], rekap["nagari"])).map(rules.norm_text)
    cnt = d.pivot_table(index="nagari_k", columns="kat", values="id", aggfunc="count", fill_value=0)
    for c in ("berat", "sedang", "ringan", "hanyut"):
        if c not in cnt.columns:
            cnt[c] = 0
    det = pd.DataFrame({
        "rb": cnt["berat"], "rs": cnt["sedang"], "rr": cnt["ringan"], "hanyut": cnt["hanyut"],
    })
    det["jumlah"] = det.sum(axis=1)

    r = rekap.copy()
    r["nagari_k"] = r["nagari"].map(rules.norm_text)
    rek = r.groupby("nagari_k")[["jumlah", "rb", "rs", "rr", "hanyut"]].sum()

    idx = det.index.union(rek.index)
    det, rek = det.reindex(idx, fill_value=0), rek.reindex(idx, fill_value=0)
    out = pd.DataFrame({"nagari": idx, "rekap_jumlah": rek["jumlah"].values, "detail_jumlah": det["jumlah"].values})
    for c in ("jumlah", "rb", "rs", "rr", "hanyut"):
        out[f"selisih_{c}"] = (det[c] - rek[c]).values
    return out.reset_index(drop=True)
