"""
Configuration Manager

YAMLベースの設定ファイル管理
"""

import os
import logging
from dataclasses import dataclass, field
from typing import List, Optional
from pathlib import Path
import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


@dataclass
class BaseStationConfig:
    """Base Station設定"""
    id: str
    name: str
    mac_address: str
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "mac_address": self.mac_address
        }


@dataclass
class ServerConfig:
    """サーバー設定"""
    host: str = "0.0.0.0"
    port: int = 5000
    # True のとき /all は完了まで待つ。False なら 202 ですぐ返す。
    default_wait: bool = True


@dataclass
class BluetoothConfig:
    """Bluetooth設定

    max_concurrency の既定は 1。CSR Bluetooth 4.0 (0a12:0001) は
    接続試行を同時に複数本出すと失敗しやすいため、台ごとに直列で行う。
    """
    scan_timeout: float = 10.0
    connection_timeout: float = 15.0
    discovery_timeout: float = 5.0
    max_attempts: int = 3
    retry_backoff: float = 1.0
    max_concurrency: int = 1
    command_interval: float = 0.5


@dataclass
class AppConfig:
    """アプリケーション設定"""
    server: ServerConfig = field(default_factory=ServerConfig)
    bluetooth: BluetoothConfig = field(default_factory=BluetoothConfig)
    base_stations: List[BaseStationConfig] = field(default_factory=list)
    
    def get_base_station_by_id(self, station_id: str) -> Optional[BaseStationConfig]:
        """IDでBase Stationを取得"""
        for bs in self.base_stations:
            if bs.id == station_id:
                return bs
        return None
    
    def get_all_mac_addresses(self) -> List[str]:
        """全Base StationのMACアドレスを取得"""
        return [bs.mac_address for bs in self.base_stations]
    
    def add_base_station(self, bs: BaseStationConfig) -> None:
        """Base Stationを追加"""
        # 既存のIDがあれば更新
        for i, existing in enumerate(self.base_stations):
            if existing.id == bs.id:
                self.base_stations[i] = bs
                return
        self.base_stations.append(bs)
    
    def remove_base_station(self, station_id: str) -> bool:
        """Base Stationを削除"""
        for i, bs in enumerate(self.base_stations):
            if bs.id == station_id:
                del self.base_stations[i]
                return True
        return False


def load_config(config_path: Optional[Path] = None) -> AppConfig:
    """
    設定ファイルを読み込みます。
    
    Args:
        config_path: 設定ファイルのパス（省略時はデフォルト）
    
    Returns:
        AppConfig インスタンス
    """
    path = config_path or DEFAULT_CONFIG_PATH
    
    if not path.exists():
        logger.warning(f"Config file not found: {path}. Using defaults.")
        return AppConfig()
    
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        
        # サーバー設定
        server_data = data.get('server', {}) or {}
        server = ServerConfig(
            host=server_data.get('host', '0.0.0.0'),
            port=_as_int(server_data, 'port', 5000, minimum=1),
            default_wait=_as_bool(server_data, 'default_wait', True),
        )
        
        # Bluetooth設定
        bt_data = data.get('bluetooth', {}) or {}
        bluetooth = BluetoothConfig(
            scan_timeout=_as_float(bt_data, 'scan_timeout', 10.0, minimum=0.0),
            connection_timeout=_as_float(bt_data, 'connection_timeout', 15.0, minimum=0.0),
            discovery_timeout=_as_float(bt_data, 'discovery_timeout', 5.0, minimum=0.0),
            max_attempts=_as_int(bt_data, 'max_attempts', 3, minimum=1),
            retry_backoff=_as_float(
                bt_data, 'retry_backoff', 1.0, minimum=0.0, allow_zero=True
            ),
            max_concurrency=_as_int(bt_data, 'max_concurrency', 1, minimum=1),
            command_interval=_as_float(
                bt_data, 'command_interval', 0.5, minimum=0.0, allow_zero=True
            ),
        )
        
        # Base Station設定
        base_stations = []
        for bs_data in data.get('base_stations', []):
            bs = BaseStationConfig(
                id=bs_data.get('id', ''),
                name=bs_data.get('name', ''),
                mac_address=bs_data.get('mac_address', '')
            )
            if bs.id and bs.mac_address:
                base_stations.append(bs)
        
        config = AppConfig(
            server=server,
            bluetooth=bluetooth,
            base_stations=base_stations
        )
        
        logger.info(f"Loaded config from {path}")
        logger.info(f"  Server: {server.host}:{server.port}")
        logger.info(f"  Base Stations: {len(base_stations)}")
        
        return config
        
    except Exception as e:
        logger.error(f"Error loading config: {e}")
        return AppConfig()


def save_config(config: AppConfig, config_path: Optional[Path] = None) -> bool:
    """
    設定ファイルを保存します。
    
    Args:
        config: 保存する設定
        config_path: 保存先パス（省略時はデフォルト）
    
    Returns:
        成功した場合True
    """
    path = config_path or DEFAULT_CONFIG_PATH
    
    try:
        data = {
            'server': {
                'host': config.server.host,
                'port': config.server.port,
                'default_wait': config.server.default_wait,
            },
            'bluetooth': {
                'scan_timeout': config.bluetooth.scan_timeout,
                'connection_timeout': config.bluetooth.connection_timeout,
                'discovery_timeout': config.bluetooth.discovery_timeout,
                'max_attempts': config.bluetooth.max_attempts,
                'retry_backoff': config.bluetooth.retry_backoff,
                'max_concurrency': config.bluetooth.max_concurrency,
                'command_interval': config.bluetooth.command_interval,
            },
            'base_stations': [bs.to_dict() for bs in config.base_stations]
        }
        
        with open(path, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
        
        logger.info(f"Saved config to {path}")
        return True
        
    except Exception as e:
        logger.error(f"Error saving config: {e}")
        return False


def _as_float(
    data: dict,
    key: str,
    default: float,
    minimum: float,
    allow_zero: bool = False,
) -> float:
    """設定値を float にする。欠落・不正・下限未満は既定値。

    minimum が 0 のタイムアウトは 0 自体も既定に戻す。待ち時間は allow_zero=True
    で 0（待たない）を許可する。
    """
    if key not in data or data.get(key) is None:
        return default
    value = data[key]
    if isinstance(value, bool):
        logger.warning("Invalid %s=%r, using %s", key, value, default)
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r, using %s", key, value, default)
        return default
    if number < minimum or (number == 0.0 and not allow_zero):
        logger.warning("%s=%s is out of range, using %s", key, number, default)
        return default
    return number


def _as_int(data: dict, key: str, default: int, minimum: int) -> int:
    """設定値を int にする。欠落・不正・下限未満は既定値。"""
    if key not in data or data.get(key) is None:
        return default
    value = data[key]
    if isinstance(value, bool):
        logger.warning("Invalid %s=%r, using %s", key, value, default)
        return default
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r, using %s", key, value, default)
        return default
    if number < minimum:
        logger.warning("%s=%s is out of range, using %s", key, number, default)
        return default
    return number


def _as_bool(data: dict, key: str, default: bool) -> bool:
    """設定値を bool にする。"""
    if key not in data or data.get(key) is None:
        return default
    value = data[key]
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    logger.warning("Invalid %s=%r, using %s", key, value, default)
    return default
