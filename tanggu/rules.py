"""Rule-Based: validasi kelengkapan, format, duplikasi, konsistensi, dan rekonsiliasi rekap.

Prinsip: sistem hanya MENANDAI (error / warning / info). Tidak ada data yang dihapus otomatis;
keputusan akhir tetap di petugas/admin.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher, get_close_matches

import pandas as pd

from . import config

ERROR, WARNING, INFO = "error", "warning", "info"


@dataclass
class Issue:
    code: str
    level: str
    field: str
    message: str

    def to_dict(self):
        return asdict(self)


# ---------------------------------------------------------------- normalisasi
def clean_digits(x) -> str:
    """'1302 1314.0' -> '13021314'. Nilai kosong -> ''."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    s = str(x).strip()
    if re.fullmatch(r"\d+\.0", s):  # Excel sering menyimpan angka sebagai 1302.0
        s = s[:-2]
    return re.sub(r"\D", "", s)


def normalize_damage(x) -> str:
    """'Rusak Ringan' / 'RR' / 'Hanyut / Hilang' -> 'Ringan' / 'Sedang' / 'Berat' / 'Hanyut'."""
    s = str(x or "").strip().lower()
    if not s or s == "nan":
        return ""
    if "hanyut" in s or "hilang" in s:
        return "Hanyut"
    if "berat" in s or s == "rb":
        return "Berat"
    if "sedang" in s or s == "rs":
        return "Sedang"
    if "ringan" in s or s == "rr":
        return "Ringan"
    return s.title()


def normalize_handling(x) -> str:
    s = str(x or "").strip().lower()
    if s.startswith("relok"):
        return "Relokasi"
    if s.startswith("rehab"):
        return "Rehabilitasi"
    return s.title() if s and s != "nan" else ""


def canonical_nagari(name: str, known: list[str], cutoff: float = 0.82) -> str:
    """Samakan ejaan: 'Aie Dingin' -> 'Aia Dingin'. Kalau tak ada yang mirip, kembalikan apa adanya."""
    name = (name or "").strip()
    if not name:
        return ""
    low = {k.lower(): k for k in known}
    m = get_close_matches(name.lower(), list(low), n=1, cutoff=cutoff)
    return low[m[0]] if m else name


# ---------------------------------------------------------------- format
def check_nik(nik) -> list[Issue]:
    d = clean_digits(nik)
    if not d:
        return [Issue("NIK_KOSONG", ERROR, "nik", "NIK belum diisi.")]
    if len(d) != 16:
        return [Issue("NIK_PANJANG", ERROR, "nik", f"NIK harus 16 digit (terisi {len(d)} digit).")]
    out: list[Issue] = []
    if d.endswith("0000"):
        out.append(Issue("NIK_URUT", ERROR, "nik", "4 digit terakhir NIK 0000 (tidak mungkin valid)."))
    day, month, yy = int(d[6:8]), int(d[8:10]), int(d[10:12])
    if day > 40:  # perempuan: tanggal lahir + 40
        day -= 40
    ok_date = 1 <= month <= 12 and any(
        1 <= day <= calendar.monthrange(y, month)[1] for y in (1900 + yy, 2000 + yy)
    )
    if not ok_date:
        out.append(Issue("NIK_TGL", ERROR, "nik", "Tanggal lahir di dalam NIK tidak valid."))
    if not d.startswith(config.NIK_PREFIX_LOKAL):
        out.append(Issue("NIK_LUAR", WARNING, "nik",
                         f"Kode wilayah NIK ({d[:4]}) di luar Kab. Solok - kemungkinan luar domisili."))
    return out


def check_kk(kk) -> list[Issue]:
    d = clean_digits(kk)
    if not d:
        return [Issue("KK_KOSONG", ERROR, "no_kk", "Nomor KK belum diisi.")]
    if len(d) != 16:
        return [Issue("KK_PANJANG", ERROR, "no_kk", f"Nomor KK harus 16 digit (terisi {len(d)} digit).")]
    return []


def parse_coordinate(text) -> tuple[float, float] | None:
    """Terima '0.95393 S, 100.62134 E', '-0.9539, 100.6213', '0.674358S 100.557530E'."""
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return None
    s = str(text).upper().replace(",", " ")
    m = re.findall(r"(-?\d+(?:\.\d+)?)\s*°?\s*([NSEW])?", s)
    if len(m) < 2:
        return None
    (lat, lat_h), (lon, lon_h) = m[0], m[1]
    lat, lon = float(lat), float(lon)
    if lat_h == "S":
        lat = -abs(lat)
    if lon_h == "W":
        lon = -abs(lon)
    return lat, lon


def check_coordinate(text, required: bool = False) -> list[Issue]:
    if not str(text or "").strip() or str(text).lower() == "nan":
        return [Issue("GPS_KOSONG", ERROR if required else INFO, "koordinat", "Koordinat GPS belum diisi.")]
    c = parse_coordinate(text)
    if c is None:
        return [Issue("GPS_FORMAT", ERROR, "koordinat", "Format koordinat tidak terbaca.")]
    lat, lon = c
    if not (config.LAT_RANGE[0] <= lat <= config.LAT_RANGE[1] and config.LON_RANGE[0] <= lon <= config.LON_RANGE[1]):
        return [Issue("GPS_LUAR", WARNING, "koordinat", "Koordinat berada di luar wilayah Kab. Solok.")]
    return []


# ---------------------------------------------------------------- satu laporan
def validate_record(rec: dict, require_photo: bool = False, require_gps: bool = False) -> list[Issue]:
    """Kelengkapan + format + konsistensi untuk SATU laporan."""
    issues: list[Issue] = []

    # kelengkapan
    for f, label in [("nama", "Nama pemilik"), ("kecamatan", "Kecamatan"), ("nagari", "Nagari")]:
        v = str(rec.get(f) or "").strip()
        if not v or v.lower() == "nan":
            issues.append(Issue(f"{f.upper()}_KOSONG", ERROR, f, f"{label} belum diisi."))
    if not str(rec.get("kerusakan") or "").strip() and not str(rec.get("deskripsi") or "").strip():
        issues.append(Issue("KERUSAKAN_KOSONG", ERROR, "kerusakan", "Tingkat kerusakan / deskripsi belum diisi."))
    if require_photo and not rec.get("foto"):
        issues.append(Issue("FOTO_KOSONG", ERROR, "foto", "Foto kerusakan belum diunggah."))

    # format
    issues += check_nik(rec.get("nik"))
    issues += check_kk(rec.get("no_kk"))
    issues += check_coordinate(rec.get("koordinat"), required=require_gps)

    # konsistensi kerusakan vs penanganan (berdasarkan pola data SK Solok)
    kr = normalize_damage(rec.get("kerusakan"))
    ph = normalize_handling(rec.get("penanganan"))
    if kr and kr not in config.KERUSAKAN_VALID:
        issues.append(Issue("KERUSAKAN_ASING", WARNING, "kerusakan", f"Kategori kerusakan '{kr}' tidak dikenal."))
    if kr == "Hanyut" and ph == "Rehabilitasi":
        issues.append(Issue("KONSIS_HANYUT", WARNING, "penanganan",
                            "Rumah hanyut/hilang tetapi penanganannya Rehabilitasi - biasanya Relokasi."))
    if kr == "Berat" and ph == "Rehabilitasi":
        issues.append(Issue("KONSIS_BERAT", WARNING, "penanganan",
                            "Rusak berat tetapi penanganannya Rehabilitasi - mohon periksa."))
    if kr in ("Ringan", "Sedang") and ph == "Relokasi":
        issues.append(Issue("KONSIS_RELOKASI", INFO, "penanganan",
                            "Kerusakan ringan/sedang dengan Relokasi - pastikan ada alasan lokasi rawan."))
    return issues


# ---------------------------------------------------------------- antar laporan
def _norm_name(s) -> str:
    return re.sub(r"[^a-z ]", "", str(s or "").lower()).strip()


def find_duplicates(df: pd.DataFrame, name_threshold: float = 0.9) -> dict[str, list[Issue]]:
    """Cari kemungkinan duplikasi. Kolom wajib: id, nik, no_kk, nama, nagari.

    Hasil: {id_laporan: [Issue, ...]}. Tidak ada yang dihapus - hanya ditandai
    'perlu pemeriksaan admin', karena satu rumah tangga bisa punya beberapa laporan perkembangan.
    """
    out: dict[str, list[Issue]] = {}

    def add(i, issue):
        out.setdefault(str(i), []).append(issue)

    d = df.copy()
    d["_nik"] = d["nik"].map(clean_digits)
    d["_kk"] = d["no_kk"].map(clean_digits)
    d["_nama"] = d["nama"].map(_norm_name)

    # 1) NIK sama persis
    for nik, g in d[d["_nik"].str.len() == 16].groupby("_nik"):
        if len(g) > 1:
            for i in g["id"]:
                others = ", ".join(str(x) for x in g["id"] if x != i)
                add(i, Issue("DUP_NIK", WARNING, "nik", f"NIK sama dengan laporan lain: {others}. Perlu pemeriksaan admin."))

    # 2) KK sama tapi NIK berbeda: bisa 1 rumah tangga, bisa juga input ganda
    for kk, g in d[d["_kk"].str.len() == 16].groupby("_kk"):
        if len(g) > 1 and g["_nik"].nunique() > 1:
            for i in g["id"]:
                others = ", ".join(str(x) for x in g["id"] if x != i)
                add(i, Issue("DUP_KK", INFO, "no_kk", f"Nomor KK sama dengan laporan: {others} (satu rumah tangga?)."))

    # 3) nama mirip di nagari yang sama dengan NIK berbeda (salah ketik NIK / orang yang sama)
    for _, g in d.groupby(d["nagari"].str.lower().fillna("")):
        rows = list(g.itertuples())
        for a in range(len(rows)):
            for b in range(a + 1, len(rows)):
                ra, rb = rows[a], rows[b]
                if ra._nik == rb._nik or not ra._nama or not rb._nama:
                    continue
                if SequenceMatcher(None, ra._nama, rb._nama).ratio() >= name_threshold:
                    add(ra.id, Issue("DUP_NAMA", WARNING, "nama", f"Nama mirip dengan laporan {rb.id} dengan NIK berbeda."))
                    add(rb.id, Issue("DUP_NAMA", WARNING, "nama", f"Nama mirip dengan laporan {ra.id} dengan NIK berbeda."))
    return out


# ---------------------------------------------------------------- rekap vs detail
def reconcile_with_rekap(df: pd.DataFrame, rekap: pd.DataFrame) -> pd.DataFrame:
    """Bandingkan jumlah baris detail per nagari/kategori dengan angka di dokumen rekap.

    rekap: kolom nagari, jumlah, berat, sedang, ringan, hanyut.
    Hasil: tabel selisih (kolom `selisih_total` != 0 berarti perlu ditelusuri).
    """
    known = list(rekap["nagari"])
    d = df.copy()
    d["_nagari"] = d["nagari"].map(lambda x: canonical_nagari(x, known))
    d["_kr"] = d["kerusakan"].map(normalize_damage)
    cnt = d.groupby(["_nagari", "_kr"]).size().unstack(fill_value=0)
    for c in ["Berat", "Sedang", "Ringan", "Hanyut"]:
        if c not in cnt:
            cnt[c] = 0
    det = pd.DataFrame({
        "nagari": cnt.index,
        "detail_total": cnt[["Berat", "Sedang", "Ringan", "Hanyut"]].sum(axis=1).values,
        "detail_berat": cnt["Berat"].values, "detail_sedang": cnt["Sedang"].values,
        "detail_ringan": cnt["Ringan"].values, "detail_hanyut": cnt["Hanyut"].values,
    })
    r = rekap.rename(columns={"jumlah": "rekap_total", "berat": "rekap_berat", "sedang": "rekap_sedang",
                              "ringan": "rekap_ringan", "hanyut": "rekap_hanyut"})
    m = r.merge(det, on="nagari", how="outer").fillna(0)
    for c in m.columns[1:]:
        m[c] = m[c].astype(int)
    m["selisih_total"] = m["detail_total"] - m["rekap_total"]
    return m.sort_values("selisih_total", key=lambda s: s.abs(), ascending=False).reset_index(drop=True)


def validate_batch(df: pd.DataFrame, **kw) -> dict[str, list[Issue]]:
    """Jalankan validasi per baris + deteksi duplikasi. df wajib punya kolom `id`."""
    res: dict[str, list[Issue]] = {}
    for rec in df.to_dict("records"):
        res[str(rec["id"])] = validate_record(rec, **kw)
    for i, extra in find_duplicates(df).items():
        res.setdefault(i, []).extend(extra)
    return res


def status_from_issues(issues: list[Issue]) -> str:
    """'perlu_periksa' bila ada error/warning, 'bersih' bila tidak."""
    return "perlu_periksa" if any(i.level in (ERROR, WARNING) for i in issues) else "bersih"
