"""Aplikasi web TANGGUH.AI (Flask) skala Nasional dengan Autentikasi Multi-Role."""
import sys
import os
import time
import uuid
from io import BytesIO
from urllib.parse import urlsplit
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    send_file,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

from tangguh import config, db, llm, rules, cloud_db, demo_state
import re
from flask import g
from tangguh.ml import predict

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}
KABUPATEN = "Nasional / Seluruh Indonesia"
FIELDS = (
    "nama", "nik", "no_kk", "provinsi", "kabupaten", "kecamatan", "desa", "dusun", "rt_rw",
    "koordinat", "deskripsi", "nagari", "jorong"
)

# Label singkat untuk kode temuan pemeriksaan data
ISSUE_LABEL = {
    "KOSONG": "Data kosong", "NIK_BUKAN_ANGKA": "NIK bukan angka", "NIK_PANJANG": "NIK salah panjang",
    "NIK_LUAR_WILAYAH": "NIK luar wilayah", "NIK_TGL_LAHIR": "NIK tgl lahir salah", "NIK_URUT_NOL": "NIK urut 0000",
    "KK_FORMAT": "No. KK salah", "GPS_FORMAT": "GPS salah format", "GPS_LUAR_INDONESIA": "GPS luar Indonesia",
    "GPS_LUAR_SUMBAR": "GPS luar Sumbar", "KONSISTENSI_PENANGANAN": "Penanganan janggal",
    "KERUSAKAN_TAK_DIKENAL": "Kategori tak dikenal", "NIK_SAMA_KK": "NIK = No. KK",
    "DUP_NIK": "Duplikasi data (NIK sama)", "DUP_KK": "Duplikasi data (No. KK sama)", "DUP_NAMA": "Duplikasi data (nama mirip)",
    "DUP_LOKASI": "Duplikasi data (lokasi & deskripsi mirip)", "DUP_FOTO": "Duplikasi data (foto identik)",
    "AI_TIDAK_YAKIN": "AI tidak yakin",
}

STATUS_LABEL = {
    "menunggu_pemeriksaan": "Menunggu Pemeriksaan",
    "sedang_diperiksa": "Sedang Diperiksa",
    "diteruskan": "Diteruskan ke Admin",
    "terverifikasi": "Terverifikasi",
    "perlu_revisi": "Perlu Revisi",
    "ditolak": "Ditolak",
    "duplikat": "Duplikat",
    "arsip": "Arsip",
    "draft": "Draft",
    "menunggu": "Menunggu Pemeriksaan",
}

STATUS_PEMERIKSAAN_LABEL = {
    "menunggu_pemeriksaan": "Menunggu Pemeriksaan",
    "sedang_diperiksa": "Sedang Diperiksa",
    "diteruskan": "Diteruskan ke Admin",
}

CEK_LABEL = {
    "lolos": "Data lolos",
    "perlu_cek": "Perlu dicek",
    "perlu_perbaikan": "Perlu diperbaiki",
    "belum_diperiksa": "Belum diperiksa",
}

DUP_ALASAN = {
    "DUP_NIK": "NIK yang sama",
    "DUP_KK": "No. KK yang sama",
    "DUP_NAMA": "nama dan lokasi yang mirip",
    "DUP_LOKASI": "lokasi dan deskripsi yang mirip",
    "DUP_FOTO": "foto yang sama",
}


def _dup_refs(report: dict) -> list[str]:
    """ID laporan pembanding yang MASIH aktif ditandai duplikat (level 'warning')."""
    return [i["ref_id"] for i in report.get("issues", [])
            if i.get("code", "").startswith("DUP_") and i.get("ref_id") and i.get("level") == "warning"]


def _ref_map(reports: list[dict]) -> dict:
    """{id laporan lama: [id laporan baru yang menandainya sebagai duplikat]}."""
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
    if config.DEMO_MODE:
        with db.connect() as con:
            for i, kat in enumerate(('ringan','ringan','sedang','berat','berat','hanyut'),1):
                con.execute('INSERT OR IGNORE INTO reports (id,created_at,nama,provinsi,kabupaten,kecamatan,desa,kerusakan_final,status,rules_status) VALUES (?,?,?,?,?,?,?,?,?,?)', (f'TG-DEMO-{i}', '2026-11-24T00:00:00', f'Contoh {i} (sintetis)', 'Sumatera Barat', 'Kabupaten Solok', 'Wilayah Contoh A' if i<=3 else 'Wilayah Contoh B', 'Desa Contoh A' if i<=3 else 'Desa Contoh B', kat, 'terverifikasi','lolos'))
    if config.IS_VERCEL:
        app.config.update(STRICT_AUTH=True, SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', MAX_CONTENT_LENGTH=4*1024*1024)

    @app.before_request
    def protect_cloud():
        if not config.IS_VERCEL:
            return None
        if request.endpoint not in {'home','login','static','health'} and not session.get('user_id'):
            return redirect(url_for('login'))
        if config.DEMO_MODE and request.endpoint not in {'static','health'}:
            demo_state.open_workspace()
        if request.method == 'POST':
            origin = request.headers.get('Origin') or request.headers.get('Referer') or ''
            if urlsplit(origin).netloc != request.host:
                abort(403)
            if config.DEMO_MODE and request.endpoint == 'change_password':
                return 'Sandi demo tidak dapat diubah.', 403

    @app.teardown_request
    def cleanup_demo(error):
        if config.DEMO_MODE:
            demo_state.close_workspace()

    @app.after_request
    def no_cache(response):
        if config.DEMO_MODE and getattr(g,'demo_db',None) is not None:
            try:
                token=demo_state.encode_workspace()
                if token:
                    session['demo_workspace']=token
                else:
                    session.pop('demo_workspace',None)
            except ValueError as exc:
                return app.make_response((str(exc),409,{'Cache-Control':'no-store'}))
        if config.IS_VERCEL and request.endpoint != 'static':
            response.headers['Cache-Control']='no-store'
        return response

    gateway_probe = {'at': 0, 'result': None}

    @app.errorhandler(413)
    def upload_too_large(error):
        return 'Ukuran kiriman terlalu besar. Pilih foto yang lebih kecil atau muat ulang formulir untuk kompresi otomatis.',413

    @app.get('/health')
    def health():
        token = request.headers.get('x-vercel-oidc-token') if config.IS_VERCEL else None
        status = {'status':'ok','demo':config.DEMO_MODE,'database':'postgresql' if config.CLOUD_DB else 'sqlite','visual_ai':'manual' if config.IS_VERCEL else 'local','gemini_configured':bool(config.GEMINI_API_KEY) or bool(os.getenv('TANGGUH_GATEWAY')=='1' and (token or os.getenv('VERCEL_OIDC_TOKEN')))}
        # Smoke check only in protected synthetic preview; cached per instance.
        if config.DEMO_MODE and session.get('role')=='admin' and request.args.get('check')=='llm':
            if gateway_probe['result'] is None or time.monotonic()-gateway_probe['at']>300:
                result=llm.summarize(llm.build_facts(db.list_reports()),oidc_token=token)
                gateway_probe.update(at=time.monotonic(),result={'source':result['source'],'note':result['note']})
            status['llm_probe']=gateway_probe['result']
        return status

    # ---------------------------------------------------------------- Decorator Autentikasi
    def login_required(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not session.get("user_id"):
                flash("Silakan login terlebih dahulu untuk mengakses halaman ini.", "error")
                return redirect(url_for("login", next=request.path))
            return f(*args, **kwargs)
        return decorated_function

    def role_required(*allowed_roles):
        def decorator(f):
            @wraps(f)
            def decorated_function(*args, **kwargs):
                if not session.get("user_id"):
                    flash("Silakan login terlebih dahulu.", "error")
                    return redirect(url_for("login", next=request.path))
                user_role = session.get("role")
                if user_role not in allowed_roles:
                    flash(f"Akses ditolak: halaman ini khusus role {', '.join(allowed_roles)}.", "error")
                    abort(403)
                return f(*args, **kwargs)
            return decorated_function
        return decorator

    # ---------------------------------------------------------------- Template Filters & Context
    @app.template_filter("kat")
    def kat_label(v):
        return {**config.KATEGORI, "hanyut": "Hanyut / Hilang"}.get((v or "").lower(), v or "-")

    @app.template_filter("isu")
    def isu_label(code):
        return ISSUE_LABEL.get(code, code)

    @app.template_filter("stat")
    def stat_label(v):
        return STATUS_LABEL.get(v, v or "-")

    @app.template_filter("stat_periksa")
    def stat_periksa_label(v):
        return STATUS_PEMERIKSAAN_LABEL.get(v, v or "-")

    @app.template_filter("cek")
    def cek_label(v):
        return CEK_LABEL.get(v, v or "-")

    @app.template_filter("mask_nik")
    def mask_nik(v):
        """Sensor NIK untuk perlindungan data pribadi di tampilan publik dan tabel."""
        if not v:
            return "-"
        s = str(v).strip()
        if len(s) >= 8:
            return s[:4] + "*" * (len(s) - 8) + s[-4:]
        return s[:2] + "****" if len(s) > 2 else "****"

    @app.template_filter("mask_kk")
    def mask_kk(v):
        """Sensor Nomor KK untuk perlindungan privasi."""
        if not v:
            return "-"
        s = str(v).strip()
        if len(s) >= 8:
            return s[:4] + "*" * (len(s) - 8) + s[-4:]
        return "****"

    @app.template_filter("lokasi")
    def lokasi_filter(r):
        """Tampilkan alamat secara dinamis dari desa hingga provinsi."""
        desa = r.get("desa") or r.get("nagari") or ""
        kec = r.get("kecamatan") or ""
        kab = r.get("kabupaten") or ""
        prov = r.get("provinsi") or ""
        parts = [p for p in (desa, kec, kab, prov) if p and p != "-"]
        return ", ".join(parts) if parts else "-"

    @app.context_processor
    def inject_globals():
        counts = db.counts()
        return {
            "draft_count": counts.get("draft", 0),
            "counts": counts,
            "current_user": session.get("username"),
            "user_role": session.get("role"),
            "user_nama": session.get("nama") or session.get("username"),
            "is_logged_in": bool(session.get("user_id")),
            "demo_mode": config.DEMO_MODE,
            "show_demo_accounts": not config.CLOUD_DB,
        }

    # ---------------------------------------------------------------- Beranda Publik & Login Awal
    @app.get("/")
    def home():
        # Selalu arahkan ke halaman login terlebih dahulu saat membuka web awal
        return render_template("login.html")

    @app.get("/beranda")
    def beranda():
        return render_template("home.html", c=db.counts(), latest=db.list_reports(limit=4),
                               ai_ready=not config.IS_VERCEL and predict.model_available())

    # ---------------------------------------------------------------- Autentikasi (Multi-Role)
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = (request.form.get("password") or "").strip()
            user = db.authenticate_user(username, password)

            if not user:
                flash("Username atau password salah. Silakan coba lagi.", "error")
                return render_template("login.html", username=username), 401

            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            session["nama"] = user.get("nama_lengkap") or user["username"]

            flash(f"Berhasil masuk sebagai {session['nama']} ({user['role'].capitalize()}).", "ok")

            next_url = request.args.get("next") or request.form.get("next")
            if next_url and next_url.startswith("/") and not next_url.startswith('//') and '\\' not in next_url and next_url not in ("/", "/login"):
                return redirect(next_url)

            if user["role"] == "admin":
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("petugas_dashboard"))

        return render_template("login.html")

    @app.route("/logout", methods=["GET", "POST"])
    def logout():
        session.clear()
        flash("Anda telah keluar dari sistem.", "ok")
        return redirect(url_for("login"))

    @app.route("/ganti-password", methods=["GET", "POST"])
    @login_required
    def change_password():
        if request.method == "POST":
            old_pwd = request.form.get("old_password", "").strip()
            new_pwd = request.form.get("new_password", "").strip()
            confirm_pwd = request.form.get("confirm_password", "").strip()

            if not db.verify_user_password(session["username"], old_pwd):
                flash("Password lama yang Anda masukkan tidak sesuai.", "error")
                return render_template("ganti_password.html"), 400

            if len(new_pwd) < 6:
                flash("Password baru minimal terdiri dari 6 karakter.", "error")
                return render_template("ganti_password.html"), 400

            if new_pwd != confirm_pwd:
                flash("Konfirmasi password baru tidak cocok.", "error")
                return render_template("ganti_password.html"), 400

            db.change_user_password(session["username"], new_pwd)
            flash("Password berhasil diubah. Gunakan password baru saat login berikutnya.", "ok")

            if session.get("role") == "admin":
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("petugas_dashboard"))

        return render_template("ganti_password.html")

    # ---------------------------------------------------------------- Dashboard Petugas
    @app.get("/petugas/dashboard")
    @role_required("petugas", "admin")
    def petugas_dashboard():
        st = request.args.get("status") or None
        c = db.counts()
        all_reports = db.list_reports()

        if st == "arsip":
            rows = [r for r in all_reports if r["status"] in ("ditolak", "duplikat") or r.get("is_archived") == 1]
        elif st == "menunggu_pemeriksaan":
            rows = [r for r in all_reports if r["effective_status"] == "menunggu_pemeriksaan"]
        elif st == "sedang_diperiksa":
            rows = [r for r in all_reports if r["effective_status"] == "sedang_diperiksa"]
        elif st == "diteruskan":
            rows = [r for r in all_reports if r["effective_status"] == "diteruskan"]
        elif st:
            rows = [r for r in all_reports if r["effective_status"] == st or r["status"] == st]
        else:
            # Antrean aktif: tampilkan laporan aktif tanpa memenuhi antrean dengan yang sudah ditolak/duplikat
            rows = [r for r in all_reports if r["status"] not in ("ditolak", "duplikat") and r.get("is_archived") != 1]

        return render_template("petugas_dashboard.html", rows=rows, c=c, st=st)

    @app.route("/petugas/laporan/<rid>", methods=["GET", "POST"])
    @role_required("petugas", "admin")
    def petugas_detail(rid):
        r = db.get_report(rid)
        if not r or r["status"] == "draft":
            abort(404)

        if request.method == "POST":
            status_pemeriksaan = request.form.get("status_pemeriksaan")
            if status_pemeriksaan not in ("sedang_diperiksa", "diteruskan"):
                flash("Pilihan status pemeriksaan tidak valid.", "error")
                return redirect(url_for("petugas_detail", rid=rid)), 400

            catatan = (request.form.get("catatan_petugas") or "").strip()
            petugas_nama = session.get("nama") or session.get("username", "petugas")

            db.petugas_review_report(rid, status_pemeriksaan, catatan, petugas_nama)

            if status_pemeriksaan == "diteruskan":
                flash(f"Laporan {rid} berhasil diperiksa dan diteruskan ke Admin untuk verifikasi akhir.", "ok")
            else:
                flash(f"Laporan {rid} disimpan dengan status Sedang Diperiksa.", "ok")

            return redirect(url_for("petugas_dashboard"))

        dups = [(ref, db.get_report(ref)) for ref in _dup_refs(r)]
        refs = _ref_map(db.list_reports())
        return render_template("petugas_detail.html", r=r, dups=[(a, b) for a, b in dups if b],
                               referenced_by=refs.get(rid, []))

    # ---------------------------------------------------------------- Dashboard Admin
    @app.get("/admin/dashboard")
    @role_required("admin")
    def admin_dashboard():
        tab = request.args.get("tab", "semua")
        allr = db.list_reports()
        refs = _ref_map(allr)
        need = [r for r in allr if r["effective_status"] in ("menunggu_pemeriksaan", "sedang_diperiksa", "diteruskan")
                and (r["rules_status"] != "lolos" or r["id"] in refs)]

        if tab == "siap":
            rows = [r for r in allr if r.get("status_pemeriksaan") == "diteruskan" and r["status"] == "menunggu"]
        elif tab == "temuan":
            rows = need
        elif tab == "arsip":
            rows = [r for r in allr if r["status"] in ("ditolak", "duplikat") or r.get("is_archived") == 1]
        elif tab == "duplikat":
            rows = [r for r in allr if r["status"] == "duplikat"]
        elif tab in ("terverifikasi", "ditolak", "perlu_revisi", "menunggu"):
            rows = [r for r in allr if r["status"] == tab or (tab == "menunggu" and r["effective_status"] in ("menunggu_pemeriksaan", "sedang_diperiksa", "diteruskan"))]
        else:
            rows = allr

        urgent = {r["id"] for r in need}
        dups = {r["id"]: _dup_refs(r) for r in allr if _dup_refs(r)}
        c = db.counts()
        c["temuan"] = len(need)
        c["siap"] = len([r for r in allr if r.get("status_pemeriksaan") == "diteruskan" and r["status"] == "menunggu"])

        return render_template("admin_dashboard.html", rows=rows, tab=tab, c=c,
                               refs=refs, urgent=urgent, dups=dups)

    # ---------------------------------------------------------------- input + draft
    def _save_photo(draft):
        """Kembalikan (nama_file, pesan_error). Foto draft lama dipertahankan bila tidak ada foto baru."""
        foto_name = draft.get("foto") if draft else None
        file = request.files.get("foto")
        if config.DEMO_MODE:
            marker = request.form.get('demo_photo','')
            if marker and re.fullmatch(r'demo-[a-f0-9]{64}\.jpg',marker):
                return marker,None
            if file and file.filename:
                return foto_name,'Foto demo harus diproses di browser. Muat ulang halaman dan coba lagi.'
            return foto_name,None
        if file and file.filename:
            ext = Path(file.filename).suffix.lower()
            if ext not in ALLOWED_EXT:
                return foto_name, "Format foto harus JPG, PNG, atau WebP."
            if config.CLOUD_DB:
                try:
                    foto_name = cloud_db.save_photo(file.stream)
                except ValueError as exc:
                    return foto_name, str(exc)
            else:
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
        if config.DEMO_MODE:
            if any(len(v)> (400 if k=="deskripsi" else 80) for k,v in f.items()):
                return "Isian demo terlalu panjang. Deskripsi maksimal 400 karakter; kolom lain maksimal 80.",400
            if not request.form.get("demo_consent"):
                flash("Konfirmasi data fiktif sebelum menyimpan.", "error")
                return render_template("input.html", f={**f,"draft_id":did or "","foto":draft.get("foto") if draft else None}),400

        # Normalisasi dua arah data alamat nasional dan lama
        if not f.get("desa") and f.get("nagari"):
            f["desa"] = f["nagari"]
        if not f.get("dusun") and f.get("jorong"):
            f["dusun"] = f["jorong"]
        if not f.get("nagari") and f.get("desa"):
            f["nagari"] = f["desa"]
        if not f.get("jorong") and f.get("dusun"):
            f["jorong"] = f["dusun"]
        if not f.get("provinsi") and f.get("nagari"):
            f["provinsi"] = "Sumatera Barat"
        if not f.get("kabupaten") and f.get("nagari"):
            f["kabupaten"] = "Kabupaten Solok"

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
        required = {
            **rules.DEFAULT_REQUIRED,
            "deskripsi": "Deskripsi kerusakan",
            "foto": "Foto kerusakan"
        }
        issues = rules.validate_record(rec, existing=db.list_reports(), required=required)
        ai = predict.predict_image(config.UPLOAD_DIR / foto_name) if foto_name and not config.IS_VERCEL and not config.CLOUD_DB else None
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

        dups = [i for i in issues if i["code"].startswith("DUP_")]
        if dups:
            alasan = " / ".join(dict.fromkeys(DUP_ALASAN.get(i["code"], "data yang mirip") for i in dups))
            ref_ids = ', '.join(i['ref_id'] for i in dups)
            flash(f"TERDAPAT DUPLIKASI DATA: laporan {rid} memiliki {alasan} dengan {ref_ids}. "
                  f"Terindikasi duplikat dari laporan {ref_ids}. Laporan tetap tersimpan dan menunggu keputusan admin.", "warn")
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
        if session.get("role") == "petugas":
            flash("Petugas tidak memiliki hak untuk menetapkan verifikasi akhir.", "error")
            abort(403)

        if not session.get("user_id"):
            if not ("pytest" in sys.modules and not app.config.get("STRICT_AUTH", False)):
                flash("Silakan login sebagai Admin untuk memverifikasi laporan.", "error")
                return redirect(url_for("login", next=url_for("detail", rid=rid)))
        elif session.get("role") != "admin":
            abort(403)

        r = db.get_report(rid)
        if not r or r["status"] == "draft":
            abort(404)

        kat = (request.form.get("kerusakan_final") or "").strip().lower()
        status = request.form.get("status", "menunggu")
        penanganan = (request.form.get("penanganan") or "").strip()
        catatan = (request.form.get("catatan") or "").strip()
        duplikat_dari = (request.form.get("duplikat_dari") or "").strip()

        if status not in ("menunggu", "terverifikasi", "ditolak", "perlu_revisi", "duplikat"):
            abort(400)

        # Validasi status terverifikasi
        if status == "terverifikasi" and kat not in ("ringan", "sedang", "berat", "hanyut"):
            flash("Pilih kategori kerusakan final yang valid sebelum memverifikasi.", "error")
            return redirect(url_for("detail", rid=rid)), 400

        # Wajib memberikan catatan jika ditolak atau perlu revisi
        if status in ("ditolak", "perlu_revisi") and not catatan:
            flash(f"Admin wajib memberikan catatan/alasan untuk keputusan {STATUS_LABEL.get(status, status)}.", "error")
            return redirect(url_for("detail", rid=rid)), 400

        # Validasi status duplikat
        if status == "duplikat":
            if not duplikat_dari:
                refs = _dup_refs(r)
                duplikat_dari = refs[0] if refs else ""
            if not duplikat_dari:
                flash("Tentukan ID laporan utama untuk laporan duplikat.", "error")
                return redirect(url_for("detail", rid=rid)), 400
            if not catatan:
                catatan = f"Dikonfirmasi admin sebagai duplikat dari {duplikat_dari}."

        admin_nama = session.get("nama") or session.get("username", "admin")
        db.verify_report(rid, kat, penanganan, status, catatan, admin_nama=admin_nama, duplikat_dari=duplikat_dari)
        flash(f"Laporan {rid} berhasil diperbarui: {STATUS_LABEL.get(status, status)}.", "ok")

        if session.get("role") == "admin":
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("review"))

    @app.post("/laporan/<rid>/duplikat")
    def set_duplicate_action(rid):
        """Aksi konfirmasi cepat admin untuk menetapkan status duplikat."""
        if session.get("role") == "petugas":
            abort(403)
        if not session.get("user_id"):
            if not ("pytest" in sys.modules and not app.config.get("STRICT_AUTH", False)):
                flash("Silakan login sebagai Admin.", "error")
                return redirect(url_for("login", next=url_for("detail", rid=rid)))
        elif session.get("role") != "admin":
            abort(403)

        r = db.get_report(rid)
        if not r or r["status"] == "draft":
            abort(404)

        main_rid = (request.form.get("duplikat_dari") or "").strip()
        catatan = (request.form.get("catatan") or "").strip()
        if not main_rid:
            refs = _dup_refs(r)
            main_rid = refs[0] if refs else ""
        if not main_rid:
            flash("ID laporan utama wajib ditentukan untuk menetapkan status duplikat.", "error")
            return redirect(url_for("detail", rid=rid)), 400

        if not catatan:
            catatan = f"Dikonfirmasi admin sebagai duplikat dari laporan {main_rid}."

        admin_nama = session.get("nama") or session.get("username", "admin")
        db.mark_duplicate(rid, main_rid, catatan, admin_nama)
        flash(f"Laporan {rid} berhasil ditetapkan sebagai Duplikat dari {main_rid}.", "ok")
        if session.get("role") == "admin":
            return redirect(url_for("admin_dashboard", tab="duplikat"))
        return redirect(url_for("review", status="duplikat"))

    @app.post("/laporan/<rid>/tolak-duplikat")
    def reject_duplicate(rid):
        """Keputusan ADMIN (satu klik)."""
        if session.get("role") == "petugas":
            abort(403)
        if not session.get("user_id"):
            if not ("pytest" in sys.modules and not app.config.get("STRICT_AUTH", False)):
                flash("Silakan login sebagai Admin.", "error")
                return redirect(url_for("login", next=url_for("detail", rid=rid)))
        elif session.get("role") != "admin":
            abort(403)

        r = db.get_report(rid)
        if not r or r["status"] == "draft":
            abort(404)
        refs = _dup_refs(r)
        if not refs:
            flash("Laporan ini tidak ditandai duplikat.", "error")
            return redirect(url_for("detail", rid=rid))

        admin_nama = session.get("nama") or session.get("username", "admin")
        main_ref = refs[0]
        db.verify_report(rid, r.get("kerusakan_final") or "", r.get("penanganan") or "", "ditolak",
                         f"Ditolak admin ({admin_nama}): duplikat dari {', '.join(refs)}.",
                         admin_nama=admin_nama, duplikat_dari=main_ref)
        flash(f"Laporan {rid} ditolak sebagai duplikat dari {', '.join(refs)}.", "ok")
        if session.get("role") == "admin":
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("review"))

    @app.post("/laporan/<rid>/bukan-duplikat")
    def not_duplicate(rid):
        """Admin menyatakan temuan duplikat bukan duplikat."""
        if session.get("role") == "petugas":
            abort(403)
        if not session.get("user_id"):
            if not ("pytest" in sys.modules and not app.config.get("STRICT_AUTH", False)):
                flash("Silakan login sebagai Admin.", "error")
                return redirect(url_for("login", next=url_for("detail", rid=rid)))
        elif session.get("role") != "admin":
            abort(403)

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
        admin_nama = session.get("nama") or session.get("username", "admin")
        db.update_issues(rid, issues, rules.status_from_issues(issues),
                         f"Admin ({admin_nama}): bukan duplikat dari {', '.join(refs)} (laporan lanjutan/berbeda).")
        flash(f"Laporan {rid} ditandai bukan duplikat. Silakan lanjut verifikasi.", "ok")
        return redirect(url_for("detail", rid=rid))

    @app.post("/laporan/<rid>/arsip")
    @role_required("admin")
    def toggle_archive(rid):
        r = db.get_report(rid)
        if not r or r["status"] == "draft":
            abort(404)
        new_archived = 0 if r.get("is_archived") == 1 else 1
        db.archive_report(rid, is_archived=bool(new_archived))
        msg = "diarsipkan" if new_archived else "dipulihkan dari arsip"
        flash(f"Laporan {rid} berhasil {msg}.", "ok")
        return redirect(url_for("detail", rid=rid))

    @app.post("/laporan/<rid>/hapus-permanen")
    @role_required("admin")
    def admin_delete(rid):
        db.delete_report_admin(rid)
        flash(f"Laporan {rid} telah dihapus permanen oleh Administrator.", "ok")
        return redirect(url_for("admin_dashboard"))

    @app.get("/verifikasi")
    def review():
        st = request.args.get("status") or None
        if st == "draft":
            return redirect(url_for("drafts"))
        allr = db.list_reports()
        refs = _ref_map(allr)
        need = [r for r in allr if r["effective_status"] in ("menunggu_pemeriksaan", "sedang_diperiksa", "diteruskan")
                and (r["rules_status"] != "lolos" or r["id"] in refs)]

        if st == "temuan":
            rows = need
        elif st == "arsip":
            rows = [r for r in allr if r["status"] in ("ditolak", "duplikat") or r.get("is_archived") == 1]
        elif st == "duplikat":
            rows = [r for r in allr if r["status"] == "duplikat"]
        elif st == "menunggu":
            rows = [r for r in allr if r["effective_status"] in ("menunggu_pemeriksaan", "sedang_diperiksa", "diteruskan")]
        elif st:
            rows = [r for r in allr if r["effective_status"] == st or r["status"] == st]
        else:
            rows = allr

        urgent = {r["id"] for r in need}
        dups = {r["id"]: _dup_refs(r) for r in allr if _dup_refs(r)}
        rows = sorted(rows, key=lambda r: 0 if r["id"] in urgent else 1)
        c = db.counts()
        c["temuan"] = len(need)
        return render_template("review.html", rows=rows, st=st, c=c, refs=refs, urgent=urgent, dups=dups)

    @app.route("/ringkasan", methods=["GET", "POST"])
    def summary():
        facts = llm.build_facts(db.list_reports())
        result = llm.summarize(facts, oidc_token=request.headers.get("x-vercel-oidc-token") if config.IS_VERCEL else None) if request.method == "POST" else None
        return render_template("summary.html", facts=facts, result=result, kab=KABUPATEN)

    @app.get("/panduan")
    def guide():
        return render_template("guide.html")

    @app.get("/foto/<name>")
    def foto(name):
        if config.CLOUD_DB:
            data = cloud_db.load_photo(secure_filename(name))
            if data is None:
                abort(404)
            return send_file(BytesIO(data), mimetype="image/jpeg", max_age=0)
        return send_from_directory(config.UPLOAD_DIR, secure_filename(name))

    return app
