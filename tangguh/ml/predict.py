"""Prediksi tingkat kerusakan dari satu foto menggunakan model CLIP + Logistic Regression.

Hasil = REKOMENDASI AWAL (2 kelas: 'ringan' vs 'sedang_berat'), bukan keputusan resmi.
Keputusan akhir spesifik (Ringan, Sedang, Berat, atau Hanyut/Hilang) diverifikasi oleh Petugas/Admin.
"""
from __future__ import annotations

import json
import os
import warnings
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from .. import config

warnings.filterwarnings("ignore")

CONFIDENCE_MIN = 0.60  # ambang batas keyakinan (sesuai konfigurasi training)


@lru_cache(maxsize=1)
def _load():
    """Memuat model clip_lr.joblib dan arsitektur CLIP ViT-B/32 sekali saat startup."""
    model_path = getattr(config, "CLIP_MODEL_PATH", config.MODEL_DIR / "clip_lr.joblib")
    if not model_path.exists():
        alt_path = config.ROOT / "extracted_model" / "clip_lr.joblib"
        if alt_path.exists():
            model_path = alt_path
        else:
            return None

    try:
        import joblib
        import torch
        from transformers import CLIPModel, CLIPProcessor

        bundle = joblib.load(model_path)
        clf = bundle["pipeline"]
        classes = list(bundle["classes"])
        clip_name = bundle.get("clip_name", "openai/clip-vit-base-patch32")
        conf_min = float(bundle.get("confidence_min", CONFIDENCE_MIN))

        dev = "cuda" if torch.cuda.is_available() else "cpu"
        proc = CLIPProcessor.from_pretrained(clip_name)
        clip = CLIPModel.from_pretrained(clip_name).to(dev).eval()

        return clf, classes, clip, proc, dev, conf_min
    except Exception as e:
        return None


def model_available() -> bool:
    return _load() is not None


def get_model_info() -> dict:
    loaded = _load()
    if loaded is None:
        return {"status": "unavailable", "classes": [], "count": 0}
    clf, classes, clip, proc, dev, conf_min = loaded
    metrics_path = getattr(config, "CLIP_METRICS_PATH", config.MODEL_DIR / "metrics.json")
    metrics = {}
    if metrics_path.exists():
        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "status": "ready",
        "name": "CLIP ViT-B/32 + Logistic Regression",
        "architecture": "OpenAI CLIP ViT-B/32 (Vision Transformer) + Scikit-Learn Pipeline (StandardScaler + LogisticRegression)",
        "classes": classes,
        "class_count": len(classes),
        "confidence_min": conf_min,
        "device": dev,
        "metrics": metrics,
    }


def predict_image(path) -> dict:
    if not path or not Path(path).exists():
        return {
            "status": "error",
            "label": None,
            "confidence": None,
            "probs": {},
            "classes": [],
            "note": "File foto tidak ditemukan.",
        }

    # Validasi integritas gambar
    try:
        img = Image.open(path)
        img.verify()
        img = Image.open(path).convert("RGB")
    except Exception:
        return {
            "status": "error",
            "label": None,
            "confidence": None,
            "probs": {},
            "classes": [],
            "note": "File foto tidak valid atau rusak.",
        }

    loaded = _load()
    if loaded is None:
        return {
            "status": "model_tidak_tersedia",
            "label": None,
            "confidence": None,
            "probs": {},
            "classes": [],
            "note": "Model AI belum terpasang. Klasifikasi dilakukan manual oleh petugas.",
        }

    clf, classes, clip, proc, dev, conf_min = loaded
    try:
        import torch

        with torch.no_grad():
            px = proc(images=[img], return_tensors="pt")["pixel_values"].to(dev)
            pool = clip.vision_model(pixel_values=px).pooler_output.cpu().numpy()

        probs = clf.predict_proba(pool)[0]
        i = int(np.argmax(probs))
        conf = float(probs[i])
        label = classes[i]

        status = "ok" if conf >= conf_min else "tidak_yakin"
        note = (
            "Rekomendasi AI (2 kelas: Ringan vs Sedang/Berat). Verifikator menetapkan keputusan akhir."
            if status == "ok"
            else f"Keyakinan model rendah ({round(conf * 100)}% < {round(conf_min * 100)}%). Perlu pemeriksaan khusus oleh petugas."
        )

        return {
            "status": status,
            "label": label,
            "confidence": conf,
            "probs": {c: float(v) for c, v in zip(classes, probs)},
            "classes": classes,
            "note": note,
        }
    except Exception as e:
        return {
            "status": "error",
            "label": None,
            "confidence": None,
            "probs": {},
            "classes": [],
            "note": f"Gagal memproses prediksi gambar ({type(e).__name__}).",
        }
