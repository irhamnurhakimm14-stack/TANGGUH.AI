"""Isi database dengan data SK resmi (status 'terverifikasi') untuk demo dashboard & ringkasan.

  python scripts/seed_db.py --file data/raw/DATA_BENCANA_LAMPIRAN_SK_KONFIRMASI.xlsx --sheet "LAMPIRAN UJI PUBLIK"
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tangguh import batch, data_loader as dl, db, rules

ap = argparse.ArgumentParser()
ap.add_argument("--file", required=True)
ap.add_argument("--sheet", default="LAMPIRAN UJI PUBLIK")
ap.add_argument("--reset", action="store_true", help="hapus isi tabel dulu")
a = ap.parse_args()

db.init_db()
if a.reset:
    with db.connect() as con:
        con.execute("DELETE FROM reports")
df = dl.load_table(a.file, a.sheet)
issues = batch.validate_dataframe(df)
by_id = {k: g.drop(columns=["id"]).to_dict("records") for k, g in issues.groupby("id")}
n = 0
for r in df.to_dict("records"):
    kat = rules.normalize_kerusakan(r["kerusakan"])
    iss = by_id.get(r["id"], [])
    db.insert_report({
        "id": r["id"], "nama": r["nama"], "nik": rules.clean_digits(r["nik"]), "no_kk": rules.clean_digits(r["no_kk"]),
        "kecamatan": r["kecamatan"], "nagari": r["nagari"], "jorong": r["jorong"],
        "kerusakan_final": kat, "penanganan": r["penanganan"], "status": "terverifikasi",
    }, iss, rules.status_from_issues(iss))
    n += 1
print(f"{n} laporan dimasukkan ke {db.config.DB_PATH}")
