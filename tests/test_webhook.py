"""Flask の電源 API。BLE はコントローラーメソッドごと差し替える。"""

import asyncio
import threading
import time

from basestation_controller.ble_controller import CommandResult
from basestation_controller.config import AppConfig, BaseStationConfig, ServerConfig
from basestation_controller.webhook_server import create_app


MAC_A = "AA:BB:CC:DD:EE:01"
MAC_B = "AA:BB:CC:DD:EE:02"


def _config(default_wait=True, stations=True) -> AppConfig:
    config = AppConfig(server=ServerConfig(default_wait=default_wait))
    if stations:
        config.base_stations = [
            BaseStationConfig(id="bs1", name="LHB-ONE", mac_address=MAC_A),
            BaseStationConfig(id="bs2", name="LHB-TWO", mac_address=MAC_B),
        ]
    return config


def _client(config=None):
    application = create_app(config or _config())
    application.config["TESTING"] = True
    return application.test_client(), application


def _ok_map(macs, attempts=1):
    return {mac: CommandResult(mac, True, attempts) for mac in macs}


def _wait_job(client, job_id, timeout=2.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        response = client.get(f"/api/basestations/jobs/{job_id}")
        last = response.get_json()
        if last and last.get("state") in {"succeeded", "failed"}:
            return last
        time.sleep(0.02)
    return last


def test_status_reports_retry_settings():
    client, _app = _client()
    body = client.get("/api/status").get_json()

    assert body["status"] == "running"
    assert body["version"] == "1.1.0"
    assert body["base_stations_registered"] == 2
    assert body["default_wait"] is True
    assert body["bluetooth"]["max_attempts"] == 3
    assert body["bluetooth"]["max_concurrency"] == 1


def test_sync_all_on_returns_200_with_per_base_details():
    client, app = _client()

    async def power_on_all(macs):
        return _ok_map(macs, attempts=2)

    app.config["BLE_CONTROLLER"].power_on_all = power_on_all
    response = client.post("/api/basestations/all/on")
    body = response.get_json()

    assert response.status_code == 200
    assert body["success"] is True
    assert body["action"] == "power_on_all"
    assert body["total"] == 2
    assert body["successful"] == 2
    assert body["failed"] == 0
    assert body["results"] == {MAC_A: "success", MAC_B: "success"}
    assert body["details"][MAC_A]["attempts"] == 2
    assert body["details"][MAC_A]["error"] is None


def test_sync_all_off_returns_503_when_any_base_fails():
    client, app = _client()

    async def power_off_all(macs):
        return {
            macs[0]: CommandResult(macs[0], True, 1),
            macs[1]: CommandResult(macs[1], False, 3, "BleakError: BleakError()"),
        }

    app.config["BLE_CONTROLLER"].power_off_all = power_off_all
    response = client.post("/api/basestations/all/off")
    body = response.get_json()

    assert response.status_code == 503
    assert body["success"] is False
    assert body["successful"] == 1
    assert body["failed"] == 1
    assert body["results"][MAC_A] == "success"
    assert body["results"][MAC_B] == "failed"
    assert body["details"][MAC_B]["attempts"] == 3
    assert "BleakError" in body["details"][MAC_B]["error"]


def test_single_station_failure_is_503():
    client, app = _client()

    async def power_on(mac):
        return CommandResult(mac, False, 3, "BleakError: BleakError()")

    app.config["BLE_CONTROLLER"].power_on = power_on
    response = client.post("/api/basestations/bs1/on")
    body = response.get_json()

    assert response.status_code == 503
    assert body["success"] is False
    assert body["station_id"] == "bs1"
    assert body["mac_address"] == MAC_A
    assert body["attempts"] == 3
    assert "BleakError()" in body["error"]


def test_unknown_station_stays_404():
    client, _app = _client()
    response = client.post("/api/basestations/missing/off")
    assert response.status_code == 404


def test_all_without_stations_is_400_even_if_async():
    client, _app = _client(_config(stations=False))
    response = client.post("/api/basestations/all/on?wait=false")
    assert response.status_code == 400
    assert response.get_json()["success"] is False


def test_wait_false_returns_202_before_the_job_finishes():
    client, app = _client()
    started = threading.Event()
    release = threading.Event()

    async def power_on_all(macs):
        started.set()
        await asyncio.to_thread(release.wait, 3)
        return _ok_map(macs)

    app.config["BLE_CONTROLLER"].power_on_all = power_on_all

    try:
        started_at = time.perf_counter()
        response = client.post("/api/basestations/all/on?wait=false")
        elapsed = time.perf_counter() - started_at
        body = response.get_json()

        assert response.status_code == 202
        assert elapsed < 1.0
        assert body["accepted"] is True
        assert body["status"] == "accepted"
        assert body["action"] == "power_on_all"
        assert body["total"] == 2
        assert body["status_url"] == f"/api/basestations/jobs/{body['job_id']}"
        assert started.wait(2)
    finally:
        release.set()

    job = _wait_job(client, body["job_id"])
    assert job["state"] == "succeeded"
    assert job["http_status"] == 200
    assert job["result"]["results"][MAC_A] == "success"
    assert job["result"]["success"] is True


def test_async_query_and_json_wait_false_are_accepted():
    client, app = _client()
    calls = []

    async def power_off_all(macs):
        calls.append(list(macs))
        return _ok_map(macs)

    app.config["BLE_CONTROLLER"].power_off_all = power_off_all

    query = client.post("/api/basestations/all/off?async=true")
    assert query.status_code == 202
    body_job = _wait_job(client, query.get_json()["job_id"])
    assert body_job["state"] == "succeeded"

    posted = client.post("/api/basestations/all/off", json={"wait": False})
    assert posted.status_code == 202
    assert _wait_job(client, posted.get_json()["job_id"])["action"] == "power_off_all"
    assert calls == [[MAC_A, MAC_B], [MAC_A, MAC_B]]


def test_default_wait_false_can_be_overridden():
    client, app = _client(_config(default_wait=False))
    calls = []

    async def power_on_all(macs):
        calls.append("ran")
        return _ok_map(macs)

    app.config["BLE_CONTROLLER"].power_on_all = power_on_all

    background = client.post("/api/basestations/all/on")
    assert background.status_code == 202
    assert _wait_job(client, background.get_json()["job_id"])["state"] == "succeeded"

    explicit = client.post("/api/basestations/all/on?wait=true")
    assert explicit.status_code == 200
    assert explicit.get_json()["success"] is True
    assert calls == ["ran", "ran"]


def test_background_failure_is_visible_on_the_job():
    client, app = _client()

    async def power_on_all(macs):
        return {
            macs[0]: CommandResult(macs[0], False, 3, "DeviceNotFoundError: missing"),
            macs[1]: CommandResult(macs[1], True, 1),
        }

    app.config["BLE_CONTROLLER"].power_on_all = power_on_all
    response = client.post("/api/basestations/all/on?wait=0")
    assert response.status_code == 202
    job = _wait_job(client, response.get_json()["job_id"])

    assert job["state"] == "failed"
    assert job["http_status"] == 503
    assert job["result"]["success"] is False
    assert job["result"]["results"][MAC_A] == "failed"


def test_unknown_job_is_404():
    client, _app = _client()
    response = client.get("/api/basestations/jobs/does-not-exist")
    assert response.status_code == 404


def test_background_jobs_do_not_use_the_adapter_at_the_same_time():
    client, app = _client()
    state = {"current": 0, "peak": 0}
    guard = threading.Lock()

    async def power_on_all(macs):
        with guard:
            state["current"] += 1
            state["peak"] = max(state["peak"], state["current"])
        await asyncio.sleep(0.15)
        with guard:
            state["current"] -= 1
        return _ok_map(macs)

    app.config["BLE_CONTROLLER"].power_on_all = power_on_all
    first = client.post("/api/basestations/all/on?async=1")
    second = client.post("/api/basestations/all/on?async=1")
    assert first.status_code == 202
    assert second.status_code == 202

    job_a = _wait_job(client, first.get_json()["job_id"])
    job_b = _wait_job(client, second.get_json()["job_id"], timeout=2.5)
    assert job_a["state"] == "succeeded"
    assert job_b["state"] == "succeeded"
    assert state["peak"] == 1


def test_single_station_background_job_uses_the_same_body():
    client, app = _client()

    async def power_off(mac):
        return CommandResult(mac, True, 1)

    app.config["BLE_CONTROLLER"].power_off = power_off
    response = client.post("/api/basestations/bs2/off?wait=false")
    body = response.get_json()

    assert response.status_code == 202
    assert body["station_id"] == "bs2"
    job = _wait_job(client, body["job_id"])
    assert job["state"] == "succeeded"
    assert job["result"]["success"] is True
    assert job["result"]["station_id"] == "bs2"
    assert job["result"]["mac_address"] == MAC_B
    assert job["result"]["action"] == "power_off"
