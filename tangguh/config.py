"""Pengaturan terpusat TANGGUH.AI. Ubah di sini, bukan di banyak file."""
import os
from pathlib import Path

try:  # .env opsional
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DATASET_DIR = DATA_DIR / "dataset"
MODEL_DIR = ROOT / "models"
UPLOAD_DIR = ROOT / "uploads"
DB_PATH = DATA_DIR / "tangguh.sqlite3"

# Kode provinsi pada 2 digit pertama NIK. Sumatera Barat = 13.
ALLOWED_NIK_PROVINCE = ("13",)

# Kotak batas koordinat (lat_min, lat_max, lon_min, lon_max)
BBOX_SUMBAR = (-3.6, 1.0, 98.4, 102.0)
BBOX_INDONESIA = (-11.5, 6.5, 94.5, 141.5)

# Kategori kerusakan (kunci internal -> label tampilan)
KATEGORI = {"ringan": "Rusak Ringan", "sedang": "Rusak Sedang", "berat": "Rusak Berat"}

# Pemetaan label dari PDF/Excel ke kelas CNN
LABEL_MAP = {"RR": "ringan", "RS": "sedang", "RB": "berat"}

IMG_SIZE = 224
MODEL_PATH = MODEL_DIR / "damage_mobilenetv2.keras"
CLASSES_PATH = MODEL_DIR / "classes.json"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-secret")
