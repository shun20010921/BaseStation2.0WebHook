"""bleak をモックした電源コマンドのテスト。"""

import asyncio
import logging

import pytest
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakError

from basestation_controller import ble_controller
from basestation_controller.ble_controller import (
    POWER_CHARACTERISTIC_UUID,
    POWER_OFF,
    POWER_ON,
    BLEController,
)


MAC_A = "AA:BB:CC:DD:EE:01"
MAC_B = "AA:BB:CC:DD:EE:02"
MAC_C = "AA:BB:CC:DD:EE:03"
MAC_D = "AA:BB:CC:DD:EE:04"


def _device(mac: str, name: str = "LHB-TEST") -> BLEDevice:
    return BLEDevice(mac, name, {"path": f"/org/bluez/hci0/dev_{mac.replace(':', '_')}"})


class ScriptedBle:
    def __init__(self):
        self.find_script = []
        self.connect_script = []
        self.find_calls = []
        self.client_calls = []
        self.writes = []
        self.active = 0
        self.peak = 0
        self.hold_seconds = 0.0

    def install(self, monkeypatch):
        monkeypatch.setattr(
            ble_controller.BleakScanner,
            "find_device_by_address",
            self.find_device_by_address,
        )
        monkeypatch.setattr(ble_controller, "BleakClient", self.make_client)

    async def find_device_by_address(self, address, timeout=10.0, **kwargs):
        self.find_calls.append({"address": address, "timeout": timeout, "kwargs": kwargs})
        if not self.find_script:
            raise AssertionError(f"unexpected lookup for {address}")
        item = self.find_script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def make_client(self, target, timeout=30, **kwargs):
        self.client_calls.append({"target": target, "timeout": timeout, "kwargs": kwargs})
        if self.connect_script:
            outcome = self.connect_script.pop(0)
        else:
            outcome = None
        parent = self

        class _Client:
            def __init__(self):
                self.is_connected = True
                self.target = target

            async def __aenter__(self):
                parent.active += 1
                parent.peak = max(parent.peak, parent.active)
                if parent.hold_seconds:
                    await asyncio.sleep(parent.hold_seconds)
                if isinstance(outcome, Exception):
                    parent.active -= 1
                    raise outcome
                return self

            async def __aexit__(self, exc_type, exc, tb):
                if not isinstance(outcome, Exception):
                    parent.active -= 1
                return False

            async def write_gatt_char(self, uuid, data, response=None):
                parent.writes.append((uuid, bytes(data), response))

        return _Client()


def _controller(**kwargs) -> BLEController:
    params = dict(
        scan_timeout=1.0,
        connection_timeout=7.0,
        discovery_timeout=2.5,
        max_attempts=3,
        retry_backoff=0.0,
        max_concurrency=1,
        command_interval=0.0,
    )
    params.update(kwargs)
    return BLEController(**params)


def test_connects_with_resolved_device_not_mac_string(monkeypatch):
    script = ScriptedBle()
    device = _device(MAC_A)
    script.find_script = [device]
    script.install(monkeypatch)
    controller = _controller()

    result = asyncio.run(controller.power_on(MAC_A))

    assert result.success is True
    assert result.attempts == 1
    assert result.error is None
    assert script.find_calls[0]["address"] == MAC_A
    assert script.find_calls[0]["timeout"] == 2.5
    assert script.client_calls[0]["target"] is device
    assert not isinstance(script.client_calls[0]["target"], str)
    assert script.client_calls[0]["timeout"] == 7.0
    assert script.writes == [(POWER_CHARACTERISTIC_UUID, POWER_ON, False)]


def test_not_found_triggers_rescan_before_connect(monkeypatch, caplog):
    script = ScriptedBle()
    device = _device(MAC_A)
    script.find_script = [None, device]
    script.install(monkeypatch)
    controller = _controller()

    with caplog.at_level(logging.WARNING):
        result = asyncio.run(controller.power_on(MAC_A))

    assert result.success is True
    assert result.attempts == 1
    assert len(script.find_calls) == 2
    assert len(script.client_calls) == 1
    assert "rescanning" in caplog.text


def test_retries_after_empty_exception_and_logs_repr(monkeypatch, caplog):
    script = ScriptedBle()
    device = _device(MAC_A)
    script.find_script = [device, device]
    script.connect_script = [BleakError()]
    script.install(monkeypatch)
    controller = _controller(max_attempts=3, retry_backoff=1.0)
    slept = []

    async def fake_sleep(delay):
        slept.append(delay)

    monkeypatch.setattr(ble_controller.asyncio, "sleep", fake_sleep)

    with caplog.at_level(logging.INFO):
        result = asyncio.run(controller.power_on(MAC_A))

    assert result.success is True
    assert result.attempts == 2
    assert slept == [1.0]
    assert "Attempt 1/3 failed" in caplog.text
    assert "type=BleakError" in caplog.text
    assert "repr=BleakError()" in caplog.text
    assert script.writes == [(POWER_CHARACTERISTIC_UUID, POWER_ON, False)]


def test_gives_up_after_rescan_misses(monkeypatch):
    script = ScriptedBle()
    script.find_script = [None, None, None, None]
    script.install(monkeypatch)
    controller = _controller(max_attempts=2, retry_backoff=0.0)

    result = asyncio.run(controller.power_off(MAC_B))

    assert result.success is False
    assert result.attempts == 2
    assert result.error is not None
    assert "DeviceNotFoundError" in result.error
    assert "not found after rescan" in result.error
    assert script.client_calls == []
    assert script.writes == []


def test_power_off_writes_off_byte(monkeypatch):
    script = ScriptedBle()
    script.find_script = [_device(MAC_A)]
    script.install(monkeypatch)

    result = asyncio.run(_controller().power_off(MAC_A))

    assert result.success is True
    assert script.writes == [(POWER_CHARACTERISTIC_UUID, POWER_OFF, False)]


def test_sequential_gap_and_per_base_backoff(monkeypatch):
    script = ScriptedBle()
    script.find_script = [_device(MAC_A), _device(MAC_A), _device(MAC_B)]
    script.connect_script = [BleakError("failed to discover services, device disconnected")]
    script.install(monkeypatch)
    controller = _controller(
        max_attempts=3,
        retry_backoff=0.4,
        max_concurrency=1,
        command_interval=0.5,
    )
    slept = []

    async def fake_sleep(delay):
        slept.append(delay)

    monkeypatch.setattr(ble_controller.asyncio, "sleep", fake_sleep)

    results = asyncio.run(controller.power_on_all([MAC_A, MAC_B]))

    assert results[MAC_A].success is True
    assert results[MAC_A].attempts == 2
    assert results[MAC_B].success is True
    assert results[MAC_B].attempts == 1
    # 1台目の失敗後 0.4s、台の間 0.5s。2台目は初回成功なので追加バックオフはない。
    assert slept == [0.4, 0.5]


def test_concurrency_is_limited(monkeypatch):
    script = ScriptedBle()
    script.hold_seconds = 0.05
    macs = [MAC_A, MAC_B, MAC_C, MAC_D]
    script.find_script = [_device(mac) for mac in macs]
    script.install(monkeypatch)
    controller = _controller(max_concurrency=2, max_attempts=1, command_interval=0.0)

    results = asyncio.run(controller.power_on_all(macs))

    assert all(result.success for result in results.values())
    assert script.peak == 2


def test_concurrency_one_never_overlaps(monkeypatch):
    script = ScriptedBle()
    script.hold_seconds = 0.02
    macs = [MAC_A, MAC_B, MAC_C]
    script.find_script = [_device(mac) for mac in macs]
    script.install(monkeypatch)
    controller = _controller(max_concurrency=1, max_attempts=1, command_interval=0.0)

    asyncio.run(controller.power_on_all(macs))

    assert script.peak == 1


def test_scan_reads_rssi_from_advertisement(monkeypatch):
    captured = {}

    async def discover(timeout=5.0, return_adv=False, **kwargs):
        captured["timeout"] = timeout
        captured["return_adv"] = return_adv
        named = BLEDevice("AA:BB:CC:DD:EE:10", None, {})
        keyboard = BLEDevice("AA:BB:CC:DD:EE:11", "Keyboard", {})
        htc = BLEDevice("AA:BB:CC:DD:EE:12", "HTC BS 01", {})
        return {
            "a": (named, AdvertisementData("LHB-ABCDEF", {}, {}, [], None, -62, ())),
            "b": (keyboard, AdvertisementData("Keyboard", {}, {}, [], None, -40, ())),
            "c": (htc, AdvertisementData(None, {}, {}, [], None, -71, ())),
        }

    monkeypatch.setattr(ble_controller.BleakScanner, "discover", discover)
    stations = asyncio.run(_controller(scan_timeout=4.0).scan_base_stations())

    assert captured == {"timeout": 4.0, "return_adv": True}
    by_mac = {station.mac_address: station for station in stations}
    assert set(by_mac) == {"AA:BB:CC:DD:EE:10", "AA:BB:CC:DD:EE:12"}
    assert by_mac["AA:BB:CC:DD:EE:10"].name == "LHB-ABCDEF"
    assert by_mac["AA:BB:CC:DD:EE:10"].rssi == -62
    assert by_mac["AA:BB:CC:DD:EE:12"].rssi == -71


def test_partial_failure_marks_only_that_base(monkeypatch):
    script = ScriptedBle()
    script.find_script = [_device(MAC_A), None, None, None, None, None, None]
    script.install(monkeypatch)
    controller = _controller(max_attempts=3, retry_backoff=0.0, command_interval=0.0)

    results = asyncio.run(controller.power_off_all([MAC_A, MAC_B]))

    assert results[MAC_A].status == "success"
    assert results[MAC_B].status == "failed"
    assert results[MAC_B].attempts == 3
    assert "DeviceNotFoundError" in results[MAC_B].error
