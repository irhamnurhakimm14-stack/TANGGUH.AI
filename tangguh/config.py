"""Pengaturan terpusat TANGGUH.AI. Ubah di sini, bukan di banyak file."""
import os
import secrets
from pathlib import Path

try:  # .env opsional
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent
IS_VERCEL = os.getenv("VERCEL") == "1"
CLOUD_DB = bool(os.getenv("DATABASE_URL"))
DEMO_MODE = IS_VERCEL and not CLOUD_DB
if IS_VERCEL and os.getenv("VERCEL_ENV") == "production" and DEMO_MODE:
    raise RuntimeError("Database permanen wajib untuk production.")
DATA_DIR = Path("/tmp/tangguh-ai/data") if IS_VERCEL else ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DATASET_DIR = DATA_DIR / "dataset"
MODEL_DIR = ROOT / "models"
UPLOAD_DIR = Path("/tmp/tangguh-ai/uploads") if IS_VERCEL else ROOT / "uploads"
DB_PATH = DATA_DIR / "tangguh.sqlite3"

# Kode provinsi pada 2 digit pertama NIK. Sumatera Barat = 13.
ALLOWED_NIK_PROVINCE = ("13",)
# Seluruh kode provinsi resmi di Indonesia (11 s.d. 96)
ALL_PROVINCES_NIK = tuple(f"{i:02d}" for i in range(11, 97))

# Kotak batas koordinat (lat_min, lat_max, lon_min, lon_max)
BBOX_SUMBAR = (-3.6, 1.0, 98.4, 102.0)
BBOX_INDONESIA = (-11.5, 6.5, 94.5, 141.5)

# Kategori kerusakan (kunci internal -> label tampilan)
KATEGORI = {
    "ringan": "Rusak Ringan",
    "sedang_berat": "Rusak Sedang / Berat",
    "sedang": "Rusak Sedang",
    "berat": "Rusak Berat",
    "hanyut": "Hanyut / Hilang",
}

# Pemetaan label dari PDF/Excel ke kelas CNN
LABEL_MAP = {"RR": "ringan", "RS": "sedang", "RB": "berat"}

IMG_SIZE = 224
CLIP_MODEL_PATH = MODEL_DIR / "clip_lr.joblib"
CLIP_METRICS_PATH = MODEL_DIR / "metrics.json"
MODEL_PATH = CLIP_MODEL_PATH
CLASSES_PATH = MODEL_DIR / "classes.json"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
SECRET_KEY = os.getenv("SECRET_KEY") or (secrets.token_hex(32) if IS_VERCEL else "dev-only-secret")
if CLOUD_DB and IS_VERCEL and not os.getenv("SECRET_KEY"):
    raise RuntimeError("SECRET_KEY wajib untuk database cloud.")
