from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "tanggu.db"
UPLOAD_DIR = DATA_DIR / "uploads"
REF_REKAP = DATA_DIR / "reference" / "rekap_uji_publik.csv"
MODEL_PATH = ROOT / "models" / "damage_mobilenetv2.keras"

# Awalan NIK (kode provinsi+kabupaten) untuk Kabupaten Solok. Di luar ini = "luar domisili".
NIK_PREFIX_LOKAL = ("1302",)
# Kotak koordinat kasar Kab. Solok / Sumbar tengah (lintang, bujur)
LAT_RANGE = (-1.6, 0.3)
LON_RANGE = (100.0, 101.3)

KELAS = ["ringan", "sedang", "berat"]
KERUSAKAN_VALID = ["Ringan", "Sedang", "Berat", "Hanyut"]
PENANGANAN_VALID = ["Rehabilitasi", "Relokasi"]

STATUS_MENUNGGU = "menunggu"
STATUS_TERVERIFIKASI = "terverifikasi"
STATUS_DITOLAK = "ditolak"
