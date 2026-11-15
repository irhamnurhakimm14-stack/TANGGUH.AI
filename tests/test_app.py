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
    assert r1.status_code == 302 and r1.location.endswith("/laporan/TG-001")
    r2 = _post(client)                                  # NIK sama -> peringatan, TIDAK dihapus
    page = client.get(r2.location).get_data(as_text=True)
    assert "DUP_NIK" in page and "TG-001" in page
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
    assert "NIK_PANJANG" in client.get(r.location).get_data(as_text=True)


def test_laporan_tak_ada(client):
    assert client.get("/laporan/TG-999").status_code == 404
