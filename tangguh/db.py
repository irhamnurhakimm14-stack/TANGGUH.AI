"""Database SQLite (gratis, tanpa instalasi; satu file data/tangguh.sqlite3)."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  nama TEXT, nik TEXT, no_kk TEXT,
  kecamatan TEXT, nagari TEXT, jorong TEXT,
  koordinat TEXT, deskripsi TEXT, foto TEXT,
  kerusakan_ai TEXT, ai_confidence REAL,
  kerusakan_final TEXT, penanganan TEXT,
  rules_status TEXT, issues_json TEXT DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'menunggu',   -- draft | menunggu | terverifikasi | ditolak
  catatan_admin TEXT
);
"""


@contextmanager
def connect():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    with connect() as con:
        con.executescript(SCHEMA)


def _next_id(con) -> str:
    n = con.execute("SELECT COUNT(*) FROM reports").fetchone()[0] + 1
    while con.execute("SELECT 1 FROM reports WHERE id=?", (f"TG-{n:03d}",)).fetchone():
        n += 1
    return f"TG-{n:03d}"


def insert_report(rec: dict, issues: list[dict], rules_status: str) -> str:
    with connect() as con:
        rid = rec.get("id") or _next_id(con)
        con.execute(
            """INSERT INTO reports (id, created_at, nama, nik, no_kk, kecamatan, nagari, jorong, koordinat,
               deskripsi, foto, kerusakan_ai, ai_confidence, kerusakan_final, penanganan, rules_status,
               issues_json, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, rec.get("created_at") or datetime.now().isoformat(timespec="seconds"),
             rec.get("nama"), rec.get("nik"), rec.get("no_kk"), rec.get("kecamatan"), rec.get("nagari"),
             rec.get("jorong"), rec.get("koordinat"), rec.get("deskripsi"), rec.get("foto"),
             rec.get("kerusakan_ai"), rec.get("ai_confidence"), rec.get("kerusakan_final"),
             rec.get("penanganan"), rules_status, json.dumps(issues, ensure_ascii=False),
             rec.get("status", "menunggu")),
        )
        return rid


def get_report(rid: str):
    with connect() as con:
        r = con.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
    return _row(r) if r else None


def list_reports(status: str | None = None, limit: int | None = None) -> list[dict]:
    """Draft TIDAK ikut daftar biasa; ambil lewat status='draft'."""
    q, args = "SELECT * FROM reports", []
    if status:
        q += " WHERE status=?"
        args.append(status)
    else:
        q += " WHERE status != 'draft'"
    q += " ORDER BY created_at DESC, id DESC"
    if limit:
        q += f" LIMIT {int(limit)}"
    with connect() as con:
        return [_row(r) for r in con.execute(q, args).fetchall()]


def verify_report(rid: str, kerusakan_final: str, penanganan: str, status: str, catatan: str = ""):
    with connect() as con:
        con.execute("UPDATE reports SET kerusakan_final=?, penanganan=?, status=?, catatan_admin=? WHERE id=?",
                    (kerusakan_final, penanganan, status, catatan, rid))


def counts() -> dict:
    with connect() as con:
        rows = con.execute("SELECT status, COUNT(*) c FROM reports GROUP BY status").fetchall()
    d = {r["status"]: r["c"] for r in rows}
    d["total"] = sum(v for k, v in d.items() if k != "draft")  # draft belum dianggap laporan
    d["draft"] = d.get("draft", 0)
    return d


def update_report(rid: str, rec: dict, issues: list[dict], rules_status: str, status: str):
    """Perbarui isi laporan (dipakai untuk menyimpan ulang draft atau mengirim draft)."""
    with connect() as con:
        con.execute(
            """UPDATE reports SET created_at=?, nama=?, nik=?, no_kk=?, kecamatan=?, nagari=?, jorong=?,
               koordinat=?, deskripsi=?, foto=?, kerusakan_ai=?, ai_confidence=?, rules_status=?,
               issues_json=?, status=? WHERE id=?""",
            (datetime.now().isoformat(timespec="seconds"), rec.get("nama"), rec.get("nik"), rec.get("no_kk"),
             rec.get("kecamatan"), rec.get("nagari"), rec.get("jorong"), rec.get("koordinat"),
             rec.get("deskripsi"), rec.get("foto"), rec.get("kerusakan_ai"), rec.get("ai_confidence"),
             rules_status, json.dumps(issues, ensure_ascii=False), status, rid))


def update_issues(rid: str, issues: list[dict], rules_status: str, catatan: str | None = None):
    """Perbarui daftar temuan Rule-Based (mis. setelah admin menandai 'bukan duplikat')."""
    with connect() as con:
        if catatan is None:
            con.execute("UPDATE reports SET issues_json=?, rules_status=? WHERE id=?",
                        (json.dumps(issues, ensure_ascii=False), rules_status, rid))
        else:
            con.execute("UPDATE reports SET issues_json=?, rules_status=?, catatan_admin=? WHERE id=?",
                        (json.dumps(issues, ensure_ascii=False), rules_status, catatan, rid))


def delete_draft(rid: str) -> dict | None:
    """Hapus HANYA laporan berstatus draft. Mengembalikan baris yang dihapus (atau None)."""
    r = get_report(rid)
    if not r or r["status"] != "draft":
        return None
    with connect() as con:
        con.execute("DELETE FROM reports WHERE id=? AND status='draft'", (rid,))
    return r


def _row(r) -> dict:
    d = dict(r)
    d["issues"] = json.loads(d.pop("issues_json") or "[]")
    return d
