"""Ekstrak foto + label tingkat kerusakan dari PDF rekap menjadi dataset CNN.

Cara kerja: di PDF, tiap rumah punya foto dan tanda centang (✓) pada kolom RB/RS/RR/...
Foto dipasangkan dengan ✓ yang posisi vertikalnya berada di dalam kotak foto tersebut,
kolom ✓ ditentukan dari posisi horizontalnya.

Keluaran:
  data/dataset/<kelas>/<file>.jpg      (kelas: ringan, sedang, berat, lainnya)
  data/dataset/manifest.csv            (file, label, raw_label, source, page, group)

`group` = identitas rumah. Dipakai saat split agar foto rumah yang sama TIDAK
terpisah ke train dan test (mencegah kebocoran data).

Jalankan:
  python -m tangguh.ml.extract_dataset
"""
from __future__ import annotations

import argparse
import io
import re
from pathlib import Path

import pandas as pd
import pdfplumber
from PIL import Image

from .. import config

# Kolom label: nama -> (x_tengah, ...) diukur dari header PDF (satuan pt, halaman A4 landscape)
SOURCES = {
    # Verifikasi 4-6 Jan 2026 (1 foto per rumah)
    "verifikasi": {
        "pattern": "*REKAP_DATA_VERIFIKASI*.pdf",
        "label_x": {"RB": 553, "RS": 570, "RR": 587, "TRD": 604, "HANYUT": 624},
        "x_range": (545, 640),     # hanya ✓ di blok tingkat kerusakan
        "photo_min_x": 400,        # foto dokumentasi ada di kolom kanan-tengah
        "photo_max_x": 560,
    },
    # Galodo Arosuka 2026 (sampai 4 foto per rumah)
    "arosuka": {
        "pattern": "*AROSUKA*.pdf",
        "label_x": {"RB": 343, "RS": 362, "RR": 383, "TRD": 403, "RAWAN": 427},
        "x_range": (335, 440),
        "photo_min_x": 478,
        "photo_max_x": 830,
    },
}

MIN_SIDE = 40  # abaikan gambar kecil (logo/ikon)


def _nearest(label_x: dict, x_center: float, tol: float = 9.0):
    name, dist = min(((k, abs(v - x_center)) for k, v in label_x.items()), key=lambda t: t[1])
    return name if dist <= tol else None


def _image_bytes(page, im) -> Image.Image:
    """Ambil foto asli dari PDF (JPEG langsung bila bisa; kalau tidak, crop render halaman)."""
    try:
        stream = im["stream"]
        if "DCTDecode" in str(stream.get("Filter")):
            return Image.open(io.BytesIO(stream.get_data())).convert("RGB")
    except Exception:
        pass
    box = (im["x0"], im["top"], im["x1"], im["bottom"])
    return page.crop(box).to_image(resolution=150).original.convert("RGB")


def extract_pdf(pdf_path: Path, name: str, out_dir: Path) -> list[dict]:
    cfg = SOURCES[name]
    rows = []
    with pdfplumber.open(pdf_path) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            checks = [
                (c["x0"] + (c["x1"] - c["x0"]) / 2, (c["top"] + c["bottom"]) / 2)
                for c in page.chars
                if c["text"] == "✓" and cfg["x_range"][0] <= c["x0"] <= cfg["x_range"][1]
            ]
            imgs = [
                im for im in page.images
                if cfg["photo_min_x"] <= im["x0"] <= cfg["photo_max_x"]
                and (im["x1"] - im["x0"]) >= MIN_SIDE and (im["bottom"] - im["top"]) >= MIN_SIDE
            ]
            for k, im in enumerate(imgs):
                cy_lo, cy_hi = im["top"] - 3, im["bottom"] + 3
                # untuk Arosuka: foto 2-4 sejajar dengan foto 1, jadi cocokkan ✓ lewat rentang vertikal
                hit = [x for x, y in checks if cy_lo <= y <= cy_hi]
                raw = _nearest(cfg["label_x"], hit[0]) if hit else None
                label = config.LABEL_MAP.get(raw, "lainnya" if raw else "tanpa_label")
                # identitas rumah = halaman + pita vertikal (dibulatkan 8pt)
                group = f"{name}_p{pno}_y{int(round(im['top'] / 8))}"
                try:
                    img = _image_bytes(page, im)
                except Exception:
                    continue
                folder = out_dir / label
                folder.mkdir(parents=True, exist_ok=True)
                fname = f"{name}_p{pno:03d}_{k:02d}.jpg"
                img.save(folder / fname, quality=95)
                rows.append({"file": f"{label}/{fname}", "label": label, "raw_label": raw or "",
                             "source": name, "page": pno, "group": group,
                             "w": img.width, "h": img.height})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=str(config.RAW_DIR), help="folder berisi PDF")
    ap.add_argument("--out", default=str(config.DATASET_DIR))
    a = ap.parse_args()
    raw, out = Path(a.raw), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    allrows = []
    for name, cfg in SOURCES.items():
        found = sorted(raw.glob(cfg["pattern"]))
        if not found:
            print(f"[lewati] PDF '{name}' tidak ditemukan di {raw} (pola {cfg['pattern']})")
            continue
        rows = extract_pdf(found[0], name, out)
        print(f"[{name}] {found[0].name}: {len(rows)} foto")
        allrows += rows
    df = pd.DataFrame(allrows)
    df.to_csv(out / "manifest.csv", index=False)
    if df.empty:
        print("Tidak ada foto terekstrak.")
        return
    print("\nJumlah foto per kelas dan sumber:")
    print(pd.crosstab(df["label"], df["source"], margins=True).to_string())
    train_ok = df[df["label"].isin(config.KATEGORI)]
    print(f"\nFoto berlabel untuk training (ringan/sedang/berat): {len(train_ok)} dari {train_ok['group'].nunique()} rumah")


if __name__ == "__main__":
    main()
