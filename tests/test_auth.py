"""Pengujian fitur Autentikasi Multi-Role (Petugas dan Admin) serta alur Human-in-the-Loop."""
import io
import pytest
from PIL import Image

from tangguh import config, db, llm


@pytest.fixture
def auth_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test_auth.sqlite3")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "up_auth")
    from app import create_app
    app = create_app()
    app.config["TESTING"] = True
    app.config["STRICT_AUTH"] = True
    return app.test_client()


def _create_sample_report(client):
    buf = io.BytesIO()
    Image.new("RGB", (30, 30), "gray").save(buf, "JPEG")
    buf.seek(0)
    data = {
        "nama": "Pak Sutan", "nik": "1302131409500001", "no_kk": "1302130111100006",
        "kecamatan": "Kubung", "nagari": "Selayo", "jorong": "Sawah Suduik",
        "koordinat": "0.95393 S 100.62134 E", "deskripsi": "Atap roboh diterjang air",
        "setuju": "on", "foto": (buf, "atap.jpg")
    }
    client.post("/laporan/baru", data=data, content_type="multipart/form-data")
    return "TG-001"


def test_demo_accounts_seeded_with_hashing(auth_client):
    admin = db.get_user_by_username("admin")
    petugas = db.get_user_by_username("petugas")

    assert admin is not None
    assert admin["role"] == "admin"
    assert petugas is not None
    assert petugas["role"] == "petugas"

    # Verifikasi password tidak disimpan plaintext
    with db.connect() as con:
        r_admin = con.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()
        r_petugas = con.execute("SELECT password_hash FROM users WHERE username='petugas'").fetchone()
        assert r_admin["password_hash"] != "admin123"
        assert r_petugas["password_hash"] != "petugas123"
        assert r_admin["password_hash"].startswith("scrypt:") or len(r_admin["password_hash"]) > 20

    # Verifikasi autentikasi aman
    assert db.authenticate_user("admin", "admin123") is not None
    assert db.authenticate_user("petugas", "petugas123") is not None
    assert db.authenticate_user("admin", "wrongpassword") is None


def test_login_page_renders_form_and_demo_info(auth_client):
    res = auth_client.get("/login")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert 'name="username"' in html
    assert 'name="password"' in html
    assert "togglePassword" in html
    assert "admin123" in html
    assert "petugas123" in html


def test_login_invalid_credentials_returns_401(auth_client):
    res = auth_client.post("/login", data={"username": "admin", "password": "salah_password"})
    assert res.status_code == 401
    html = res.get_data(as_text=True)
    assert "Username atau password salah" in html


def test_login_admin_redirects_to_admin_dashboard(auth_client):
    res = auth_client.post("/login", data={"username": "admin", "password": "admin123"})
    assert res.status_code == 302
    assert "/admin/dashboard" in res.location

    # Akses dashboard admin
    dash = auth_client.get("/admin/dashboard")
    assert dash.status_code == 200
    html = dash.get_data(as_text=True)
    assert "Dashboard Administrator" in html
    assert "ADMIN" in html


def test_login_petugas_redirects_to_petugas_dashboard(auth_client):
    res = auth_client.post("/login", data={"username": "petugas", "password": "petugas123"})
    assert res.status_code == 302
    assert "/petugas/dashboard" in res.location

    # Akses dashboard petugas
    dash = auth_client.get("/petugas/dashboard")
    assert dash.status_code == 200
    html = dash.get_data(as_text=True)
    assert "Dashboard Petugas Lapangan" in html
    assert "PETUGAS" in html


def test_access_control_and_role_protection(auth_client):
    # Akses tanpa login diarahkan ke /login
    r1 = auth_client.get("/admin/dashboard")
    assert r1.status_code == 302
    assert "/login" in r1.location

    r2 = auth_client.get("/petugas/dashboard")
    assert r2.status_code == 302
    assert "/login" in r2.location

    # Petugas tidak boleh mengakses halaman khusus admin
    auth_client.post("/login", data={"username": "petugas", "password": "petugas123"})
    r_forbidden = auth_client.get("/admin/dashboard")
    assert r_forbidden.status_code == 403


def test_petugas_inspection_workflow(auth_client):
    rid = _create_sample_report(auth_client)

    # Login sebagai petugas
    auth_client.post("/login", data={"username": "petugas", "password": "petugas123"})

    # Petugas memeriksa dan menyimpan sebagai sedang_diperiksa
    r_periksa = auth_client.post(f"/petugas/laporan/{rid}", data={
        "status_pemeriksaan": "sedang_diperiksa",
        "catatan_petugas": "Sedang koordinasi dengan wali nagari dan korban."
    })
    assert r_periksa.status_code == 302

    rep = db.get_report(rid)
    assert rep["status_pemeriksaan"] == "sedang_diperiksa"
    assert "wali nagari" in rep["catatan_petugas"]

    # Petugas meneruskan ke admin
    r_teruskan = auth_client.post(f"/petugas/laporan/{rid}", data={
        "status_pemeriksaan": "diteruskan",
        "catatan_petugas": "Selesai ditinjau: rangka atap kayu hancur. Diteruskan ke admin."
    })
    assert r_teruskan.status_code == 302

    rep_after = db.get_report(rid)
    assert rep_after["status_pemeriksaan"] == "diteruskan"
    assert rep_after["status"] == "menunggu"  # Keputusan admin masih menunggu


def test_petugas_dilarang_verifikasi_akhir(auth_client):
    rid = _create_sample_report(auth_client)

    # Login sebagai petugas
    auth_client.post("/login", data={"username": "petugas", "password": "petugas123"})

    # Coba melakukan verifikasi akhir (seharusnya ditolak 403 Forbidden)
    res = auth_client.post(f"/laporan/{rid}/verifikasi", data={
        "status": "terverifikasi",
        "kerusakan_final": "berat",
        "penanganan": "Relokasi",
        "catatan": "Petugas coba memverifikasi"
    })
    assert res.status_code == 403


def test_admin_verification_workflow_terverifikasi(auth_client):
    rid = _create_sample_report(auth_client)

    # Petugas melakukan pemeriksaan awal terlebih dahulu
    auth_client.post("/login", data={"username": "petugas", "password": "petugas123"})
    auth_client.post(f"/petugas/laporan/{rid}", data={
        "status_pemeriksaan": "diteruskan",
        "catatan_petugas": "Laporan valid hasil cek lapangan."
    })
    auth_client.get("/logout")

    # Login sebagai admin
    auth_client.post("/login", data={"username": "admin", "password": "admin123"})

    # Admin menetapkan keputusan akhir
    v = auth_client.post(f"/laporan/{rid}/verifikasi", data={
        "status": "terverifikasi",
        "kerusakan_final": "berat",
        "penanganan": "Relokasi",
        "catatan": "Disetujui relokasi berdasarkan catatan petugas."
    })
    assert v.status_code == 302

    rep = db.get_report(rid)
    assert rep["status"] == "terverifikasi"
    assert rep["kerusakan_final"] == "berat"
    assert rep["penanganan"] == "Relokasi"
    assert rep["catatan_petugas"] == "Laporan valid hasil cek lapangan."


def test_admin_keputusan_ditolak_dan_perlu_revisi_wajib_catatan(auth_client):
    rid = _create_sample_report(auth_client)

    auth_client.post("/login", data={"username": "admin", "password": "admin123"})

    # Ditolak tanpa catatan -> Ditolak 400 Bad Request
    r_tolak_kosong = auth_client.post(f"/laporan/{rid}/verifikasi", data={
        "status": "ditolak",
        "catatan": ""
    })
    assert r_tolak_kosong.status_code == 400

    # Perlu revisi tanpa catatan -> Ditolak 400 Bad Request
    r_revisi_kosong = auth_client.post(f"/laporan/{rid}/verifikasi", data={
        "status": "perlu_revisi",
        "catatan": ""
    })
    assert r_revisi_kosong.status_code == 400

    # Perlu revisi dengan catatan -> Berhasil
    r_revisi_sukses = auth_client.post(f"/laporan/{rid}/verifikasi", data={
        "status": "perlu_revisi",
        "catatan": "Mohon petugas melampirkan foto bagian dinding samping."
    })
    assert r_revisi_sukses.status_code == 302
    assert db.get_report(rid)["status"] == "perlu_revisi"

    # Ditolak dengan catatan -> Berhasil
    r_tolak_sukses = auth_client.post(f"/laporan/{rid}/verifikasi", data={
        "status": "ditolak",
        "catatan": "Alamat dan identitas NIK tidak sesuai data dukcapil."
    })
    assert r_tolak_sukses.status_code == 302
    assert db.get_report(rid)["status"] == "ditolak"


def test_admin_koreksi_kategori_hanyut(auth_client):
    rid = _create_sample_report(auth_client)
    auth_client.post("/login", data={"username": "admin", "password": "admin123"})

    # Kategori Hanyut / Hilang diputuskan secara Human-in-the-Loop
    res = auth_client.post(f"/laporan/{rid}/verifikasi", data={
        "status": "terverifikasi",
        "kerusakan_final": "hanyut",
        "penanganan": "Relokasi",
        "catatan": "Rumah tersapu galodo / banjir bandang total."
    })
    assert res.status_code == 302
    rep = db.get_report(rid)
    assert rep["kerusakan_final"] == "hanyut"
    assert rep["status"] == "terverifikasi"


def test_ganti_password_flow(auth_client):
    auth_client.post("/login", data={"username": "petugas", "password": "petugas123"})

    # Password lama salah
    r1 = auth_client.post("/ganti-password", data={
        "old_password": "salah",
        "new_password": "passwordBaru123",
        "confirm_password": "passwordBaru123"
    })
    assert r1.status_code == 400

    # Konfirmasi tidak cocok
    r2 = auth_client.post("/ganti-password", data={
        "old_password": "petugas123",
        "new_password": "passwordBaru123",
        "confirm_password": "berbeda"
    })
    assert r2.status_code == 400

    # Sukses ubah password
    r3 = auth_client.post("/ganti-password", data={
        "old_password": "petugas123",
        "new_password": "passwordBaru123",
        "confirm_password": "passwordBaru123"
    })
    assert r3.status_code == 302

    # Logout dan coba login dengan password baru
    auth_client.get("/logout")
    assert db.authenticate_user("petugas", "petugas123") is None
    assert db.authenticate_user("petugas", "passwordBaru123") is not None


def test_national_address_reporting(auth_client):
    """Pengujian input laporan dengan format alamat nasional (misal Jawa Barat)."""
    buf = io.BytesIO()
    Image.new("RGB", (30, 30), "red").save(buf, "JPEG")
    buf.seek(0)
    data = {
        "nama": "Asep Sunandar",
        "nik": "3203011205850002",  # NIK Jawa Barat (Cianjur)
        "no_kk": "3203010101100005",
        "provinsi": "Jawa Barat",
        "kabupaten": "Kabupaten Cianjur",
        "kecamatan": "Cugenang",
        "desa": "Cibulakan",
        "dusun": "Kampung Garogol",
        "rt_rw": "02/05",
        "koordinat": "-6.81234, 107.12345",
        "deskripsi": "Dinding rumah retak parah akibat gempa bumi",
        "setuju": "on",
        "foto": (buf, "cianjur.jpg")
    }
    res = auth_client.post("/laporan/baru", data=data, content_type="multipart/form-data")
    assert res.status_code == 302

    rep = db.get_report("TG-001")
    assert rep is not None
    assert rep["provinsi"] == "Jawa Barat"
    assert rep["kabupaten"] == "Kabupaten Cianjur"
    assert rep["desa"] == "Cibulakan"
    assert rep["dusun"] == "Kampung Garogol"
    assert rep["rt_rw"] == "02/05"


def test_duplicate_warning_no_auto_reject_and_admin_duplicate_action(auth_client):
    """Pengujian: NIK sama tidak otomatis menolak laporan; admin bisa menandai duplikat atau bukan duplikat."""
    # Laporan 1
    rid1 = _create_sample_report(auth_client)

    # Verifikasi laporan 1 oleh admin
    auth_client.post("/login", data={"username": "admin", "password": "admin123"})
    auth_client.post(f"/laporan/{rid1}/verifikasi", data={
        "status": "terverifikasi",
        "kerusakan_final": "berat",
        "catatan": "Valid terverifikasi"
    })
    auth_client.get("/logout")

    # Laporan 2 dengan NIK sama persis
    buf2 = io.BytesIO()
    Image.new("RGB", (30, 30), "blue").save(buf2, "JPEG")
    buf2.seek(0)
    data2 = {
        "nama": "Pak Sutan",
        "nik": "1302131409500001",  # NIK sama dengan TG-001
        "no_kk": "1302130111100006",
        "kecamatan": "Kubung",
        "desa": "Selayo",
        "deskripsi": "Laporan susulan untuk bagian belakang",
        "setuju": "on",
        "foto": (buf2, "susulan.jpg")
    }
    res2 = auth_client.post("/laporan/baru", data=data2, content_type="multipart/form-data")
    assert res2.status_code == 302
    rid2 = "TG-002"

    rep2 = db.get_report(rid2)
    # Laporan TIDAK boleh otomatis ditolak!
    assert rep2["status"] == "menunggu"
    assert rep2["effective_status"] == "menunggu_pemeriksaan"

    # Periksa adanya isu duplikasi
    issues_code = [i["code"] for i in rep2["issues"]]
    assert "DUP_NIK" in issues_code

    # Buka detail: periksa pesan peringatan duplikasi
    auth_client.post("/login", data={"username": "admin", "password": "admin123"})
    detail_res = auth_client.get(f"/laporan/{rid2}")
    assert detail_res.status_code == 200
    html = detail_res.get_data(as_text=True)
    assert f"Terindikasi duplikat dari laporan {rid1}" in html

    # Admin menandai sebagai duplikat
    dup_res = auth_client.post(f"/laporan/{rid2}/duplikat", data={
        "duplikat_dari": rid1,
        "catatan": f"Dikonfirmasi sebagai duplikat dari {rid1}"
    })
    assert dup_res.status_code == 302

    rep2_after = db.get_report(rid2)
    assert rep2_after["status"] == "duplikat"
    assert rep2_after["effective_status"] == "duplikat"
    assert rep2_after["duplikat_dari"] == rid1


def test_rejected_and_duplicate_excluded_from_damage_stats_and_llm(auth_client):
    """Pengujian: Laporan ditolak dan duplikat tidak masuk statistik kerusakan dan LLM facts."""
    # Buat TG-001 (terverifikasi berat)
    rid1 = _create_sample_report(auth_client)
    auth_client.post("/login", data={"username": "admin", "password": "admin123"})
    auth_client.post(f"/laporan/{rid1}/verifikasi", data={
        "status": "terverifikasi",
        "kerusakan_final": "berat",
        "catatan": "Terverifikasi rusak berat"
    })

    # Buat TG-002 (ditolak)
    buf2 = io.BytesIO()
    Image.new("RGB", (30, 30), "green").save(buf2, "JPEG")
    buf2.seek(0)
    auth_client.post("/laporan/baru", data={
        "nama": "Warga B", "nik": "1302131409500002", "no_kk": "1302130111100007",
        "kecamatan": "Kubung", "desa": "Koto Baru", "deskripsi": "Data fiktif",
        "setuju": "on", "foto": (buf2, "fiktif.jpg")
    }, content_type="multipart/form-data")
    auth_client.post("/laporan/TG-002/verifikasi", data={
        "status": "ditolak",
        "catatan": "Ditolak karena data tidak valid"
    })

    # Buat TG-003 (duplikat)
    buf3 = io.BytesIO()
    Image.new("RGB", (30, 30), "yellow").save(buf3, "JPEG")
    buf3.seek(0)
    auth_client.post("/laporan/baru", data={
        "nama": "Pak Sutan Duplikat", "nik": "1302131409500001", "no_kk": "1302130111100006",
        "kecamatan": "Kubung", "desa": "Selayo", "deskripsi": "Duplikat",
        "setuju": "on", "foto": (buf3, "dup.jpg")
    }, content_type="multipart/form-data")
    auth_client.post("/laporan/TG-003/duplikat", data={
        "duplikat_dari": "TG-001",
        "catatan": "Duplikat dari TG-001"
    })

    # Verifikasi database facts LLM
    all_reps = db.list_reports()
    facts = llm.build_facts(all_reps)

    # Hanya 1 laporan yang terverifikasi (TG-001)
    assert facts["total_terverifikasi"] == 1
    assert facts["per_kategori"]["berat"] == 1
    assert facts["per_kategori"]["sedang"] == 0
    assert facts["per_kategori"]["ringan"] == 0

    # Laporan ditolak dan duplikat TIDAK boleh ada dalam daftar item terverifikasi
    item_ids = [item["id"] for item in facts["items"]]
    assert "TG-001" in item_ids
    assert "TG-002" not in item_ids
    assert "TG-003" not in item_ids

    # Privasi: LLM facts TIDAK boleh memuat NIK atau nama pribadi warga
    for item in facts["items"]:
        assert "nik" not in item
        assert "nama" not in item


def test_status_consistency_across_dashboards_and_tabs(auth_client):
    """Pengujian konsistensi status di Dashboard Petugas, Admin, dan Rekap."""
    rid = _create_sample_report(auth_client)

    # Status awal konsisten: menunggu pemeriksaan
    rep = db.get_report(rid)
    assert rep["effective_status"] == "menunggu_pemeriksaan"

    # Petugas periksa -> diteruskan
    auth_client.post("/login", data={"username": "petugas", "password": "petugas123"})
    auth_client.post(f"/petugas/laporan/{rid}", data={
        "status_pemeriksaan": "diteruskan",
        "catatan_petugas": "Cek lapangan selesai."
    })
    auth_client.get("/logout")

    rep_iter = db.get_report(rid)
    assert rep_iter["effective_status"] == "diteruskan"

    # Admin verifikasi -> terverifikasi
    auth_client.post("/login", data={"username": "admin", "password": "admin123"})
    auth_client.post(f"/laporan/{rid}/verifikasi", data={
        "status": "terverifikasi",
        "kerusakan_final": "sedang",
        "catatan": "Disetujui rusak sedang"
    })

    # Di Petugas Dashboard: harus langsung terverifikasi, bukan lagi menunggu
    rep_final = db.get_report(rid)
    assert rep_final["effective_status"] == "terverifikasi"

    petugas_page = auth_client.get("/petugas/dashboard")
    assert petugas_page.status_code == 200
    p_html = petugas_page.get_data(as_text=True)
    assert "Terverifikasi" in p_html

    # Admin Dashboard juga terverifikasi
    admin_page = auth_client.get("/admin/dashboard")
    assert admin_page.status_code == 200
    a_html = admin_page.get_data(as_text=True)
    assert "Terverifikasi" in a_html

    # Rekap (/verifikasi) juga terverifikasi
    rekap_page = auth_client.get("/verifikasi")
    assert rekap_page.status_code == 200
    r_html = rekap_page.get_data(as_text=True)
    assert "Terverifikasi" in r_html


def test_archiving_soft_delete(auth_client):
    """Pengujian soft-delete / arsip tanpa penghapusan permanen."""
    rid = _create_sample_report(auth_client)

    auth_client.post("/login", data={"username": "admin", "password": "admin123"})
    # Arsipkan
    res_arsip = auth_client.post(f"/laporan/{rid}/arsip")
    assert res_arsip.status_code == 302

    rep = db.get_report(rid)
    assert rep["is_archived"] == 1

    # Masih tersimpan di database dan bisa diakses
    res_detail = auth_client.get(f"/laporan/{rid}")
    assert res_detail.status_code == 200
    assert "Diarsipkan" in res_detail.get_data(as_text=True)

    # Muncul di tab arsip
    res_tab_arsip = auth_client.get("/admin/dashboard?tab=arsip")
    assert rid in res_tab_arsip.get_data(as_text=True)


def test_root_always_shows_login_page(auth_client):
    """Pengujian: Membuka root URL '/' selalu menampilkan halaman login terlebih dahulu."""
    # Tanpa login
    res = auth_client.get("/")
    assert res.status_code == 200
    assert 'name="username"' in res.get_data(as_text=True)
    assert 'name="password"' in res.get_data(as_text=True)

    # Bahkan jika sudah ada session login, buka '/' tetap menampilkan halaman login
    auth_client.post("/login", data={"username": "admin", "password": "admin123"})
    res_logged = auth_client.get("/")
    assert res_logged.status_code == 200
    assert 'name="username"' in res_logged.get_data(as_text=True)


def test_clip_model_inference_and_info(tmp_path):
    """Pengujian pemuatan model CLIP ViT-B/32 + Logistic Regression dan inferensi gambar."""
    from tangguh.ml import predict
    assert predict.model_available() is True

    info = predict.get_model_info()
    assert info["status"] == "ready"
    assert info["class_count"] == 2
    assert "ringan" in info["classes"]
    assert "sedang_berat" in info["classes"]

    # Buat file gambar uji
    test_img = tmp_path / "test_building.jpg"
    Image.new("RGB", (224, 224), color="gray").save(test_img, "JPEG")

    res = predict.predict_image(test_img)
    assert res["status"] in ("ok", "tidak_yakin")
    assert res["label"] in ("ringan", "sedang_berat")
    assert isinstance(res["confidence"], float)
    assert 0.0 <= res["confidence"] <= 1.0
    assert "ringan" in res["probs"]
    assert "sedang_berat" in res["probs"]


def test_clip_invalid_image_handling(tmp_path):
    """Pengujian penanganan file foto rusak atau tidak valid agar tidak menghasilkan prediksi palsu."""
    from tangguh.ml import predict

    # File rusak (bukan format gambar valid)
    corrupt_file = tmp_path / "corrupted.jpg"
    corrupt_file.write_text("Ini bukan gambar asli tapi teks palsu", encoding="utf-8")

    res = predict.predict_image(corrupt_file)
    assert res["status"] == "error"
    assert res["label"] is None
    assert res["confidence"] is None
    assert "tidak valid atau rusak" in res["note"]

    # File tidak ada
    non_existent = tmp_path / "tidak_ada.jpg"
    res_none = predict.predict_image(non_existent)
    assert res_none["status"] == "error"
    assert res_none["label"] is None


