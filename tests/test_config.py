from brickblade.config import Settings


def test_settings_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = Settings()
    assert s.brickblade_price_ttl_hours == 48
    assert s.data_dir.exists()
