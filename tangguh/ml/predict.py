"""Prediksi tingkat kerusakan dari satu foto. Hasil = REKOMENDASI AWAL, bukan keputusan resmi.

Jika model belum dilatih / TensorFlow belum terpasang, `predict_image` mengembalikan
status 'model_tidak_tersedia' sehingga aplikasi tetap berjalan (mode demo).
"""
from __future__ import annotations

import json
from functools import lru_cache

import numpy as np
from PIL import Image

from .. import config

CONFIDENCE_MIN = 0.55  # di bawah ini -> tandai "tidak yakin", wajib diperiksa petugas


@lru_cache(maxsize=1)
def _load():
    if not (config.MODEL_PATH.exists() and config.CLASSES_PATH.exists()):
        return None
    try:
        import tensorflow as tf

        model = tf.keras.models.load_model(config.MODEL_PATH)
        classes = json.loads(config.CLASSES_PATH.read_text())
        return model, classes
    except Exception:
        return None


def model_available() -> bool:
    return _load() is not None


def predict_image(path) -> dict:
    loaded = _load()
    if loaded is None:
        return {"status": "model_tidak_tersedia", "label": None, "confidence": None, "probs": {},
                "note": "Model CNN belum dilatih/dipasang. Klasifikasi dilakukan manual oleh petugas."}
    model, classes = loaded
    img = Image.open(path).convert("RGB").resize((config.IMG_SIZE, config.IMG_SIZE))
    x = np.asarray(img, dtype="float32")[None]  # Rescaling sudah ada di dalam model
    p = model.predict(x, verbose=0)[0]
    i = int(np.argmax(p))
    conf = float(p[i])
    return {
        "status": "ok" if conf >= CONFIDENCE_MIN else "tidak_yakin",
        "label": classes[i],
        "confidence": conf,
        "probs": {c: float(v) for c, v in zip(classes, p)},
        "note": "Rekomendasi AI. Keputusan akhir oleh petugas/admin.",
    }
