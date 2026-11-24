"""Rule-Based: validasi kelengkapan, format, duplikasi, dan konsistensi.

Semua fungsi mengembalikan daftar `Issue` (dict) dan TIDAK PERNAH menghapus data.
Laporan yang dicurigai hanya diberi peringatan agar diperiksa admin (human-in-the-loop).

Issue = {"code", "level", "field", "message", "ref_id"(opsional)}
level: "error" (harus diperbaiki) | "warning" (perlu dicek admin) | "info"
"""
from __future__ import annotations

import calendar
import re
from difflib import SequenceMatcher
from typing import Iterable

from . import config

# ----------------------------------------------------------------- util dasar


def issue(code: str, level: str, field: str, message: str, ref_id=None) -> dict:
    d = {"code": code, "level": level, "field": field, "message": message}
    if ref_id is not None:
        d["ref_id"] = ref_id
    return d


def clean_digits(value) -> str:
    """Bersihkan angka dari Excel: tanda petik di depan, '.0' di belakang, spasi."""
    if value is None:
        return ""
    s = str(value).strip()
    if s.lower() in ("nan", "none", "-"):
        return ""
    s = s.lstrip("'`’").replace(" ", "").replace("-", "")
    if re.fullmatch(r"\d+\.0", s):
        s = s[:-2]
    return s


def is_blank(value) -> bool:
    return value is None or str(value).strip().lower() in ("", "nan", "none")


def norm_text(value) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", str(value or "").lower())).strip()


# ------------------------------------------------------------- 1. kelengkapan

DEFAULT_REQUIRED = {
    "nama": "Nama pemilik/pelapor",
    "provinsi": "Provinsi",
    "kabupaten": "Kabupaten/Kota",
    "kecamatan": "Kecamatan",
    "desa": "Desa/Kelurahan",
}


def validate_completeness(rec: dict, required: dict | None = None) -> list[dict]:
    required = required or DEFAULT_REQUIRED
    out = []
    for field, label in required.items():
        val = rec.get(field)
        # Fallback mapping untuk kompatibilitas data lama nagari/jorong dan data baru nasional
        if field == "desa" and is_blank(val):
            val = rec.get("nagari")
        elif field == "nagari" and is_blank(val):
            val = rec.get("desa")
        elif field == "dusun" and is_blank(val):
            val = rec.get("jorong")
        elif field == "jorong" and is_blank(val):
            val = rec.get("dusun")
        elif field == "provinsi" and is_blank(val) and (rec.get("nagari") or rec.get("jorong") or (rec.get("nik") and str(rec.get("nik")).startswith("13"))):
            val = "Sumatera Barat"
        elif field == "kabupaten" and is_blank(val) and (rec.get("nagari") or rec.get("jorong") or (rec.get("nik") and str(rec.get("nik")).startswith("13"))):
            val = "Kabupaten Solok"

        if is_blank(val):
            out.append(issue("KOSONG", "error", field, f"{label} wajib diisi."))
    return out


# ------------------------------------------------------------------ 2. format


def validate_nik(nik, allowed_province=None, field: str = "nik") -> list[dict]:
    """NIK: 16 digit; 2 digit awal kode provinsi; tanggal lahir valid
    (perempuan: tanggal + 40); 4 digit terakhir bukan 0000."""
    allowed_province = allowed_province or config.ALLOWED_NIK_PROVINCE
    s = clean_digits(nik)
    if not s:
        return []  # kekosongan ditangani validate_completeness
    out = []
    if not s.isdigit():
        return [issue("NIK_BUKAN_ANGKA", "error", field, "NIK hanya boleh berisi angka.")]
    if len(s) != 16:
        out.append(issue("NIK_PANJANG", "error", field, f"NIK harus 16 digit, terdeteksi {len(s)} digit."))
        return out  # sisa pemeriksaan tidak bermakna bila panjang salah
    if allowed_province != "all" and s[:2] not in allowed_province:
        out.append(
            issue("NIK_LUAR_WILAYAH", "warning", field,
                  f"Kode provinsi NIK {s[:2]} di luar wilayah ({', '.join(allowed_province)}). Cek domisili.")
        )
    dd, mm, yy = int(s[6:8]), int(s[8:10]), int(s[10:12])
    if dd > 40:
        dd -= 40
    ok = 1 <= mm <= 12 and dd >= 1
    if ok:
        ok = any(dd <= calendar.monthrange(y, mm)[1] for y in (1900 + yy, 2000 + yy))
    if not ok:
        out.append(issue("NIK_TGL_LAHIR", "error", field, "Tanggal lahir pada NIK tidak valid."))
    if s[12:] == "0000":
        out.append(issue("NIK_URUT_NOL", "error", field, "4 digit terakhir NIK 0000 (nomor urut tidak sah)."))
    return out


def validate_kk(kk, field: str = "no_kk") -> list[dict]:
    s = clean_digits(kk)
    if not s:
        return []
    if not s.isdigit() or len(s) != 16:
        return [issue("KK_FORMAT", "error", field, "Nomor KK harus 16 digit angka.")]
    return []


_COORD = re.compile(r"(-?\d+(?:[.,]\d+)?)\s*°?\s*([NSEW])?", re.I)


def parse_coordinate(text):
    """Terima '0.95393 S 100.62134 E', '0.9376S, 100.62E', '-0.95, 100.62'.
    Kembalikan (lat, lon) atau None."""
    if is_blank(text):
        return None
    parts = _COORD.findall(str(text).replace("\n", " "))
    if len(parts) < 2:
        return None
    vals = []
    for num, hemi in parts[:2]:
        v = float(num.replace(",", "."))
        if hemi.upper() in ("S", "W"):
            v = -abs(v)
        vals.append(v)
    lat, lon = vals
    if abs(lat) > 90 and abs(lon) <= 90:  # tertukar
        lat, lon = lon, lat
    return lat, lon


def validate_coordinate(text, field: str = "koordinat", check_sumbar: bool = True) -> list[dict]:
    if is_blank(text):
        return []
    p = parse_coordinate(text)
    if p is None:
        return [issue("GPS_FORMAT", "error", field, "Format koordinat tidak dikenali. Contoh: 0.95393 S 100.62134 E")]
    lat, lon = p
    a, b, c, d = config.BBOX_INDONESIA
    if not (a <= lat <= b and c <= lon <= d):
        return [issue("GPS_LUAR_INDONESIA", "error", field, f"Koordinat ({lat:.5f}, {lon:.5f}) di luar Indonesia.")]
    if check_sumbar:
        a, b, c, d = config.BBOX_SUMBAR
        if not (a <= lat <= b and c <= lon <= d):
            return [issue("GPS_LUAR_SUMBAR", "warning", field, f"Koordinat ({lat:.5f}, {lon:.5f}) di luar Sumatera Barat.")]
    return []


# ------------------------------------------------------------ 3. konsistensi

KERUSAKAN_ALIAS = {
    "rusak ringan": "ringan", "rr": "ringan", "ringan": "ringan",
    "rusak sedang": "sedang", "rs": "sedang", "sedang": "sedang",
    "rusak berat": "berat", "rb": "berat", "berat": "berat",
    "hanyut": "hanyut", "hanyut hilang": "hanyut", "hilang": "hanyut",
}


def normalize_kerusakan(value) -> str:
    return KERUSAKAN_ALIAS.get(norm_text(value), "")


def validate_consistency(rec: dict) -> list[dict]:
    """Aturan dari pola data SK: rumah hanyut / rusak berat SELALU relokasi."""
    kat = normalize_kerusakan(rec.get("kerusakan"))
    pen = norm_text(rec.get("penanganan"))
    out = []
    if kat in ("hanyut", "berat") and pen and "rehab" in pen:
        out.append(
            issue("KONSISTENSI_PENANGANAN", "warning", "penanganan",
                  f"Kerusakan '{rec.get('kerusakan')}' biasanya ditangani Relokasi, bukan Rehabilitasi.")
        )
    if not is_blank(rec.get("kerusakan")) and not kat:
        out.append(issue("KERUSAKAN_TAK_DIKENAL", "error", "kerusakan", f"Kategori kerusakan '{rec.get('kerusakan')}' tidak dikenal."))
    nik, kk = clean_digits(rec.get("nik")), clean_digits(rec.get("no_kk"))
    if nik and kk and nik == kk:
        out.append(issue("NIK_SAMA_KK", "error", "no_kk", "NIK dan Nomor KK identik; kemungkinan salah input."))
    return out


# --------------------------------------------------------------- 4. duplikasi


def find_duplicates(rec: dict, existing: Iterable[dict], name_threshold: float = 0.88) -> list[dict]:
    """Bandingkan laporan baru dengan laporan yang sudah ada secara cerdas dan komprehensif.

    Indikator duplikasi meliputi:
    1. NIK sama (kuat)
    2. No. KK sama (sedang)
    3. Nama mirip + lokasi sama (desa/kelurahan/dusun)
    4. Lokasi sama + deskripsi sangat mirip
    5. Foto identik (bila tersedia)

    Hasilnya PERINGATAN (warning): tidak ada laporan yang ditolak atau dihapus otomatis.
    Admin/Petugas dapat menelaah dan memutuskan apakah benar-benar duplikat atau kejadian terpisah.
    """
    nik, kk = clean_digits(rec.get("nik")), clean_digits(rec.get("no_kk"))
    nama = norm_text(rec.get("nama"))
    loc = norm_text(rec.get("desa") or rec.get("nagari") or rec.get("jorong") or rec.get("dusun"))
    desc = norm_text(rec.get("deskripsi"))
    foto = (rec.get("foto") or "").strip()

    out, seen = [], set()
    for ex in existing:
        rid = ex.get("id", ex.get("no"))
        if rec.get("id") is not None and rid == rec.get("id"):
            continue
        ex_loc = norm_text(ex.get("desa") or ex.get("nagari") or ex.get("jorong") or ex.get("dusun"))
        ex_desc = norm_text(ex.get("deskripsi"))
        ex_foto = (ex.get("foto") or "").strip()

        reason = None
        if nik and len(nik) == 16 and nik == clean_digits(ex.get("nik")):
            reason = ("DUP_NIK", "NIK yang sama")
        elif kk and len(kk) == 16 and kk == clean_digits(ex.get("no_kk")):
            reason = ("DUP_KK", "Nomor KK yang sama")
        elif nama and loc and loc == ex_loc and \
                SequenceMatcher(None, nama, norm_text(ex.get("nama"))).ratio() >= name_threshold:
            reason = ("DUP_NAMA", "nama dan lokasi yang mirip")
        elif loc and ex_loc and loc == ex_loc and desc and ex_desc and \
                SequenceMatcher(None, desc, ex_desc).ratio() >= 0.85:
            reason = ("DUP_LOKASI", "lokasi dan deskripsi yang sangat mirip")
        elif foto and ex_foto and foto == ex_foto:
            reason = ("DUP_FOTO", "foto kerusakan yang sama")

        if reason and rid not in seen:
            seen.add(rid)
            out.append(
                issue(reason[0], "warning", "nik",
                      f"Terindikasi duplikat dari laporan {rid} ({reason[1]}).", ref_id=rid)
            )
    return out


# ------------------------------------------------------------ gabungan 1 data


def validate_record(rec: dict, existing: Iterable[dict] | None = None,
                    required: dict | None = None, allowed_province=None) -> list[dict]:
    """Jalankan semua aturan untuk satu laporan dengan cakupan nasional."""
    issues = []
    issues += validate_completeness(rec, required)

    # Bila wilayah bukan Sumatera Barat atau mode nasional aktif, terima kode NIK dari seluruh provinsi
    prov = (rec.get("provinsi") or "").strip().lower()
    is_non_sumbar = prov != "" and prov not in ("sumatera barat", "sumbar", "-")
    prov_rule = allowed_province or ("all" if is_non_sumbar else config.ALLOWED_NIK_PROVINCE)

    issues += validate_nik(rec.get("nik"), allowed_province=prov_rule)
    issues += validate_kk(rec.get("no_kk"))
    issues += validate_coordinate(rec.get("koordinat"), check_sumbar=(not is_non_sumbar))
    issues += validate_consistency(rec)
    if existing is not None:
        issues += find_duplicates(rec, existing)
    return issues


def status_from_issues(issues: list[dict]) -> str:
    """'perlu_perbaikan' bila ada error; 'perlu_cek' bila hanya warning; 'lolos' bila bersih."""
    levels = {i["level"] for i in issues}
    if "error" in levels:
        return "perlu_perbaikan"
    if "warning" in levels:
        return "perlu_cek"
    return "lolos"
