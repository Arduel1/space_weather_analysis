import pathlib

import pytest

from catalog_collector import main as collector
from space_weather_analysis.config.mongo import MongoSettings


@pytest.mark.integration
def test_collector_exits_with_status_1_when_the_catalog_snapshot_cannot_be_read(
    mongo_settings: MongoSettings, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A catalog snapshot that is no JSON ends the collector with status 1, so services waiting for its success do not start."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "master_catalog.json").write_text("{not json", encoding="utf-8")
    for name, value in {
        "MONGO_USER": mongo_settings.user,
        "MONGO_PASS": mongo_settings.password.get_secret_value(),
        "MONGO_HOST": mongo_settings.host,
        "MONGO_PORT": str(mongo_settings.port),
        "MONGO_DB_NAME": mongo_settings.db_name,
        "CATALOG_COLLECTOR_DATA_DIR": str(tmp_path),
    }.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(SystemExit) as raised:
        collector.main()

    assert raised.value.code == 1
