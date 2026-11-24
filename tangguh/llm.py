"""Generative AI: ringkasan situasi bencana dari data TERVERIFIKASI skala nasional.

Prinsip anti-halusinasi & perlindungan privasi:
  1. HANYA laporan dengan status TERVERIFIKASI yang dihitung. Laporan menunggu, ditolak,
     duplikat, atau perlu revisi DILARANG masuk ke ringkasan.
  2. NIK, Nomor KK, dan identitas individu warga DILARANG dimasukkan ke prompt AI.
  3. Semua angka dihitung dari database (build_facts), bukan diperkirakan oleh LLM.
  4. LLM hanya menyusun kalimat dari fakta terhitung (temperature rendah).
  5. Setelah LLM menjawab, setiap angka dicek terhadap fakta. Bila ada angka tidak sah ->
     jawaban diganti ringkasan template otomatis.
  6. Tanpa API key / gagal -> otomatis memakai ringkasan template.
"""
from __future__ import annotations

import json
import re
from collections import Counter

import requests

from . import config

KAT_LABEL = {"ringan": "rusak ringan", "sedang": "rusak sedang", "berat": "rusak berat", "hanyut": "hanyut/hilang"}

SYSTEM_PROMPT = """Kamu adalah asisten penyusun laporan situasi pascabencana tingkat nasional untuk BNPB/BPBD dan kementerian/lembaga terkait di Indonesia.
ATURAN KETAT:
1. Gunakan HANYA angka dan nama wilayah yang ada pada blok DATA TERVERIFIKASI. Jangan menambah, membulatkan, menghitung ulang, atau memperkirakan angka.
2. Jika suatu informasi tidak ada di data, tulis "tidak tersedia di data". Jangan mengarang penyebab, korban jiwa, atau kebutuhan bantuan.
3. Jangan pernah menyebut nama, NIK, atau nomor identitas individu warga (patuhi perlindungan data pribadi).
4. Bahasa Indonesia formal, singkat, dan netral. Jangan memberi keputusan; ini bahan pertimbangan petugas.
5. Format keluaran persis:
RINGKASAN UMUM: (2-3 kalimat)
KONDISI PER WILAYAH: (butir per kecamatan/desa, sebutkan angka dari data)
CATATAN: (1 kalimat bahwa data berasal dari laporan terverifikasi per tanggal pembuatan)
"""


def build_facts(reports: list[dict]) -> dict:
    """Hitung semua angka HANYA dari laporan yang sah dan telah TERVERIFIKASI oleh admin.
    Laporan berstatus ditolak, duplikat, draft, menunggu, atau perlu revisi otomatis disaring keluar."""
    rows = [r for r in reports if r.get("status") == "terverifikasi" and r.get("status") not in ("ditolak", "duplikat", "draft")]
    kat = Counter((r.get("kerusakan_final") or "").strip().lower() for r in rows)
    per_kec, per_desa, pen = Counter(), Counter(), Counter(r.get("penanganan") or "belum ditetapkan" for r in rows)
    per_desa_kat: dict[str, Counter] = {}

    for r in rows:
        kec = r.get("kecamatan") or "-"
        desa = r.get("desa") or r.get("nagari") or "-"
        per_kec[kec] += 1
        key = f"{desa} ({kec})"
        per_desa[key] += 1
        per_desa_kat.setdefault(key, Counter())[(r.get("kerusakan_final") or "").lower()] += 1

    top = per_desa.most_common(1)[0] if per_desa else None
    return {
        "total_terverifikasi": len(rows),
        "per_kategori": {k: kat.get(k, 0) for k in KAT_LABEL},
        "per_kecamatan": dict(per_kec.most_common()),
        "per_desa": {k: {"jumlah": v, **{kk: per_desa_kat[k].get(kk, 0) for kk in KAT_LABEL}} for k, v in per_desa.most_common()},
        # Alias per_nagari untuk backward compatibility
        "per_nagari": {k: {"jumlah": v, **{kk: per_desa_kat[k].get(kk, 0) for kk in KAT_LABEL}} for k, v in per_desa.most_common()},
        "penanganan": dict(pen),
        "wilayah_terbanyak": {"nama": top[0], "jumlah": top[1]} if top else None,
        "items": [
            {"id": r.get("id"), "kecamatan": r.get("kecamatan"), "desa": r.get("desa") or r.get("nagari"),
             "kerusakan": r.get("kerusakan_final")}
            for r in rows
        ],
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
    for nama, d in list(f["per_desa"].items())[:15]:
        det = ", ".join(f"{d[x]} {KAT_LABEL[x]}" for x in KAT_LABEL if d[x])
        lines.append(f"- {nama}: {d['jumlah']} rumah ({det}).")
    pen = ", ".join(f"{v} {k}" for k, v in f["penanganan"].items())
    lines.append(f"PENANGANAN: {pen}.")
    lines.append("CATATAN: Ringkasan ini disusun otomatis dari data laporan terverifikasi dan perlu ditinjau petugas.")
    return "\n".join(lines)


def summarize(facts: dict, caller=None) -> dict:
    """Susun ringkasan dari fakta terverifikasi; fallback ke template bila LLM gagal/tidak valid."""
    fallback = template_summary(facts)
    if not facts["total_terverifikasi"]:
        return {"text": fallback, "source": "template", "note": "Tanpa data terverifikasi."}

    prompt = (
        f"{SYSTEM_PROMPT}\n\nDATA TERVERIFIKASI (FAKTA):\n"
        f"{json.dumps(facts, ensure_ascii=False, indent=2)}\n\n"
        f"Susun ringkasan sesuai format."
    )

    if caller is not None:
        try:
            text = caller(prompt).strip()
        except Exception as e:
            return {"text": fallback, "source": "template", "note": f"Panggilan AI gagal ({type(e).__name__})."}
    elif not config.GEMINI_API_KEY:
        return {"text": fallback, "source": "template", "note": "Tanpa API key atau data kosong."}
    else:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent"
            f"?key={config.GEMINI_API_KEY}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 600},
        }
        try:
            r = requests.post(url, json=payload, timeout=15)
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception as e:
            return {"text": fallback, "source": "template", "note": f"Panggilan AI gagal ({type(e).__name__})."}

    # Anti-halusinasi angka: setiap angka di keluaran LLM harus ada di fakta
    allowed = _numbers_in(facts)
    hallucinated = set(re.findall(r"\b\d+\b", text)) - allowed
    if hallucinated:
        return {
            "text": fallback,
            "source": "template",
            "note": f"AI menyebut angka di luar data ({', '.join(sorted(hallucinated))}); jawaban dibatalkan demi akurasi.",
        }
    return {"text": text, "source": "gemini", "note": "Disusun AI Gemini dari data terverifikasi dan tervalidasi."}
