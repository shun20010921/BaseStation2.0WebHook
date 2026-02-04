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


@dataclass
class BluetoothConfig:
    """Bluetooth設定"""
    scan_timeout: float = 10.0
    connection_timeout: float = 15.0


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
        server_data = data.get('server', {})
        server = ServerConfig(
            host=server_data.get('host', '0.0.0.0'),
            port=server_data.get('port', 5000)
        )
        
        # Bluetooth設定
        bt_data = data.get('bluetooth', {})
        bluetooth = BluetoothConfig(
            scan_timeout=bt_data.get('scan_timeout', 10.0),
            connection_timeout=bt_data.get('connection_timeout', 15.0)
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
                'port': config.server.port
            },
            'bluetooth': {
                'scan_timeout': config.bluetooth.scan_timeout,
                'connection_timeout': config.bluetooth.connection_timeout
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
