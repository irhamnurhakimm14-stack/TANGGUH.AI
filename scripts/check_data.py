"""Jalankan Rule-Based ke file data asli dan tulis laporan CSV.

Contoh:
  python scripts/check_data.py --file data/raw/DATA_BENCANA_LAMPIRAN_SK_KONFIRMASI.xlsx \
      --sheet "LAMPIRAN UJI PUBLIK" --rekap data/rekap_solok_2025.csv
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from tangguh import batch, data_loader as dl

ap = argparse.ArgumentParser()
ap.add_argument("--file", required=True)
ap.add_argument("--sheet", default=0)
ap.add_argument("--rekap", help="CSV rekap (data/rekap_solok_2025.csv) atau PDF rekap")
ap.add_argument("--compare", help="file versi lain untuk dibandingkan (opsional)")
ap.add_argument("--out", default="data/laporan_validasi")
a = ap.parse_args()

out = Path(a.out)
out.mkdir(parents=True, exist_ok=True)
df = dl.load_table(a.file, a.sheet)
iss = batch.validate_dataframe(df)
iss.to_csv(out / "temuan.csv", index=False)
print(f"{len(df)} baris diperiksa, {len(iss)} temuan -> {out/'temuan.csv'}")
print(batch.summarize_issues(iss).to_string(index=False))

if a.rekap:
    rk = dl.load_rekap_csv(a.rekap) if a.rekap.endswith(".csv") else dl.parse_rekap_pdf(a.rekap)
    cmp_ = dl.compare_with_rekap(df, rk)
    bad = cmp_[cmp_.filter(like="selisih").abs().sum(axis=1) > 0]
    cmp_.to_csv(out / "rekap_vs_detail.csv", index=False)
    print("\nSelisih rekap vs detail:", "tidak ada" if bad.empty else "")
    if not bad.empty:
        print(bad.to_string(index=False))

if a.compare:
    other = dl.load_table(a.compare, a.sheet)
    diff = batch.compare_versions(df, other)
    diff.to_csv(out / "beda_versi.csv", index=False)
    print(f"\nPerbedaan antar versi: {len(diff)} (lihat {out/'beda_versi.csv'})")
