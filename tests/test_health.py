from app import main


def test_health_reports_application_version_with_disposable_database(db_engine, monkeypatch):
    monkeypatch.setattr(main, "engine", db_engine)
    payload = main.health()
    assert payload["status"] == "ok"
    assert payload["app"] == "ChainLoop"
    assert payload["version"] == "0.7.0"

