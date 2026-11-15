"""Aplikasi web TANGGUH.AI (Flask)."""
import uuid
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, send_from_directory, url_for
from werkzeug.utils import secure_filename

from tangguh import config, db, llm, rules
from tangguh.ml import predict

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}
KABUPATEN = "Kabupaten Solok"


def create_app():
    app = Flask(__name__)
    app.config.update(SECRET_KEY=config.SECRET_KEY, MAX_CONTENT_LENGTH=8 * 1024 * 1024)
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    db.init_db()

    @app.template_filter("kat")
    def kat_label(v):
        return {**config.KATEGORI, "hanyut": "Hanyut / Hilang"}.get((v or "").lower(), v or "-")

    @app.get("/")
    def home():
        return render_template("home.html", c=db.counts(), latest=db.list_reports(limit=4),
                               ai_ready=predict.model_available())

    @app.route("/laporan/baru", methods=["GET", "POST"])
    def new_report():
        if request.method == "GET":
            return render_template("input.html", f={})
        f = {k: (request.form.get(k) or "").strip() for k in
             ("nama", "nik", "no_kk", "kecamatan", "nagari", "jorong", "koordinat", "deskripsi")}
        if not request.form.get("setuju"):
            flash("Centang pernyataan persetujuan penggunaan data terlebih dahulu.", "error")
            return render_template("input.html", f=f), 400

        foto_name, ai = None, None
        file = request.files.get("foto")
        if file and file.filename:
            ext = Path(file.filename).suffix.lower()
            if ext not in ALLOWED_EXT:
                flash("Format foto harus JPG, PNG, atau WebP.", "error")
                return render_template("input.html", f=f), 400
            foto_name = f"{uuid.uuid4().hex}{ext}"
            file.save(config.UPLOAD_DIR / secure_filename(foto_name))

        # 1) Rule-Based: kelengkapan, format, konsistensi, duplikasi
        rec = {**f, "foto": foto_name}
        required = {**rules.DEFAULT_REQUIRED, "deskripsi": "Deskripsi kerusakan", "foto": "Foto kerusakan"}
        issues = rules.validate_record(rec, existing=db.list_reports(), required=required)

        # 2) Predictive: CNN (rekomendasi awal)
        if foto_name:
            ai = predict.predict_image(config.UPLOAD_DIR / foto_name)
            if ai["status"] == "tidak_yakin":
                issues.append(rules.issue("AI_TIDAK_YAKIN", "warning", "foto",
                                          "AI tidak yakin dengan klasifikasi foto ini; periksa manual."))
        rec["kerusakan_ai"] = ai["label"] if ai else None
        rec["ai_confidence"] = ai["confidence"] if ai else None
        rid = db.insert_report(rec, issues, rules.status_from_issues(issues))
        return redirect(url_for("detail", rid=rid))

    @app.get("/laporan/<rid>")
    def detail(rid):
        r = db.get_report(rid)
        if not r:
            abort(404)
        return render_template("detail.html", r=r)

    @app.post("/laporan/<rid>/verifikasi")
    def verify(rid):
        if not db.get_report(rid):
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

    @app.get("/verifikasi")
    def review():
        st = request.args.get("status") or None
        return render_template("review.html", rows=db.list_reports(st), st=st, c=db.counts())

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
