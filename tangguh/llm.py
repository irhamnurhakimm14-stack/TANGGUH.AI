"""Verified aggregates -> Gemini JSON plan -> deterministic Indonesian summary."""
import json
import os
from collections import Counter
import requests
from . import config

KAT_LABEL={"ringan":"rusak ringan","sedang":"rusak sedang","berat":"rusak berat","hanyut":"hanyut/hilang"}

def build_facts(reports):
    rows=[r for r in reports if r.get("status")=="terverifikasi" and not r.get("is_archived") and not r.get("duplikat_dari")]
    cat, kec, desa, pen=Counter(),Counter(),{},Counter()
    for r in rows:
        k=str(r.get("kerusakan_final") or "").strip().lower()
        k=k if k in KAT_LABEL else "belum_ditetapkan"
        cat[k]+=1
        a=str(r.get("kecamatan") or "-"); b=str(r.get("desa") or r.get("nagari") or "-")
        scope=", ".join(str(r.get(x) or "-") for x in ("kabupaten","provinsi"))
        kec[f"{a}, {scope}"]+=1
        name=f"{b} ({a}, {scope})"
        d=desa.setdefault(name,{"jumlah":0,**dict.fromkeys(KAT_LABEL,0),"belum_ditetapkan":0})
        d["jumlah"]+=1; d[k]+=1
        pen[str(r.get("penanganan") or "belum ditetapkan")]+=1
    desa=dict(sorted(desa.items(),key=lambda x:(-x[1]["jumlah"],x[0])))
    top=next(iter(desa),None)
    return {"total_terverifikasi":len(rows),"per_kategori":{k:cat[k] for k in KAT_LABEL},"belum_ditetapkan":cat["belum_ditetapkan"],"per_kecamatan":dict(kec),"per_desa":desa,"per_nagari":desa,"penanganan":dict(pen),"wilayah_terbanyak":{"nama":top,"jumlah":desa[top]["jumlah"]} if top else None}

def _category_text(d):
    parts=[f"{d.get(k,0)} {label}" for k,label in KAT_LABEL.items() if d.get(k,0)]
    if d.get("belum_ditetapkan"): parts.append(f"{d['belum_ditetapkan']} kategori belum ditetapkan")
    return ", ".join(parts) or "tidak ada kategori tercatat"

def template_summary(f,order=None):
    if not f["total_terverifikasi"]: return "Belum ada laporan terverifikasi. Ringkasan tidak dapat disusun."
    names=list(f["per_desa"])[:15]
    if order is None: order=list(range(len(names)))
    cats={**f["per_kategori"],"belum_ditetapkan":f.get("belum_ditetapkan",0)}
    lines=[f"RINGKASAN UMUM: Terdapat {f['total_terverifikasi']} rumah dalam laporan terverifikasi ({_category_text(cats)})."]
    if f.get("wilayah_terbanyak"):
        w=f["wilayah_terbanyak"]; lines.append(f"Wilayah dengan laporan terbanyak: {w['nama']} ({w['jumlah']} laporan).")
    lines.append("KONDISI PER WILAYAH:")
    for i in order:
        name=names[i]; d=f["per_desa"][name]; lines.append(f"- {name}: {d['jumlah']} rumah ({_category_text(d)}).")
    if len(f["per_desa"])>len(names): lines.append(f"Ditampilkan {len(names)} dari {len(f['per_desa'])} wilayah.")
    if f["penanganan"]: lines.append("PENANGANAN: "+", ".join(f"{v} {k}" for k,v in f["penanganan"].items())+".")
    lines.append("CATATAN: Hanya data terverifikasi aktif. Ringkasan wajib ditinjau petugas sebelum penggunaan resmi.")
    return "\n".join(lines)

def _prompt(f):
    data=[{"id":i,"jumlah":d["jumlah"],**{k:d.get(k,0) for k in KAT_LABEL}} for i,d in enumerate(list(f["per_desa"].values())[:15])]
    return ('Urutkan ID wilayah untuk tinjauan petugas berdasarkan jumlah berat, sedang, dan total. Kembalikan hanya JSON {"order":[...]}, setiap ID tepat sekali. DATA: '+json.dumps(data)),len(data)

def _parse_plan(text,n):
    def unique(pairs):
        d={}
        for k,v in pairs:
            if k in d: raise ValueError("duplicate key")
            d[k]=v
        return d
    if not isinstance(text,str) or len(text)>10000: raise ValueError("invalid size")
    x=json.loads(text,object_pairs_hook=unique)
    if type(x) is not dict or set(x)!={"order"} or type(x["order"]) is not list: raise ValueError("invalid schema")
    order=x["order"]
    if any(type(i) is not int for i in order) or sorted(order)!=list(range(n)): raise ValueError("invalid ids")
    return order

def summarize(facts,caller=None,oidc_token=None):
    fallback=template_summary(facts)
    def fail(note): return {"text":fallback,"source":"template","note":note}
    if not facts["total_terverifikasi"]: return fail("Tanpa data terverifikasi.")
    prompt,n=_prompt(facts)
    try:
        if caller is not None: raw=caller(prompt)
        elif not config.GEMINI_API_KEY:
            token=(oidc_token or os.getenv("VERCEL_OIDC_TOKEN")) if os.getenv("TANGGUH_GATEWAY")=="1" else None
            if not token: return fail("API key belum tersedia; ringkasan template digunakan.")
            r=requests.post("https://ai-gateway.vercel.sh/v1/chat/completions",headers={"Authorization":"Bearer "+token},json={"model":"google/gemini-3.1-flash-lite","messages":[{"role":"user","content":prompt}],"temperature":0,"max_tokens":512,"response_format":{"type":"json_object"}},timeout=15)
            r.raise_for_status(); raw=r.json()["choices"][0]["message"]["content"]
        else:
            url=f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent"
            r=requests.post(url,headers={"x-goog-api-key":config.GEMINI_API_KEY},json={"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"temperature":0,"responseMimeType":"application/json","maxOutputTokens":512}},timeout=15)
            r.raise_for_status(); raw=r.json()["candidates"][0]["content"]["parts"][0]["text"]
        order=_parse_plan(raw,n)
    except requests.HTTPError as e:
        status=e.response.status_code if e.response is not None else "unknown"
        # Report status only; never expose upstream response, auth header, token or prompt.
        return fail(f"Gemini API HTTP {status}; ringkasan template digunakan.")
    except Exception as e: return fail(f"Rencana Gemini gagal atau tidak valid ({type(e).__name__}); ringkasan template digunakan.")
    return {"text":template_summary(facts,order),"source":"gemini","note":"Gemini menyusun urutan wilayah; fakta dirender dari database. Wajib tinjauan petugas."}
