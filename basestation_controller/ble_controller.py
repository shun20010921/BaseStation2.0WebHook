"""
BLE Controller for VIVE Base Station 2.0

Bluetooth Low Energy を使用してBase Stationの電源を制御します。
Windows / Linux 両対応（bleak 2.x）

接続は次の順で行います。
1. BleakScanner.find_device_by_address で BLEDevice を解決する
2. 見つからなければ同じタイムアウトでもう一度スキャンする
3. 解決した BLEDevice を BleakClient に渡して GATT へ書き込む
4. 失敗したら短いバックオフのあと、設定回数までやり直す

bleak-retry-connector は使っていません。4.6.3 以降は bleak 3 以上が必要で、
bleak 2 向けの 4.6.2 も Home Assistant の常駐スキャナと D-Bus サービスキャッシュを
前提にしています。このプロセスは接続のたびにスキャンして切るため、試行回数・
バックオフ・例外の repr を自前で制御します。
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

logger = logging.getLogger(__name__)


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
            "rssi": self.rssi,
        }


# VIVE Base Station 2.0 BLE GATT UUIDs
POWER_SERVICE_UUID = "00001523-1212-efde-1523-785feabcd124"
POWER_CHARACTERISTIC_UUID = "00001525-1212-efde-1523-785feabcd124"

# Power commands
POWER_ON = b"\x01"
POWER_OFF = b"\x00"

# Base Station device name prefix
BASESTATION_NAME_PREFIXES = ["LHB-", "HTC BS"]


class DeviceNotFoundError(Exception):
    """再スキャン後も Base Station をアドバタイズから解決できなかった。"""


@dataclass
class CommandResult:
    """1台分の電源コマンド結果。"""

    mac_address: str
    success: bool
    attempts: int
    error: Optional[str] = None

    @property
    def status(self) -> str:
        return "success" if self.success else "failed"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "attempts": self.attempts,
            "error": self.error,
        }


def format_exception(exc: BaseException) -> str:
    """例外の型と repr を返す。str(exc) が空でもログと API で原因が分かる。"""
    return f"{type(exc).__name__}: {exc!r}"


class BLEController:
    """
    VIVE Base Station 2.0 BLE Controller

    Bluetooth Low Energy を使用してBase Stationの電源を制御します。

    max_concurrency の既定は 1 です。CSR Bluetooth 4.0 ドングル
    (USB ID 0a12:0001) は接続試行を重ねると 'device disconnected' や
    空の BleakError になりやすく、スキャンと接続も同時に安定しません。
    台ごとに直列で接続し、台間に command_interval を空けます。
    同時接続が安定するアダプタでは max_concurrency を 2 以上にしてください。
    """

    def __init__(
        self,
        scan_timeout: float = 10.0,
        connection_timeout: float = 15.0,
        discovery_timeout: float = 5.0,
        max_attempts: int = 3,
        retry_backoff: float = 1.0,
        max_concurrency: int = 1,
        command_interval: float = 0.5,
    ):
        """
        Initialize BLE Controller

        Args:
            scan_timeout: 一覧スキャンのタイムアウト（秒）
            connection_timeout: BLE接続のタイムアウト（秒）
            discovery_timeout: 接続前のアドレス解決タイムアウト（秒）
            max_attempts: 1台あたりの試行回数
            retry_backoff: 失敗後の待ち時間の基準（秒）。試行番号を掛ける
            max_concurrency: 同時に接続を試みる台数。1 なら順次
            command_interval: 順次実行のとき、台と台の間に空ける秒数
        """
        self.scan_timeout = _positive(scan_timeout, 10.0)
        self.connection_timeout = _positive(connection_timeout, 15.0)
        self.discovery_timeout = _positive(discovery_timeout, 5.0)
        self.max_attempts = max(1, int(max_attempts))
        self.retry_backoff = max(0.0, float(retry_backoff))
        self.max_concurrency = max(1, int(max_concurrency))
        self.command_interval = max(0.0, float(command_interval))

    @classmethod
    def from_settings(cls, settings: Any) -> "BLEController":
        """BluetoothConfig など同じ属性を持つオブジェクトから生成する。"""
        return cls(
            scan_timeout=settings.scan_timeout,
            connection_timeout=settings.connection_timeout,
            discovery_timeout=settings.discovery_timeout,
            max_attempts=settings.max_attempts,
            retry_backoff=settings.retry_backoff,
            max_concurrency=settings.max_concurrency,
            command_interval=settings.command_interval,
        )

    async def scan_base_stations(self) -> List[BaseStation]:
        """
        周囲のVIVE Base Station 2.0をスキャンします。

        Returns:
            検出されたBase Stationのリスト
        """
        logger.info(f"Scanning for Base Stations (timeout: {self.scan_timeout}s)...")

        base_stations: List[BaseStation] = []

        try:
            # bleak 2 では RSSI は BLEDevice ではなく AdvertisementData にある
            discovered = await BleakScanner.discover(
                timeout=self.scan_timeout,
                return_adv=True,
            )

            for device, adv in discovered.values():
                device_name = device.name or getattr(adv, "local_name", None) or ""
                if any(device_name.startswith(prefix) for prefix in BASESTATION_NAME_PREFIXES):
                    rssi = getattr(adv, "rssi", None)
                    bs = BaseStation(
                        mac_address=device.address,
                        name=device_name,
                        rssi=rssi,
                    )
                    base_stations.append(bs)
                    logger.info(f"Found Base Station: {bs.name} ({bs.mac_address})")

            logger.info(f"Scan complete. Found {len(base_stations)} Base Station(s)")

        except Exception as e:
            logger.error(f"Error during BLE scan: {format_exception(e)}")
            raise

        return base_stations

    async def _resolve_device(self, mac_address: str) -> BLEDevice:
        """アドレスを BLEDevice に解決する。未検出なら一度だけ再スキャンする。"""
        for lookup in (1, 2):
            logger.info(
                "Looking up %s (discovery %s/2, timeout=%ss)",
                mac_address,
                lookup,
                self.discovery_timeout,
            )
            device = await BleakScanner.find_device_by_address(
                mac_address,
                timeout=self.discovery_timeout,
            )
            if device is not None:
                logger.info("Resolved %s as %s", mac_address, device)
                return device
            if lookup == 1:
                logger.warning(
                    "Device %s was not found; rescanning",
                    mac_address,
                )

        raise DeviceNotFoundError(
            f"Device with address {mac_address} was not found after rescan"
        )

    async def _send_power_command_once(self, mac_address: str, command: bytes) -> None:
        """解決済みデバイスへ1回だけ接続して電源コマンドを書く。"""
        device = await self._resolve_device(mac_address)
        logger.info("Connecting to %s using resolved BLEDevice", mac_address)

        async with BleakClient(device, timeout=self.connection_timeout) as client:
            if not client.is_connected:
                raise ConnectionError(f"Failed to connect to {mac_address}")

            logger.info("Connected to %s", mac_address)
            await client.write_gatt_char(
                POWER_CHARACTERISTIC_UUID,
                command,
                response=False,
            )

        action = "ON" if command == POWER_ON else "OFF"
        logger.info("Power %s command sent to %s", action, mac_address)

    async def _send_power_command(
        self,
        mac_address: str,
        command: bytes,
        semaphore: Optional[asyncio.Semaphore] = None,
    ) -> CommandResult:
        """
        1台に電源コマンドを送る。失敗時はバックオフして max_attempts までやり直す。

        semaphore がある場合、スキャンと接続の間だけ取得する。バックオフ中は
        手放すので、同時実行数が 2 以上のとき他の台がアダプタを使える。
        """
        last_error: Optional[str] = None

        for attempt in range(1, self.max_attempts + 1):
            logger.info(
                "Sending power command to %s (attempt %s/%s)",
                mac_address,
                attempt,
                self.max_attempts,
            )
            try:
                if semaphore is None:
                    await self._send_power_command_once(mac_address, command)
                else:
                    async with semaphore:
                        await self._send_power_command_once(mac_address, command)
                logger.info(
                    "Power command succeeded for %s on attempt %s/%s",
                    mac_address,
                    attempt,
                    self.max_attempts,
                )
                return CommandResult(
                    mac_address=mac_address,
                    success=True,
                    attempts=attempt,
                )
            except Exception as exc:
                last_error = format_exception(exc)
                logger.warning(
                    "Attempt %s/%s failed for %s: type=%s str=%r repr=%r",
                    attempt,
                    self.max_attempts,
                    mac_address,
                    type(exc).__name__,
                    str(exc),
                    exc,
                )
                if attempt < self.max_attempts and self.retry_backoff > 0:
                    delay = self.retry_backoff * attempt
                    logger.info(
                        "Backing off %.2fs before retrying %s",
                        delay,
                        mac_address,
                    )
                    await asyncio.sleep(delay)

        logger.error(
            "Giving up on %s after %s attempt(s): %s",
            mac_address,
            self.max_attempts,
            last_error,
        )
        return CommandResult(
            mac_address=mac_address,
            success=False,
            attempts=self.max_attempts,
            error=last_error,
        )

    async def power_on(self, mac_address: str) -> CommandResult:
        """Base Stationの電源をONにします。"""
        return await self._send_power_command(mac_address, POWER_ON)

    async def power_off(self, mac_address: str) -> CommandResult:
        """Base Stationの電源をOFF（スリープ）にします。"""
        return await self._send_power_command(mac_address, POWER_OFF)

    async def _power_many(
        self,
        mac_addresses: List[str],
        command: bytes,
    ) -> Dict[str, CommandResult]:
        """
        複数台へ電源コマンドを送る。

        max_concurrency が 1 のときは順に実行し、台間で command_interval 待つ。
        2 以上のときは Semaphore で同時接続数を制限して重ねる。
        """
        if not mac_addresses:
            return {}

        action = "ON" if command == POWER_ON else "OFF"
        logger.info(
            "Power %s for %s base station(s), concurrency=%s",
            action,
            len(mac_addresses),
            self.max_concurrency,
        )

        # イベントループごとに作る。プロセス内で asyncio.run を繰り返すため、
        # __init__ で作った Semaphore は次のループで使えない。
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def run_one(mac: str) -> CommandResult:
            return await self._send_power_command(mac, command, semaphore)

        if self.max_concurrency <= 1:
            ordered: Dict[str, CommandResult] = {}
            for index, mac in enumerate(mac_addresses):
                ordered[mac] = await run_one(mac)
                if self.command_interval > 0 and index < len(mac_addresses) - 1:
                    await asyncio.sleep(self.command_interval)
            return ordered

        results = await asyncio.gather(*(run_one(mac) for mac in mac_addresses))
        return {mac: result for mac, result in zip(mac_addresses, results)}

    async def power_on_all(self, mac_addresses: List[str]) -> Dict[str, CommandResult]:
        """複数のBase Stationの電源をONにします。"""
        return await self._power_many(mac_addresses, POWER_ON)

    async def power_off_all(self, mac_addresses: List[str]) -> Dict[str, CommandResult]:
        """複数のBase Stationの電源をOFF（スリープ）にします。"""
        return await self._power_many(mac_addresses, POWER_OFF)


def _positive(value: float, default: float) -> float:
    """タイムアウトが 0 以下なら既定値に戻す。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number <= 0:
        return default
    return number


# Convenience functions for synchronous usage
def scan_base_stations_sync(timeout: float = 10.0) -> List[BaseStation]:
    """スキャンの同期バージョン"""
    controller = BLEController(scan_timeout=timeout)
    return asyncio.run(controller.scan_base_stations())


def power_on_sync(mac_address: str) -> bool:
    """電源ONの同期バージョン"""
    controller = BLEController()
    return asyncio.run(controller.power_on(mac_address)).success


def power_off_sync(mac_address: str) -> bool:
    """電源OFFの同期バージョン"""
    controller = BLEController()
    return asyncio.run(controller.power_off(mac_address)).success
