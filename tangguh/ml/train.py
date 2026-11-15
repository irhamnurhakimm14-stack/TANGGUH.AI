"""Training MobileNetV2 (transfer learning) untuk klasifikasi kerusakan: ringan/sedang/berat.

Jalankan (Colab atau laptop):
  pip install -r requirements-ml.txt
  python -m tangguh.ml.train --epochs-head 15 --epochs-ft 15

Langkah:
  1. Baca data/dataset/manifest.csv, ambil hanya kelas ringan/sedang/berat.
  2. Split PER RUMAH (group) -> train/val/test, stratified, agar tidak bocor.
  3. Tahap 1: backbone MobileNetV2 dibekukan, latih kepala klasifikasi.
  4. Tahap 2: buka ~30 layer terakhir, fine-tune dengan learning rate kecil.
  5. Evaluasi di test set, simpan model + classes.json + metrics.json + confusion matrix.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.utils.class_weight import compute_class_weight

from .. import config

CLASSES = ["ringan", "sedang", "berat"]  # urutan = indeks output model
SEED = 42


def split_by_group(df: pd.DataFrame, seed: int = SEED):
    """~70% train, 15% val, 15% test; satu rumah tidak pernah terpecah."""
    y, g = df["label"].values, df["group"].values
    outer = StratifiedGroupKFold(n_splits=7, shuffle=True, random_state=seed)  # ~1/7 = 14% test
    trainval_idx, test_idx = next(outer.split(df, y, g))
    tv = df.iloc[trainval_idx]
    inner = StratifiedGroupKFold(n_splits=6, shuffle=True, random_state=seed)  # ~1/6 val
    tr_i, va_i = next(inner.split(tv, tv["label"].values, tv["group"].values))
    return tv.iloc[tr_i], tv.iloc[va_i], df.iloc[test_idx]


def build_datasets(tf, root: Path, train, val, test, batch: int):
    cls_idx = {c: i for i, c in enumerate(CLASSES)}
    size = (config.IMG_SIZE, config.IMG_SIZE)

    def make(d, training):
        paths = [str(root / f) for f in d["file"]]
        labels = [cls_idx[l] for l in d["label"]]
        ds = tf.data.Dataset.from_tensor_slices((paths, labels))

        def load(p, y):
            img = tf.io.decode_image(tf.io.read_file(p), channels=3, expand_animations=False)
            img = tf.image.resize(img, size)
            img.set_shape((*size, 3))
            return img, y

        ds = ds.map(load, num_parallel_calls=tf.data.AUTOTUNE)
        if training:
            ds = ds.shuffle(len(paths), seed=SEED)
        return ds.batch(batch).prefetch(tf.data.AUTOTUNE)

    return make(train, True), make(val, False), make(test, False)


def build_model(tf):
    from tensorflow.keras import layers

    aug = tf.keras.Sequential(
        [layers.RandomFlip("horizontal"), layers.RandomRotation(0.06), layers.RandomZoom(0.15),
         layers.RandomContrast(0.15), layers.RandomBrightness(0.15)],
        name="augmentasi",
    )
    base = tf.keras.applications.MobileNetV2(
        input_shape=(config.IMG_SIZE, config.IMG_SIZE, 3), include_top=False, weights="imagenet"
    )
    base.trainable = False
    inp = layers.Input((config.IMG_SIZE, config.IMG_SIZE, 3))
    x = aug(inp)  # augmentasi hanya aktif saat training
    x = layers.Rescaling(1 / 127.5, offset=-1)(x)  # = preprocess_input MobileNetV2 -> [-1, 1]
    x = base(x, training=False)
    x = layers.GlobalAveragePooling2D()(x)
    x = layers.Dropout(0.3)(x)
    out = layers.Dense(len(CLASSES), activation="softmax")(x)
    return tf.keras.Model(inp, out), base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(config.DATASET_DIR))
    ap.add_argument("--epochs-head", type=int, default=15)
    ap.add_argument("--epochs-ft", type=int, default=15)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--unfreeze", type=int, default=30, help="jumlah layer terakhir yang di-fine-tune")
    ap.add_argument("--out", default=str(config.MODEL_DIR))
    a = ap.parse_args()

    import tensorflow as tf

    tf.keras.utils.set_random_seed(SEED)
    root = Path(a.dataset)
    df = pd.read_csv(root / "manifest.csv")
    df = df[df["label"].isin(CLASSES)].reset_index(drop=True)
    print("Data berlabel:\n", df["label"].value_counts().to_string())

    train, val, test = split_by_group(df)
    for n, d in (("train", train), ("val", val), ("test", test)):
        print(f"{n:5s}: {len(d):4d} foto, {d['group'].nunique():4d} rumah |", d["label"].value_counts().to_dict())
    assert not (set(train["group"]) & set(test["group"])), "kebocoran data train/test!"

    ds_tr, ds_va, ds_te = build_datasets(tf, root, train, val, test, a.batch)
    cw = compute_class_weight("balanced", classes=np.array(CLASSES), y=train["label"].values)
    class_weight = {i: float(w) for i, w in enumerate(cw)}
    print("class_weight:", class_weight)

    model, base = build_model(tf)
    cb = [tf.keras.callbacks.EarlyStopping(patience=5, restore_best_weights=True, monitor="val_loss")]

    model.compile(optimizer=tf.keras.optimizers.Adam(1e-3), loss="sparse_categorical_crossentropy", metrics=["accuracy"])
    model.fit(ds_tr, validation_data=ds_va, epochs=a.epochs_head, class_weight=class_weight, callbacks=cb)

    base.trainable = True
    for layer in base.layers[: -a.unfreeze]:
        layer.trainable = False
    for layer in base.layers:  # BatchNorm tetap beku saat fine-tune
        if isinstance(layer, tf.keras.layers.BatchNormalization):
            layer.trainable = False
    model.compile(optimizer=tf.keras.optimizers.Adam(1e-5), loss="sparse_categorical_crossentropy", metrics=["accuracy"])
    model.fit(ds_tr, validation_data=ds_va, epochs=a.epochs_ft, class_weight=class_weight, callbacks=cb)

    # --- evaluasi di test set (data yang belum pernah dilihat model)
    y_true = np.concatenate([y.numpy() for _, y in ds_te])
    y_pred = np.argmax(model.predict(ds_te), axis=1)
    rep = classification_report(y_true, y_pred, target_names=CLASSES, output_dict=True, zero_division=0)
    cm = confusion_matrix(y_true, y_pred, labels=range(len(CLASSES)))
    print(classification_report(y_true, y_pred, target_names=CLASSES, zero_division=0))
    print("Confusion matrix (baris=benar, kolom=prediksi):\n", cm)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    model.save(out / "damage_mobilenetv2.keras")
    (out / "classes.json").write_text(json.dumps(CLASSES))
    (out / "metrics.json").write_text(json.dumps(
        {"report": rep, "confusion_matrix": cm.tolist(), "n_train": len(train), "n_val": len(val), "n_test": len(test)},
        indent=2))
    print(f"\nModel tersimpan di {out}/damage_mobilenetv2.keras")


if __name__ == "__main__":
    main()
