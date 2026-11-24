"""Aplikasi web TANGGUH.AI (Flask)."""
import uuid
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, send_from_directory, url_for
from werkzeug.utils import secure_filename

from tangguh import config, db, llm, rules
from tangguh.ml import predict

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}
KABUPATEN = "Kabupaten Solok"
FIELDS = ("nama", "nik", "no_kk", "kecamatan", "nagari", "jorong", "koordinat", "deskripsi")

# Label singkat untuk kode temuan pemeriksaan data (tampil sebagai chip di daftar verifikasi)
ISSUE_LABEL = {
    "KOSONG": "Data kosong", "NIK_BUKAN_ANGKA": "NIK bukan angka", "NIK_PANJANG": "NIK salah panjang",
    "NIK_LUAR_WILAYAH": "NIK luar wilayah", "NIK_TGL_LAHIR": "NIK tgl lahir salah", "NIK_URUT_NOL": "NIK urut 0000",
    "KK_FORMAT": "No. KK salah", "GPS_FORMAT": "GPS salah format", "GPS_LUAR_INDONESIA": "GPS luar Indonesia",
    "GPS_LUAR_SUMBAR": "GPS luar Sumbar", "KONSISTENSI_PENANGANAN": "Penanganan janggal",
    "KERUSAKAN_TAK_DIKENAL": "Kategori tak dikenal", "NIK_SAMA_KK": "NIK = No. KK",
    "DUP_NIK": "Duplikasi data (NIK sama)", "DUP_KK": "Duplikasi data (No. KK sama)", "DUP_NAMA": "Duplikasi data (nama mirip)",
    "AI_TIDAK_YAKIN": "AI tidak yakin",
}


STATUS_LABEL = {"menunggu": "Menunggu verifikasi", "terverifikasi": "Terverifikasi", "ditolak": "Ditolak", "draft": "Draft"}
CEK_LABEL = {"lolos": "Data lolos", "perlu_cek": "Perlu dicek", "perlu_perbaikan": "Perlu diperbaiki",
             "belum_diperiksa": "Belum diperiksa"}
DUP_ALASAN = {"DUP_NIK": "NIK yang sama", "DUP_KK": "No. KK yang sama", "DUP_NAMA": "nama dan jorong yang mirip"}


def _dup_refs(report: dict) -> list[str]:
    """ID laporan pembanding yang MASIH aktif ditandai duplikat (level 'warning').
    Setelah admin menandai 'bukan duplikat', level turun jadi 'info' dan tidak dihitung lagi."""
    return [i["ref_id"] for i in report["issues"]
            if i["code"].startswith("DUP_") and i.get("ref_id") and i["level"] == "warning"]


def _ref_map(reports: list[dict]) -> dict:
    """{id laporan lama: [id laporan baru yang menandainya sebagai duplikat]}.
    Supaya laporan 'asli' juga tahu bahwa ia dirujuk laporan lain."""
    m: dict[str, list[str]] = {}
    for r in reports:
        for ref in _dup_refs(r):
            m.setdefault(ref, []).append(r["id"])
    return m


def create_app():
    app = Flask(__name__)
    app.config.update(SECRET_KEY=config.SECRET_KEY, MAX_CONTENT_LENGTH=8 * 1024 * 1024)
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    db.init_db()

    @app.template_filter("kat")
    def kat_label(v):
        return {**config.KATEGORI, "hanyut": "Hanyut / Hilang"}.get((v or "").lower(), v or "-")

    @app.template_filter("isu")
    def isu_label(code):
        return ISSUE_LABEL.get(code, code)

    @app.template_filter("stat")
    def stat_label(v):
        return STATUS_LABEL.get(v, v or "-")

    @app.template_filter("cek")
    def cek_label(v):
        return CEK_LABEL.get(v, v or "-")

    @app.context_processor
    def inject_globals():
        return {"draft_count": db.counts().get("draft", 0)}

    @app.get("/")
    def home():
        return render_template("home.html", c=db.counts(), latest=db.list_reports(limit=4),
                               ai_ready=predict.model_available())

    # ---------------------------------------------------------------- input + draft
    def _save_photo(draft):
        """Kembalikan (nama_file, pesan_error). Foto draft lama dipertahankan bila tidak ada foto baru."""
        foto_name = draft.get("foto") if draft else None
        file = request.files.get("foto")
        if file and file.filename:
            ext = Path(file.filename).suffix.lower()
            if ext not in ALLOWED_EXT:
                return foto_name, "Format foto harus JPG, PNG, atau WebP."
            foto_name = f"{uuid.uuid4().hex}{ext}"
            file.save(config.UPLOAD_DIR / secure_filename(foto_name))
        return foto_name, None

    @app.route("/laporan/baru", methods=["GET", "POST"])
    def new_report():
        did = request.values.get("draft") or request.form.get("draft_id")
        draft = db.get_report(did) if did else None
        if draft and draft["status"] != "draft":
            draft = None
        if request.method == "GET":
            if did and not draft:
                abort(404)
            f = {k: (draft or {}).get(k) or "" for k in FIELDS}
            f.update(draft_id=draft["id"] if draft else "", foto=draft["foto"] if draft else None)
            return render_template("input.html", f=f)

        f = {k: (request.form.get(k) or "").strip() for k in FIELDS}
        ctx = {**f, "draft_id": draft["id"] if draft else "", "foto": draft["foto"] if draft else None}
        aksi = request.form.get("aksi", "kirim")

        if aksi != "draft" and not request.form.get("setuju"):
            flash("Centang pernyataan persetujuan penggunaan data terlebih dahulu.", "error")
            return render_template("input.html", f=ctx), 400
        foto_name, err = _save_photo(draft)
        if err:
            flash(err, "error")
            return render_template("input.html", f=ctx), 400
        ctx["foto"] = foto_name

        # ---- simpan sebagai draft: tanpa validasi, boleh belum lengkap
        if aksi == "draft":
            if not any(f.values()) and not foto_name:
                flash("Isi minimal satu kolom sebelum menyimpan draft.", "error")
                return render_template("input.html", f=ctx), 400
            rec = {**f, "foto": foto_name}
            if draft:
                db.update_report(draft["id"], rec, [], "belum_diperiksa", "draft")
                rid = draft["id"]
            else:
                rid = db.insert_report({**rec, "status": "draft"}, [], "belum_diperiksa")
            flash(f"Draft {rid} tersimpan. Belum dikirim; lanjutkan kapan saja dari menu Draft.", "ok")
            return redirect(url_for("drafts", saved=draft["id"] if draft else "baru"))

        # ---- kirim: 1) pemeriksaan data otomatis  2) klasifikasi foto (CNN)
        rec = {**f, "foto": foto_name, "id": draft["id"] if draft else None}
        required = {**rules.DEFAULT_REQUIRED, "deskripsi": "Deskripsi kerusakan", "foto": "Foto kerusakan"}
        issues = rules.validate_record(rec, existing=db.list_reports(), required=required)
        ai = predict.predict_image(config.UPLOAD_DIR / foto_name) if foto_name else None
        if ai and ai["status"] == "tidak_yakin":
            issues.append(rules.issue("AI_TIDAK_YAKIN", "warning", "foto",
                                      "AI tidak yakin dengan klasifikasi foto ini; periksa manual."))
        rec["kerusakan_ai"] = ai["label"] if ai else None
        rec["ai_confidence"] = ai["confidence"] if ai else None
        rstatus = rules.status_from_issues(issues)
        if draft:
            db.update_report(draft["id"], rec, issues, rstatus, "menunggu")
            rid = draft["id"]
        else:
            rid = db.insert_report(rec, issues, rstatus)

        # notifikasi yang terlihat jelas, bukan hanya di halaman detail
        dups = [i for i in issues if i["code"].startswith("DUP_")]
        if dups:
            alasan = " / ".join(dict.fromkeys(DUP_ALASAN.get(i["code"], "data yang mirip") for i in dups))
            flash(f"⚠ TERDAPAT DUPLIKASI DATA: laporan {rid} memiliki {alasan} dengan "
                  f"{', '.join(i['ref_id'] for i in dups)}. Laporan tetap tersimpan dan menunggu keputusan admin.", "warn")
        n_err = sum(1 for i in issues if i["level"] == "error")
        if n_err:
            flash(f"{n_err} data perlu diperbaiki (lihat hasil pemeriksaan di bawah).", "error")
        return redirect(url_for("detail", rid=rid, saved=draft["id"] if draft else "baru"))

    @app.get("/draft")
    def drafts():
        return render_template("drafts.html", rows=db.list_reports("draft"))

    @app.post("/draft/<rid>/hapus")
    def delete_draft(rid):
        gone = db.delete_draft(rid)
        if not gone:
            abort(404)
        flash(f"Draft {rid} dihapus.", "ok")
        return redirect(url_for("drafts"))

    # ------------------------------------------------------------ detail + verifikasi
    @app.get("/laporan/<rid>")
    def detail(rid):
        r = db.get_report(rid)
        if not r:
            abort(404)
        if r["status"] == "draft":
            return redirect(url_for("new_report", draft=rid))
        refs = _ref_map(db.list_reports())
        dups = [(ref, db.get_report(ref)) for ref in _dup_refs(r)]
        return render_template("detail.html", r=r, dups=[(a, b) for a, b in dups if b],
                               referenced_by=refs.get(rid, []))

    @app.post("/laporan/<rid>/verifikasi")
    def verify(rid):
        r = db.get_report(rid)
        if not r or r["status"] == "draft":
            abort(404)
        kat = request.form.get("kerusakan_final", "")
        status = request.form.get("status", "menunggu")
        if status not in ("menunggu", "terverifikasi", "ditolak"):
            abort(400)
        if status == "terverifikasi" and kat not in ("ringan", "sedang", "berat", "hanyut"):
            flash("Pilih kategori kerusakan final sebelum memverifikasi.", "error")
            return redirect(url_for("detail", rid=rid))
        db.verify_report(rid, kat, request.form.get("penanganan", ""), status, request.form.get("catatan", ""))
        flash(f"Laporan {rid} diperbarui: {status}.", "ok")
        return redirect(url_for("review"))

    @app.post("/laporan/<rid>/tolak-duplikat")
    def reject_duplicate(rid):
        """Keputusan ADMIN (satu klik). Sistem sendiri tidak pernah menolak otomatis."""
        r = db.get_report(rid)
        if not r or r["status"] == "draft":
            abort(404)
        refs = _dup_refs(r)
        if not refs:
            flash("Laporan ini tidak ditandai duplikat.", "error")
            return redirect(url_for("detail", rid=rid))
        db.verify_report(rid, r.get("kerusakan_final") or "", r.get("penanganan") or "", "ditolak",
                         f"Ditolak admin: duplikat dari {', '.join(refs)}.")
        flash(f"Laporan {rid} ditolak sebagai duplikat dari {', '.join(refs)}.", "ok")
        return redirect(url_for("review"))

    @app.post("/laporan/<rid>/bukan-duplikat")
    def not_duplicate(rid):
        """Admin menyatakan temuan duplikat bukan duplikat (mis. laporan perkembangan kerusakan)."""
        r = db.get_report(rid)
        if not r or r["status"] == "draft":
            abort(404)
        refs = _dup_refs(r)
        if not refs:
            flash("Laporan ini tidak ditandai duplikat.", "error")
            return redirect(url_for("detail", rid=rid))
        issues = r["issues"]
        for i in issues:
            if i["code"].startswith("DUP_") and i["level"] == "warning":
                i["level"] = "info"
                i["message"] = "Dikonfirmasi admin: BUKAN duplikat. " + i["message"]
        db.update_issues(rid, issues, rules.status_from_issues(issues),
                         f"Admin: bukan duplikat dari {', '.join(refs)} (laporan lanjutan/berbeda).")
        flash(f"Laporan {rid} ditandai bukan duplikat. Silakan lanjut verifikasi.", "ok")
        return redirect(url_for("detail", rid=rid))

    @app.get("/verifikasi")
    def review():
        st = request.args.get("status") or None
        if st == "draft":
            return redirect(url_for("drafts"))
        allr = db.list_reports()
        refs = _ref_map(allr)
        # perlu perhatian = menunggu dan (ada temuan ATAU dirujuk laporan lain sebagai duplikat)
        need = [r for r in allr if r["status"] == "menunggu" and (r["rules_status"] != "lolos" or r["id"] in refs)]
        rows = need if st == "temuan" else [r for r in allr if not st or r["status"] == st]
        urgent = {r["id"] for r in need}
        dups = {r["id"]: _dup_refs(r) for r in allr if _dup_refs(r)}
        rows = sorted(rows, key=lambda r: 0 if r["id"] in urgent else 1)  # bermasalah dulu (urutan terbaru dipertahankan)
        c = db.counts()
        c["temuan"] = len(need)
        return render_template("review.html", rows=rows, st=st, c=c, refs=refs, urgent=urgent, dups=dups)

    @app.route("/ringkasan", methods=["GET", "POST"])
    def summary():
        facts = llm.build_facts(db.list_reports())
        result = llm.summarize(facts) if request.method == "POST" else None
        return render_template("summary.html", facts=facts, result=result, kab=KABUPATEN)

    @app.get("/panduan")
    def guide():
        return render_template("guide.html")

    @app.get("/foto/<name>")
    def foto(name):
        return send_from_directory(config.UPLOAD_DIR, secure_filename(name))

    return app
