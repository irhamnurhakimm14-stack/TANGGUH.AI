import io

import pytest

from tangguh import config


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.sqlite3")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "up")
    from app import create_app
    return create_app().test_client()


FORM = {"nama": "Budi", "nik": "1302131409500001", "no_kk": "1302130111100006", "kecamatan": "Kubung",
        "nagari": "Selayo", "jorong": "Sawah Suduik", "koordinat": "0.95393 S 100.62134 E",
        "deskripsi": "Dinding retak", "setuju": "on"}


def _post(client, **over):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (30, 30), "gray").save(buf, "JPEG")
    buf.seek(0)
    return client.post("/laporan/baru", data={**FORM, **over, "foto": (buf, "a.jpg")},
                       content_type="multipart/form-data")


def test_halaman_utama(client):
    for p in ("/", "/laporan/baru", "/verifikasi", "/ringkasan", "/panduan"):
        assert client.get(p).status_code == 200


def test_alur_laporan_duplikat_dan_verifikasi(client):
    r1 = _post(client)
    assert r1.status_code == 302 and "/laporan/TG-001" in r1.location
    r2 = _post(client)                                  # NIK sama -> peringatan, TIDAK dihapus
    page = client.get(r2.location).get_data(as_text=True)
    assert "Duplikasi data (NIK sama)" in page and "TG-001" in page
    assert client.get("/laporan/TG-001").status_code == 200
    v = client.post("/laporan/TG-001/verifikasi", data={"kerusakan_final": "sedang", "penanganan": "Rehabilitasi", "status": "terverifikasi"})
    assert v.status_code == 302
    s = client.post("/ringkasan").get_data(as_text=True)
    assert "1 rumah" in s


def test_tanpa_persetujuan_ditolak(client):
    data = {k: v for k, v in FORM.items() if k != "setuju"}
    assert client.post("/laporan/baru", data=data).status_code == 400


def test_nik_salah_ditandai(client):
    r = _post(client, nik="123")
    assert "NIK salah panjang" in client.get(r.location).get_data(as_text=True)


def test_laporan_tak_ada(client):
    assert client.get("/laporan/TG-999").status_code == 404


def test_duplikat_banner_tolak_dan_urutan(client):
    _post(client)
    r2 = _post(client)
    page = client.get(r2.location, follow_redirects=True).get_data(as_text=True)
    assert "TERDAPAT DUPLIKASI DATA" in page and "Tolak laporan ini (duplikat)" in page
    assert "Ada laporan lain dengan data yang sama" in client.get("/laporan/TG-001").get_data(as_text=True)
    rev = client.get("/verifikasi").get_data(as_text=True)
    assert "perlu perhatian admin" in rev and "Ada duplikasinya" in rev
    client.post("/laporan/TG-002/tolak-duplikat")                       # keputusan admin, bukan sistem
    assert "ditolak" in client.get("/laporan/TG-002").get_data(as_text=True)


def test_bukan_duplikat_menurunkan_urgensi(client):
    _post(client)
    client.get(_post(client).location)                                  # tampilkan banner awal (dikonsumsi)
    client.post("/laporan/TG-002/bukan-duplikat")
    client.get("/laporan/TG-002")                                       # konsumsi pesan "ditandai bukan duplikat"
    page = client.get("/laporan/TG-002").get_data(as_text=True)
    assert "TERDAPAT DUPLIKASI DATA" not in page and "BUKAN duplikat" in page
    assert "perlu perhatian admin" not in client.get("/verifikasi").get_data(as_text=True)


def test_draft_simpan_lanjut_kirim_hapus(client):
    r = client.post("/laporan/baru", data={"aksi": "draft", "nama": "Siti", "nik": "1302"})  # tanpa validasi/persetujuan
    assert r.status_code == 302 and "/draft" in r.location
    assert "Siti" in client.get("/draft").get_data(as_text=True)
    assert "Draft" in client.get("/").get_data(as_text=True)
    from tangguh import db
    assert db.counts()["total"] == 0 and db.counts()["draft"] == 1       # draft belum jadi laporan
    assert 'value="Siti"' in client.get("/laporan/baru?draft=TG-001").get_data(as_text=True)
    full = {**FORM, "draft_id": "TG-001", "aksi": "kirim"}
    from PIL import Image
    import io as _io
    buf = _io.BytesIO(); Image.new("RGB", (20, 20)).save(buf, "JPEG"); buf.seek(0)
    r = client.post("/laporan/baru", data={**full, "foto": (buf, "a.jpg")}, content_type="multipart/form-data")
    assert "/laporan/TG-001" in r.location and db.counts()["draft"] == 0 and db.counts()["total"] == 1
    client.post("/laporan/baru", data={"aksi": "draft", "nama": "Tmp"})
    assert client.post("/draft/TG-002/hapus").status_code == 302
    assert client.post("/draft/TG-001/hapus").status_code == 404         # laporan terkirim tidak bisa dihapus lewat draft


def test_draft_kosong_ditolak(client):
    assert client.post("/laporan/baru", data={"aksi": "draft"}).status_code == 400


def test_admin_langsung_tolak_dari_daftar(client):
    _post(client)
    _post(client)
    rev = client.get("/verifikasi").get_data(as_text=True)
    assert "/laporan/TG-002/tolak-duplikat" in rev and "Tolak (duplikat)" in rev     # tombol langsung di daftar
    assert "/laporan/TG-001/tolak-duplikat" not in rev                                # laporan asli tidak ditolak
    assert "Rule-Based" not in rev and "DUP_NIK" not in rev                           # bahasa jelas, tanpa istilah teknis
    client.post("/laporan/TG-002/tolak-duplikat")
    after = client.get("/verifikasi").get_data(as_text=True)
    assert "/laporan/TG-002/tolak-duplikat" not in after and "Ditolak" in after


def test_pesan_duplikasi_jelas(client):
    _post(client)
    r2 = _post(client)
    page = client.get(r2.location, follow_redirects=True).get_data(as_text=True)
    assert "TERDAPAT DUPLIKASI DATA" in page and "NIK yang sama" in page and "Rule-Based" not in page
