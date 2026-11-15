from tangguh import llm

REPORTS = [
    {"status": "terverifikasi", "kecamatan": "Kubung", "nagari": "Selayo", "kerusakan_final": "berat", "penanganan": "Relokasi"},
    {"status": "terverifikasi", "kecamatan": "Kubung", "nagari": "Selayo", "kerusakan_final": "ringan", "penanganan": "Rehabilitasi"},
    {"status": "menunggu", "kecamatan": "Kubung", "nagari": "Selayo", "kerusakan_final": "ringan", "penanganan": ""},
]


def test_facts_hanya_terverifikasi():
    f = llm.build_facts(REPORTS)
    assert f["total_terverifikasi"] == 2 and f["per_kategori"]["berat"] == 1
    assert f["wilayah_terbanyak"]["jumlah"] == 2


def test_template_tanpa_api_key():
    r = llm.summarize(llm.build_facts(REPORTS))
    assert r["source"] == "template" and "2 rumah" in r["text"]


def test_angka_halusinasi_dibuang():
    f = llm.build_facts(REPORTS)
    r = llm.summarize(f, caller=lambda p: "Ada 2 rumah terdampak dan 57 korban jiwa.")
    assert r["source"] == "template" and "57" in r["note"]


def test_jawaban_benar_diterima():
    f = llm.build_facts(REPORTS)
    r = llm.summarize(f, caller=lambda p: "RINGKASAN UMUM: 2 rumah terverifikasi, 1 rusak berat dan 1 rusak ringan.")
    assert r["source"] == "gemini"


def test_llm_error_fallback():
    def boom(p):
        raise TimeoutError()
    assert llm.summarize(llm.build_facts(REPORTS), caller=boom)["source"] == "template"
