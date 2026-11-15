"""Generative AI: ringkasan situasi bencana dari data TERVERIFIKASI.

Prinsip anti-halusinasi:
  1. Semua angka dihitung dari database (build_facts), bukan oleh LLM.
  2. LLM hanya menyusun kalimat dari fakta tersebut (temperature rendah).
  3. Setelah LLM menjawab, setiap angka di teks dicek terhadap fakta. Bila ada angka
     yang tidak berasal dari fakta -> jawaban dibuang, diganti ringkasan template.
  4. Tanpa API key / gagal -> otomatis pakai ringkasan template (aplikasi tetap jalan).
LLM gratis: Google Gemini API free tier (kunci di https://aistudio.google.com/apikey).
"""
from __future__ import annotations

import json
import re
from collections import Counter

import requests

from . import config

KAT_LABEL = {"ringan": "rusak ringan", "sedang": "rusak sedang", "berat": "rusak berat", "hanyut": "hanyut/hilang"}

SYSTEM_PROMPT = """Kamu adalah asisten penyusun laporan situasi pascabencana untuk BPBD/dinas terkait di Sumatera Barat.
ATURAN KETAT:
1. Gunakan HANYA angka dan nama wilayah yang ada pada blok DATA TERVERIFIKASI. Jangan menambah, membulatkan, menghitung ulang, atau memperkirakan angka.
2. Jika suatu informasi tidak ada di data, tulis "tidak tersedia di data". Jangan mengarang penyebab, korban jiwa, atau kebutuhan bantuan.
3. Jangan menyebut nama atau NIK individu warga.
4. Bahasa Indonesia formal, singkat, dan netral. Jangan memberi keputusan; ini bahan pertimbangan petugas.
5. Format keluaran persis:
RINGKASAN UMUM: (2-3 kalimat)
KONDISI PER WILAYAH: (butir per kecamatan/nagari, sebutkan angka dari data)
CATATAN: (1 kalimat bahwa data berasal dari laporan terverifikasi per tanggal pembuatan)
"""


def build_facts(reports: list[dict]) -> dict:
    """Hitung semua angka dari laporan terverifikasi."""
    rows = [r for r in reports if r.get("status") == "terverifikasi"]
    kat = Counter((r.get("kerusakan_final") or "").strip().lower() for r in rows)
    per_kec, per_nag, pen = Counter(), Counter(), Counter(r.get("penanganan") or "belum ditetapkan" for r in rows)
    per_nag_kat: dict[str, Counter] = {}
    for r in rows:
        per_kec[r.get("kecamatan") or "-"] += 1
        key = f"{r.get('nagari') or '-'} ({r.get('kecamatan') or '-'})"
        per_nag[key] += 1
        per_nag_kat.setdefault(key, Counter())[(r.get("kerusakan_final") or "").lower()] += 1
    top = per_nag.most_common(1)[0] if per_nag else None
    return {
        "total_terverifikasi": len(rows),
        "per_kategori": {k: kat.get(k, 0) for k in KAT_LABEL},
        "per_kecamatan": dict(per_kec.most_common()),
        "per_nagari": {k: {"jumlah": v, **{kk: per_nag_kat[k].get(kk, 0) for kk in KAT_LABEL}} for k, v in per_nag.most_common()},
        "penanganan": dict(pen),
        "wilayah_terbanyak": {"nama": top[0], "jumlah": top[1]} if top else None,
    }


def _numbers_in(obj) -> set[str]:
    return {str(n) for n in re.findall(r"\d+", json.dumps(obj, ensure_ascii=False))}


def template_summary(f: dict) -> str:
    if not f["total_terverifikasi"]:
        return "Belum ada laporan terverifikasi. Ringkasan tidak dapat disusun."
    k = f["per_kategori"]
    kat = ", ".join(f"{v} {KAT_LABEL[x]}" for x, v in k.items() if v)
    lines = [
        f"RINGKASAN UMUM: Berdasarkan laporan yang telah diverifikasi, terdapat {f['total_terverifikasi']} rumah terdampak "
        f"({kat}).",
    ]
    if f["wilayah_terbanyak"]:
        w = f["wilayah_terbanyak"]
        lines[0] += f" Wilayah dengan laporan terbanyak adalah {w['nama']} dengan {w['jumlah']} laporan."
    lines.append("KONDISI PER WILAYAH:")
    for nama, d in list(f["per_nagari"].items())[:15]:
        det = ", ".join(f"{d[x]} {KAT_LABEL[x]}" for x in KAT_LABEL if d[x])
        lines.append(f"- {nama}: {d['jumlah']} rumah ({det}).")
    pen = ", ".join(f"{v} {k}" for k, v in f["penanganan"].items())
    lines.append(f"PENANGANAN: {pen}.")
    lines.append("CATATAN: Ringkasan ini disusun otomatis dari data laporan terverifikasi dan perlu ditinjau petugas.")
    return "\n".join(lines)


def _call_gemini(prompt: str, timeout: int = 40) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 900},
    }
    r = requests.post(url, json=body, headers={"x-goog-api-key": config.GEMINI_API_KEY}, timeout=timeout)
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()


def summarize(facts: dict, use_llm: bool = True, caller=None) -> dict:
    """Kembalikan {"text", "source": "gemini"|"template", "note"}.
    `caller` (opsional) menggantikan pemanggilan Gemini, berguna untuk tes."""
    fallback = {"text": template_summary(facts), "source": "template", "note": ""}
    if not use_llm or not facts["total_terverifikasi"]:
        return fallback
    if caller is None and not config.GEMINI_API_KEY:
        fallback["note"] = "GEMINI_API_KEY belum diisi; memakai ringkasan template."
        return fallback
    prompt = "DATA TERVERIFIKASI (JSON):\n" + json.dumps(facts, ensure_ascii=False, indent=1) + \
             "\n\nSusun laporan situasi sesuai format."
    try:
        text = (caller or _call_gemini)(prompt)
    except Exception as e:  # jaringan, kuota, dll.
        fallback["note"] = f"LLM gagal ({type(e).__name__}); memakai ringkasan template."
        return fallback
    allowed = _numbers_in(facts)
    bad = sorted(n for n in set(re.findall(r"\d+", text)) if n not in allowed)
    if bad:
        fallback["note"] = f"Jawaban LLM dibuang karena memuat angka di luar data ({', '.join(bad[:5])}); memakai template."
        return fallback
    return {"text": text, "source": "gemini", "note": "Angka telah dicek terhadap database."}
