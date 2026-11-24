"""Database SQLite (gratis, tanpa instalasi; satu file data/tangguh.sqlite3)."""
from __future__ import annotations

import json
import sqlite3
import os
import uuid
from contextlib import contextmanager
from datetime import datetime

from werkzeug.security import check_password_hash, generate_password_hash

from . import config, cloud_db

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  nama TEXT, nik TEXT, no_kk TEXT,
  provinsi TEXT DEFAULT 'Sumatera Barat',
  kabupaten TEXT DEFAULT 'Kabupaten Solok',
  kecamatan TEXT,
  desa TEXT,
  dusun TEXT,
  rt_rw TEXT,
  nagari TEXT, jorong TEXT,                 -- kolom kompatibilitas lama
  koordinat TEXT, deskripsi TEXT, foto TEXT,
  kerusakan_ai TEXT, ai_confidence REAL,
  kerusakan_final TEXT, penanganan TEXT,
  rules_status TEXT, issues_json TEXT DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'menunggu',   -- draft | menunggu | terverifikasi | ditolak | perlu_revisi | duplikat
  catatan_admin TEXT,
  catatan_petugas TEXT,
  petugas_nama TEXT,
  status_pemeriksaan TEXT DEFAULT 'menunggu_pemeriksaan',  -- menunggu_pemeriksaan | sedang_diperiksa | diteruskan
  waktu_pemeriksaan_petugas TEXT,
  admin_nama TEXT,
  waktu_verifikasi_admin TEXT,
  duplikat_dari TEXT,
  is_archived INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL,                       -- admin | petugas
  nama_lengkap TEXT,
  created_at TEXT NOT NULL
);
"""


@contextmanager
def connect():
    if config.CLOUD_DB:
        with cloud_db.connect() as con:
            yield con
        return
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    if config.CLOUD_DB:
        cloud_db.initialize(SCHEMA)
        seed_users()
        return
    with connect() as con:
        con.executescript(SCHEMA)

        # Migrasi kolom tambahan pada tabel reports jika database sudah ada sebelumnya
        cols = [c[1] for c in con.execute("PRAGMA table_info(reports)").fetchall()]
        extra_cols = [
            ("catatan_petugas", "TEXT"),
            ("petugas_nama", "TEXT"),
            ("status_pemeriksaan", "TEXT DEFAULT 'menunggu_pemeriksaan'"),
            ("waktu_pemeriksaan_petugas", "TEXT"),
            ("admin_nama", "TEXT"),
            ("waktu_verifikasi_admin", "TEXT"),
            ("provinsi", "TEXT DEFAULT 'Sumatera Barat'"),
            ("kabupaten", "TEXT DEFAULT 'Kabupaten Solok'"),
            ("desa", "TEXT"),
            ("dusun", "TEXT"),
            ("rt_rw", "TEXT"),
            ("duplikat_dari", "TEXT"),
            ("is_archived", "INTEGER DEFAULT 0"),
        ]
        for col_name, col_type in extra_cols:
            if col_name not in cols:
                con.execute(f"ALTER TABLE reports ADD COLUMN {col_name} {col_type}")

    seed_users()


def seed_users():
    """Seed accounts; cloud requires explicitly configured passwords."""
    if config.CLOUD_DB:
        with connect() as con:
            for role in ("admin", "petugas"):
                password = os.getenv(role.upper() + "_PASSWORD")
                if password and len(password) >= 12:
                    con.execute("INSERT INTO users (username,password_hash,role,nama_lengkap,created_at) VALUES (?,?,?,?,?) ON CONFLICT (username) DO NOTHING", (role,generate_password_hash(password),role,role.capitalize(),datetime.now().isoformat(timespec="seconds")))
        return
    with connect() as con:
        if not con.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
            con.execute(
                "INSERT INTO users (username, password_hash, role, nama_lengkap, created_at) VALUES (?, ?, ?, ?, ?)",
                ("admin", generate_password_hash("admin123"), "admin", "Administrator Utama",
                 datetime.now().isoformat(timespec="seconds")),
            )
        if not con.execute("SELECT 1 FROM users WHERE username='petugas'").fetchone():
            con.execute(
                "INSERT INTO users (username, password_hash, role, nama_lengkap, created_at) VALUES (?, ?, ?, ?, ?)",
                ("petugas", generate_password_hash("petugas123"), "petugas", "Petugas Lapangan Demo",
                 datetime.now().isoformat(timespec="seconds")),
            )


def authenticate_user(username: str, password: str) -> dict | None:
    """Verifikasi username & password hash. Jangan return jika password salah."""
    with connect() as con:
        r = con.execute("SELECT * FROM users WHERE username=?", (username.strip(),)).fetchone()
        if r and check_password_hash(r["password_hash"], password):
            u = dict(r)
            u.pop("password_hash", None)
            return u
    return None


def get_user_by_username(username: str) -> dict | None:
    with connect() as con:
        r = con.execute("SELECT id, username, role, nama_lengkap, created_at FROM users WHERE username=?",
                        (username.strip(),)).fetchone()
        return dict(r) if r else None


def verify_user_password(username: str, password: str) -> bool:
    with connect() as con:
        r = con.execute("SELECT password_hash FROM users WHERE username=?", (username.strip(),)).fetchone()
        return bool(r and check_password_hash(r["password_hash"], password))


def change_user_password(username: str, new_password: str) -> bool:
    """Ganti password akun dengan hash baru."""
    with connect() as con:
        con.execute("UPDATE users SET password_hash=? WHERE username=?",
                    (generate_password_hash(new_password), username.strip()))
        return True


def _next_id(con) -> str:
    if config.CLOUD_DB:
        return "TG-" + uuid.uuid4().hex[:16]
    n = con.execute("SELECT COUNT(*) FROM reports").fetchone()[0] + 1
    while con.execute("SELECT 1 FROM reports WHERE id=?", (f"TG-{n:03d}",)).fetchone():
        n += 1
    return f"TG-{n:03d}"


def get_effective_status(r: dict) -> str:
    """Status konsisten satu pintu untuk seluruh tampilan sistem.
    Prioritas: Keputusan Admin > Status Pemeriksaan Petugas > Menunggu Pemeriksaan."""
    st = r.get("status")
    if st in ("terverifikasi", "ditolak", "perlu_revisi", "duplikat"):
        return st
    if st == "draft":
        return "draft"
    sp = r.get("status_pemeriksaan")
    if sp in ("sedang_diperiksa", "diteruskan"):
        return sp
    return "menunggu_pemeriksaan"


def insert_report(rec: dict, issues: list[dict], rules_status: str) -> str:
    with connect() as con:
        rid = rec.get("id") or _next_id(con)
        # Normalisasi alamat nasional dua arah
        provinsi = rec.get("provinsi") or ("Sumatera Barat" if rec.get("nagari") else "-")
        kabupaten = rec.get("kabupaten") or ("Kabupaten Solok" if rec.get("nagari") else "-")
        desa = rec.get("desa") or rec.get("nagari") or ""
        dusun = rec.get("dusun") or rec.get("jorong") or ""
        nagari = rec.get("nagari") or desa
        jorong = rec.get("jorong") or dusun
        rt_rw = rec.get("rt_rw") or ""
        duplikat_dari = rec.get("duplikat_dari") or ""
        is_archived = int(rec.get("is_archived") or 0)

        con.execute(
            """INSERT INTO reports (id, created_at, nama, nik, no_kk, provinsi, kabupaten, kecamatan,
               desa, dusun, rt_rw, nagari, jorong, koordinat, deskripsi, foto, kerusakan_ai, ai_confidence,
               kerusakan_final, penanganan, rules_status, issues_json, status, status_pemeriksaan,
               catatan_petugas, petugas_nama, duplikat_dari, is_archived)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, rec.get("created_at") or datetime.now().isoformat(timespec="seconds"),
             rec.get("nama"), rec.get("nik"), rec.get("no_kk"), provinsi, kabupaten, rec.get("kecamatan"),
             desa, dusun, rt_rw, nagari, jorong, rec.get("koordinat"), rec.get("deskripsi"), rec.get("foto"),
             rec.get("kerusakan_ai"), rec.get("ai_confidence"), rec.get("kerusakan_final"),
             rec.get("penanganan"), rules_status, json.dumps(issues, ensure_ascii=False),
             rec.get("status", "menunggu"),
             rec.get("status_pemeriksaan", "menunggu_pemeriksaan"),
             rec.get("catatan_petugas", ""),
             rec.get("petugas_nama", ""),
             duplikat_dari, is_archived),
        )
        return rid


def get_report(rid: str):
    with connect() as con:
        r = con.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
    return _row(r) if r else None


def list_reports(status: str | None = None, limit: int | None = None) -> list[dict]:
    """Mengambil daftar laporan dengan filter status atau arsip."""
    with connect() as con:
        raw_rows = con.execute("SELECT * FROM reports ORDER BY created_at DESC, id DESC").fetchall()

    all_reports = [_row(r) for r in raw_rows]

    if status == "draft":
        return [r for r in all_reports if r["status"] == "draft"]

    # Jangan sertakan draft dalam daftar biasa
    non_draft = [r for r in all_reports if r["status"] != "draft"]

    if status == "arsip":
        return [r for r in non_draft if r["status"] in ("ditolak", "duplikat") or r.get("is_archived") == 1]

    if status in ("menunggu_pemeriksaan", "sedang_diperiksa", "diteruskan"):
        return [r for r in non_draft if r["effective_status"] == status]

    if status == "menunggu":
        return [r for r in non_draft if r["effective_status"] in ("menunggu_pemeriksaan", "sedang_diperiksa", "diteruskan")]

    if status:
        return [r for r in non_draft if r["effective_status"] == status or r["status"] == status]

    if limit:
        return non_draft[:int(limit)]
    return non_draft


def petugas_review_report(rid: str, status_pemeriksaan: str, catatan: str, petugas_nama: str):
    """Pemeriksaan awal oleh petugas (memberikan catatan & meneruskan ke admin)."""
    with connect() as con:
        con.execute(
            """UPDATE reports SET status_pemeriksaan=?, catatan_petugas=?, petugas_nama=?,
               waktu_pemeriksaan_petugas=? WHERE id=?""",
            (status_pemeriksaan, catatan, petugas_nama, datetime.now().isoformat(timespec="seconds"), rid),
        )


def verify_report(rid: str, kerusakan_final: str, penanganan: str, status: str, catatan: str = "",
                  admin_nama: str = "", duplikat_dari: str = ""):
    """Verifikasi keputusan akhir oleh admin."""
    with connect() as con:
        if duplikat_dari:
            con.execute(
                """UPDATE reports SET kerusakan_final=?, penanganan=?, status=?, catatan_admin=?,
                   admin_nama=?, waktu_verifikasi_admin=?, duplikat_dari=? WHERE id=?""",
                (kerusakan_final, penanganan, status, catatan, admin_nama,
                 datetime.now().isoformat(timespec="seconds"), duplikat_dari, rid),
            )
        else:
            con.execute(
                """UPDATE reports SET kerusakan_final=?, penanganan=?, status=?, catatan_admin=?,
                   admin_nama=?, waktu_verifikasi_admin=? WHERE id=?""",
                (kerusakan_final, penanganan, status, catatan, admin_nama,
                 datetime.now().isoformat(timespec="seconds"), rid),
            )


def mark_duplicate(rid: str, main_rid: str, catatan: str, admin_nama: str):
    """Menetapkan laporan sebagai duplikat dan menghubungkannya dengan laporan utama."""
    with connect() as con:
        con.execute(
            """UPDATE reports SET status='duplikat', duplikat_dari=?, catatan_admin=?,
               admin_nama=?, waktu_verifikasi_admin=? WHERE id=?""",
            (main_rid, catatan, admin_nama, datetime.now().isoformat(timespec="seconds"), rid),
        )


def archive_report(rid: str, is_archived: bool = True):
    """Pengarsipan laporan (soft delete/archive)."""
    with connect() as con:
        con.execute("UPDATE reports SET is_archived=? WHERE id=?", (1 if is_archived else 0, rid))


def counts() -> dict:
    all_reports = list_reports()  # non-draft
    drafts = list_reports("draft")

    d = {
        "total": len(all_reports),
        "draft": len(drafts),
        "terverifikasi": 0,
        "perlu_revisi": 0,
        "ditolak": 0,
        "duplikat": 0,
        "arsip": 0,
        "menunggu_pemeriksaan": 0,
        "sedang_diperiksa": 0,
        "diteruskan": 0,
        "menunggu": 0,
    }

    for r in all_reports:
        eff = r["effective_status"]
        if eff == "terverifikasi":
            d["terverifikasi"] += 1
        elif eff == "perlu_revisi":
            d["perlu_revisi"] += 1
        elif eff == "ditolak":
            d["ditolak"] += 1
        elif eff == "duplikat":
            d["duplikat"] += 1
        elif eff == "menunggu_pemeriksaan":
            d["menunggu_pemeriksaan"] += 1
            d["menunggu"] += 1
        elif eff == "sedang_diperiksa":
            d["sedang_diperiksa"] += 1
            d["menunggu"] += 1
        elif eff == "diteruskan":
            d["diteruskan"] += 1
            d["menunggu"] += 1

        if r.get("is_archived") == 1 or eff in ("ditolak", "duplikat"):
            d["arsip"] += 1

    return d


def update_report(rid: str, rec: dict, issues: list[dict], rules_status: str, status: str):
    """Perbarui isi laporan (dipakai untuk menyimpan ulang draft atau mengirim draft)."""
    provinsi = rec.get("provinsi") or ("Sumatera Barat" if rec.get("nagari") else "-")
    kabupaten = rec.get("kabupaten") or ("Kabupaten Solok" if rec.get("nagari") else "-")
    desa = rec.get("desa") or rec.get("nagari") or ""
    dusun = rec.get("dusun") or rec.get("jorong") or ""
    nagari = rec.get("nagari") or desa
    jorong = rec.get("jorong") or dusun
    rt_rw = rec.get("rt_rw") or ""

    with connect() as con:
        con.execute(
            """UPDATE reports SET created_at=?, nama=?, nik=?, no_kk=?, provinsi=?, kabupaten=?,
               kecamatan=?, desa=?, dusun=?, rt_rw=?, nagari=?, jorong=?,
               koordinat=?, deskripsi=?, foto=?, kerusakan_ai=?, ai_confidence=?, rules_status=?,
               issues_json=?, status=? WHERE id=?""",
            (datetime.now().isoformat(timespec="seconds"), rec.get("nama"), rec.get("nik"), rec.get("no_kk"),
             provinsi, kabupaten, rec.get("kecamatan"), desa, dusun, rt_rw, nagari, jorong,
             rec.get("koordinat"), rec.get("deskripsi"), rec.get("foto"), rec.get("kerusakan_ai"),
             rec.get("ai_confidence"), rules_status, json.dumps(issues, ensure_ascii=False), status, rid))


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
    """Hapus HANYA laporan berstatus draft."""
    r = get_report(rid)
    if not r or r["status"] != "draft":
        return None
    with connect() as con:
        con.execute("DELETE FROM reports WHERE id=? AND status='draft'", (rid,))
    return r


def delete_report_admin(rid: str) -> bool:
    """Penghapusan oleh admin dengan hak istimewa (bila diperlukan)."""
    with connect() as con:
        con.execute("DELETE FROM reports WHERE id=?", (rid,))
        return True


def _row(r) -> dict:
    d = dict(r)
    d["issues"] = json.loads(d.pop("issues_json") or "[]")
    d["status_pemeriksaan"] = d.get("status_pemeriksaan") or "menunggu_pemeriksaan"
    d["catatan_petugas"] = d.get("catatan_petugas") or ""
    d["petugas_nama"] = d.get("petugas_nama") or ""
    d["admin_nama"] = d.get("admin_nama") or ""
    d["duplikat_dari"] = d.get("duplikat_dari") or ""
    d["is_archived"] = int(d.get("is_archived") or 0)

    # Pemetaan fallback alamat nasional vs lama
    desa = d.get("desa") or d.get("nagari") or ""
    dusun = d.get("dusun") or d.get("jorong") or ""
    d["desa"] = desa
    d["dusun"] = dusun
    d["nagari"] = d.get("nagari") or desa
    d["jorong"] = d.get("jorong") or dusun
    d["provinsi"] = d.get("provinsi") or ("Sumatera Barat" if d.get("nagari") else "-")
    d["kabupaten"] = d.get("kabupaten") or ("Kabupaten Solok" if d.get("nagari") else "-")
    d["rt_rw"] = d.get("rt_rw") or ""

    # Status efektif terpadu
    d["effective_status"] = get_effective_status(d)
    return d
