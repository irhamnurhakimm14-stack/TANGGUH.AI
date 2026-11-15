import pandas as pd

from tangguh import batch, data_loader as dl, rules


def codes(iss):
    return {i["code"] for i in iss}


def test_nik_valid():
    assert rules.validate_nik("1302131409500001") == []
    assert rules.validate_nik("1302134208810003") == []  # perempuan: tgl 02 + 40


def test_nik_salah():
    assert "NIK_PANJANG" in codes(rules.validate_nik("130205808750003"))      # 15 digit
    assert "NIK_PANJANG" in codes(rules.validate_nik("15710113112550121"))    # 17 digit
    assert "NIK_URUT_NOL" in codes(rules.validate_nik("1302100506650000"))
    assert "NIK_TGL_LAHIR" in codes(rules.validate_nik("1302139913500001"))
    assert "NIK_LUAR_WILAYAH" in codes(rules.validate_nik("1402014702790004"))
    assert rules.validate_nik("'1302130505750001") == []                     # petik Excel dibersihkan


def test_koordinat():
    assert rules.parse_coordinate("0.95393 S 100.62134 E") == (-0.95393, 100.62134)
    assert rules.parse_coordinate("0.93765S, 100.620183E") == (-0.93765, 100.620183)
    assert rules.validate_coordinate("0.95393 S 100.62134 E") == []
    assert "GPS_FORMAT" in codes(rules.validate_coordinate("tidak tahu"))
    assert "GPS_LUAR_SUMBAR" in codes(rules.validate_coordinate("-6.2 106.8"))
    assert "GPS_LUAR_INDONESIA" in codes(rules.validate_coordinate("40.7 -74.0"))


def test_kelengkapan():
    assert codes(rules.validate_completeness({"nama": "A"})) == {"KOSONG"}
    full = {k: "x" for k in rules.DEFAULT_REQUIRED}
    assert rules.validate_completeness(full) == []


def test_duplikasi_tidak_menghapus():
    existing = [{"id": "TG-014", "nama": "Budi Santoso", "nik": "1302131409500001", "no_kk": "1302130111100006", "jorong": "Guci IV"}]
    d = rules.find_duplicates({"nama": "Budi S", "nik": "1302131409500001", "jorong": "Guci IV"}, existing)
    assert d and d[0]["code"] == "DUP_NIK" and d[0]["ref_id"] == "TG-014" and d[0]["level"] == "warning"
    assert rules.find_duplicates({"nama": "Orang Lain", "nik": "1302131409500099", "jorong": "X"}, existing) == []


def test_konsistensi():
    assert "KONSISTENSI_PENANGANAN" in codes(rules.validate_consistency({"kerusakan": "Rusak Berat", "penanganan": "Rehabilitasi"}))
    assert rules.validate_consistency({"kerusakan": "Hanyut / Hilang", "penanganan": "Relokasi"}) == []
    assert "NIK_SAMA_KK" in codes(rules.validate_consistency({"nik": "1" * 16, "no_kk": "1" * 16}))


def test_status():
    assert rules.status_from_issues([]) == "lolos"
    assert rules.status_from_issues([rules.issue("a", "warning", "f", "m")]) == "perlu_cek"
    assert rules.status_from_issues([rules.issue("a", "error", "f", "m")]) == "perlu_perbaikan"


def test_batch_dan_rekap():
    df = pd.DataFrame([
        {"id": "R1", "nama": "A", "nik": "1302131409500001", "no_kk": "1302130111100006", "kecamatan": "K", "nagari": "Muaro Pingai", "jorong": "J", "kerusakan": "Rusak Berat", "penanganan": "Relokasi"},
        {"id": "R2", "nama": "B", "nik": "1302131409500001", "no_kk": "1302130111100007", "kecamatan": "K", "nagari": "Aie Dingin", "jorong": "J", "kerusakan": "Rusak Ringan", "penanganan": "Rehabilitasi"},
    ])
    iss = batch.validate_dataframe(df)
    assert (iss["code"] == "DUP_NIK").sum() == 1
    rekap = pd.DataFrame([{"nagari": "Muaro Pingai", "jumlah": 1, "rb": 1, "rs": 0, "rr": 0, "hanyut": 0},
                          {"nagari": "Aia Dingin", "jumlah": 2, "rb": 0, "rs": 0, "rr": 2, "hanyut": 0}])
    cmp_ = dl.compare_with_rekap(df, rekap)           # 'Aie Dingin' dipetakan ke 'Aia Dingin'
    row = cmp_[cmp_.nagari == "aia dingin"].iloc[0]
    assert row.selisih_jumlah == -1 and row.selisih_rr == -1
