"""
BLE Controller for VIVE Base Station 2.0

Bluetooth Low Energy を使用してBase Stationの電源を制御します。
Windows / Linux 両対応（bleak ライブラリ使用）
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import List, Optional
from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

logger = logging.getLogger(__name__)

# VIVE Base Station 2.0 BLE GATT UUIDs
POWER_SERVICE_UUID = "00001523-1212-efde-1523-785feabcd124"
POWER_CHARACTERISTIC_UUID = "00001525-1212-efde-1523-785feabcd124"

# Power commands
POWER_ON = b'\x01'
POWER_OFF = b'\x00'

# Base Station device name prefix
BASESTATION_NAME_PREFIXES = ["LHB-", "HTC BS"]


@dataclass
class BaseStation:
    """Base Station information"""
    mac_address: str
    name: str
    rssi: Optional[int] = None
    
    def to_dict(self) -> dict:
        return {
            "mac_address": self.mac_address,
            "name": self.name,
            "rssi": self.rssi
        }


class BLEController:
    """
    VIVE Base Station 2.0 BLE Controller
    
    Bluetooth Low Energy を使用してBase Stationの電源を制御します。
    """
    
    def __init__(self, scan_timeout: float = 10.0, connection_timeout: float = 15.0):
        """
        Initialize BLE Controller
        
        Args:
            scan_timeout: BLEスキャンのタイムアウト（秒）
            connection_timeout: BLE接続のタイムアウト（秒）
        """
        self.scan_timeout = scan_timeout
        self.connection_timeout = connection_timeout
    
    async def scan_base_stations(self) -> List[BaseStation]:
        """
        周囲のVIVE Base Station 2.0をスキャンします。
        
        Returns:
            検出されたBase Stationのリスト
        """
        logger.info(f"Scanning for Base Stations (timeout: {self.scan_timeout}s)...")
        
        base_stations: List[BaseStation] = []
        
        try:
            devices: List[BLEDevice] = await BleakScanner.discover(
                timeout=self.scan_timeout,
                return_adv=False
            )
            
            for device in devices:
                device_name = device.name or ""
                # Base Station判定（名前のプレフィックスで判断）
                if any(device_name.startswith(prefix) for prefix in BASESTATION_NAME_PREFIXES):
                    bs = BaseStation(
                        mac_address=device.address,
                        name=device_name,
                        rssi=getattr(device, 'rssi', None)
                    )
                    base_stations.append(bs)
                    logger.info(f"Found Base Station: {bs.name} ({bs.mac_address})")
            
            logger.info(f"Scan complete. Found {len(base_stations)} Base Station(s)")
            
        except Exception as e:
            logger.error(f"Error during BLE scan: {e}")
            raise
        
        return base_stations
    
    async def _send_power_command(self, mac_address: str, command: bytes) -> bool:
        """
        Base Stationに電源コマンドを送信します。
        
        Args:
            mac_address: Base StationのMACアドレス
            command: 送信するコマンド（POWER_ON or POWER_OFF）
        
        Returns:
            成功した場合True
        """
        logger.info(f"Connecting to {mac_address}...")
        
        try:
            async with BleakClient(
                mac_address, 
                timeout=self.connection_timeout
            ) as client:
                if not client.is_connected:
                    logger.error(f"Failed to connect to {mac_address}")
                    return False
                
                logger.info(f"Connected to {mac_address}")
                
                # 電源コマンドを書き込み
                await client.write_gatt_char(
                    POWER_CHARACTERISTIC_UUID,
                    command,
                    response=False
                )
                
                action = "ON" if command == POWER_ON else "OFF"
                logger.info(f"Power {action} command sent to {mac_address}")
                
                return True
                
        except Exception as e:
            logger.error(f"Error sending command to {mac_address}: {e}")
            return False
    
    async def power_on(self, mac_address: str) -> bool:
        """
        Base Stationの電源をONにします。
        
        Args:
            mac_address: Base StationのMACアドレス
        
        Returns:
            成功した場合True
        """
        return await self._send_power_command(mac_address, POWER_ON)
    
    async def power_off(self, mac_address: str) -> bool:
        """
        Base Stationの電源をOFF（スリープ）にします。
        
        Args:
            mac_address: Base StationのMACアドレス
        
        Returns:
            成功した場合True
        """
        return await self._send_power_command(mac_address, POWER_OFF)
    
    async def power_on_all(self, mac_addresses: List[str]) -> dict:
        """
        複数のBase Stationの電源をONにします。
        
        Args:
            mac_addresses: MACアドレスのリスト
        
        Returns:
            各Base Stationの結果
        """
        results = {}
        for mac in mac_addresses:
            success = await self.power_on(mac)
            results[mac] = "success" if success else "failed"
            # 次のデバイスへの接続前に少し待機
            await asyncio.sleep(0.5)
        return results
    
    async def power_off_all(self, mac_addresses: List[str]) -> dict:
        """
        複数のBase Stationの電源をOFF（スリープ）にします。
        
        Args:
            mac_addresses: MACアドレスのリスト
        
        Returns:
            各Base Stationの結果
        """
        results = {}
        for mac in mac_addresses:
            success = await self.power_off(mac)
            results[mac] = "success" if success else "failed"
            # 次のデバイスへの接続前に少し待機
            await asyncio.sleep(0.5)
        return results


# Convenience functions for synchronous usage
def scan_base_stations_sync(timeout: float = 10.0) -> List[BaseStation]:
    """スキャンの同期バージョン"""
    controller = BLEController(scan_timeout=timeout)
    return asyncio.run(controller.scan_base_stations())


def power_on_sync(mac_address: str) -> bool:
    """電源ONの同期バージョン"""
    controller = BLEController()
    return asyncio.run(controller.power_on(mac_address))


def power_off_sync(mac_address: str) -> bool:
    """電源OFFの同期バージョン"""
    controller = BLEController()
    return asyncio.run(controller.power_off(mac_address))
