"""設定の読み書き。"""

from basestation_controller.ble_controller import BLEController
from basestation_controller.config import (
    AppConfig,
    BaseStationConfig,
    load_config,
    save_config,
)


def test_missing_file_uses_retry_defaults(tmp_path):
    config = load_config(tmp_path / "missing.yaml")

    assert config.bluetooth.scan_timeout == 10.0
    assert config.bluetooth.connection_timeout == 15.0
    assert config.bluetooth.discovery_timeout == 5.0
    assert config.bluetooth.max_attempts == 3
    assert config.bluetooth.retry_backoff == 1.0
    assert config.bluetooth.max_concurrency == 1
    assert config.bluetooth.command_interval == 0.5
    assert config.server.default_wait is True
    assert config.base_stations == []


def test_invalid_values_fall_back_and_zero_backoff_is_kept(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
server:
  host: 127.0.0.1
  port: 8080
  default_wait: false
bluetooth:
  max_attempts: 0
  max_concurrency: 4
  retry_backoff: 0
  command_interval: 0
  discovery_timeout: -3
  connection_timeout: "12"
  scan_timeout: nope
base_stations:
  - id: bs1
    name: LHB-TEST
    mac_address: AA:BB:CC:DD:EE:01
  - id: ""
    name: skipped
    mac_address: AA:BB:CC:DD:EE:02
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.server.host == "127.0.0.1"
    assert config.server.port == 8080
    assert config.server.default_wait is False
    assert config.bluetooth.max_attempts == 3
    assert config.bluetooth.max_concurrency == 4
    assert config.bluetooth.retry_backoff == 0.0
    assert config.bluetooth.command_interval == 0.0
    assert config.bluetooth.discovery_timeout == 5.0
    assert config.bluetooth.connection_timeout == 12.0
    assert config.bluetooth.scan_timeout == 10.0
    assert len(config.base_stations) == 1
    assert config.base_stations[0].id == "bs1"


def test_save_roundtrip_keeps_new_settings(tmp_path):
    path = tmp_path / "config.yaml"
    config = AppConfig()
    config.server.port = 9000
    config.server.default_wait = False
    config.bluetooth.max_attempts = 5
    config.bluetooth.retry_backoff = 0.25
    config.bluetooth.max_concurrency = 2
    config.bluetooth.discovery_timeout = 8
    config.add_base_station(
        BaseStationConfig(id="bs1", name="LHB-TEST", mac_address="AA:BB:CC:DD:EE:01")
    )

    assert save_config(config, path) is True
    loaded = load_config(path)

    assert loaded.server.port == 9000
    assert loaded.server.default_wait is False
    assert loaded.bluetooth.max_attempts == 5
    assert loaded.bluetooth.retry_backoff == 0.25
    assert loaded.bluetooth.max_concurrency == 2
    assert loaded.bluetooth.discovery_timeout == 8
    assert loaded.get_all_mac_addresses() == ["AA:BB:CC:DD:EE:01"]


def test_controller_from_settings_copies_bluetooth_config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
bluetooth:
  max_attempts: 4
  retry_backoff: 0.5
  max_concurrency: 2
  discovery_timeout: 6
  connection_timeout: 9
  command_interval: 0.2
""",
        encoding="utf-8",
    )
    config = load_config(path)
    controller = BLEController.from_settings(config.bluetooth)

    assert controller.max_attempts == 4
    assert controller.retry_backoff == 0.5
    assert controller.max_concurrency == 2
    assert controller.discovery_timeout == 6
    assert controller.connection_timeout == 9
    assert controller.command_interval == 0.2


def test_checked_in_config_exposes_retry_settings():
    """リポジトリの config.yaml が新しいキーを読み、台数以外の中身は固定しない。"""
    config = load_config()

    assert config.server.port == 5000
    assert config.server.default_wait is True
    assert config.bluetooth.max_attempts == 3
    assert config.bluetooth.retry_backoff == 1.0
    assert config.bluetooth.max_concurrency == 1
    assert config.bluetooth.discovery_timeout == 5
    assert config.bluetooth.command_interval == 0.5
    assert config.bluetooth.connection_timeout == 15
    assert config.bluetooth.scan_timeout == 10
