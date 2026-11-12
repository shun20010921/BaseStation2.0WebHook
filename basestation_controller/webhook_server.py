"""
Webhook Server for VIVE Base Station 2.0

Flask ベースのWebhookサーバー
Home Assistant / HomeBridge から呼び出し可能なAPIを提供します。
"""

import asyncio
import copy
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

from flask import Flask, jsonify, request

from . import __version__
from .ble_controller import BLEController, CommandResult, format_exception
from .config import AppConfig, BaseStationConfig, load_config, save_config

logger = logging.getLogger(__name__)

_FALSEY = {"0", "false", "no", "off"}
_TRUTHY = {"1", "true", "yes", "on"}
_MAX_JOBS = 32


class BleExecutor:
    """BlueZ 用にイベントループを1本だけ生かし続ける。

    bleak の BlueZ マネージャはループに紐づく。リクエストのたびに
    asyncio.run すると D-Bus 接続が作り直しになるので、プロセス内では
    このループを使い回す。トップレベルの電源操作はロックで直列化し、
    台ごとの同時接続数は BLEController.max_concurrency に任せる。
    """

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="ble-event-loop",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.call_soon(self._ready.set)
        self._loop.run_forever()

    def run(self, coro, before: Optional[Callable] = None):
        if not self._ready.wait(5):
            raise RuntimeError("BLE event loop did not start")
        with self._lock:
            if before is not None:
                before()
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            return future.result()


def create_app(config: AppConfig = None) -> Flask:
    """
    Flaskアプリケーションを作成します。

    Args:
        config: アプリケーション設定（省略時は設定ファイルから読み込み）

    Returns:
        Flask アプリケーション
    """
    app = Flask(__name__)

    if config is None:
        config = load_config()

    app.config["APP_CONFIG"] = config
    app.config["BLE_CONTROLLER"] = BLEController.from_settings(config.bluetooth)
    app.config["BLE_EXECUTOR"] = BleExecutor()
    app.config["JOBS"] = {}
    app.config["JOBS_LOCK"] = threading.Lock()

    def run_async(coro):
        """BLE操作を同期的に実行する。ほかの電源操作とは重ならない。"""
        return app.config["BLE_EXECUTOR"].run(coro)

    def should_wait() -> bool:
        """クエリまたは JSON の wait / async を見る。無指定なら設定の default_wait。"""
        args = {key.lower(): value for key, value in request.args.items()}
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            body = {}

        if "wait" in args:
            return _parse_bool(args.get("wait"), default=True)
        if "wait" in body:
            return _parse_bool(body.get("wait"), default=True)
        if "async" in args:
            return not _parse_bool(args.get("async"), default=False)
        if "async" in body:
            return not _parse_bool(body.get("async"), default=False)
        return app.config["APP_CONFIG"].server.default_wait

    def enqueue(
        action: str,
        operation: Callable,
        extra: Optional[dict] = None,
        present: Optional[Callable] = None,
    ):
        """電源操作を別スレッドで走らせ、すぐに 202 を返す。"""
        job_id = uuid.uuid4().hex
        job = {
            "job_id": job_id,
            "action": action,
            "state": "queued",
            "created_at": _utcnow(),
            "finished_at": None,
            "http_status": None,
            "result": None,
            "error": None,
        }
        jobs = app.config["JOBS"]
        jobs_lock = app.config["JOBS_LOCK"]
        with jobs_lock:
            jobs[job_id] = job
            while len(jobs) > _MAX_JOBS:
                oldest = next(iter(jobs))
                jobs.pop(oldest, None)

        def runner():
            try:
                detailed = app.config["BLE_EXECUTOR"].run(
                    operation(),
                    before=lambda: _update_job(
                        jobs, jobs_lock, job_id, state="running"
                    ),
                )
                if present is None:
                    body, status = _command_http_body(action, detailed)
                else:
                    body, status = present(detailed)
                state = "succeeded" if status == 200 else "failed"
                _update_job(
                    jobs,
                    jobs_lock,
                    job_id,
                    state=state,
                    finished_at=_utcnow(),
                    http_status=status,
                    result=body,
                )
                if status != 200:
                    logger.warning("Background %s finished with failures: %s", action, body)
            except Exception as exc:
                logger.exception("Background %s failed", action)
                _update_job(
                    jobs,
                    jobs_lock,
                    job_id,
                    state="failed",
                    finished_at=_utcnow(),
                    http_status=500,
                    error=format_exception(exc),
                )

        threading.Thread(
            target=runner,
            name=f"ble-{action}-{job_id[:8]}",
            daemon=True,
        ).start()

        payload = {
            "accepted": True,
            "status": "accepted",
            "action": action,
            "job_id": job_id,
            "status_url": f"/api/basestations/jobs/{job_id}",
        }
        if extra:
            payload.update(extra)
        logger.info("Accepted background %s as job %s", action, job_id)
        return jsonify(payload), 202

    # ===== API Endpoints =====

    @app.route("/api/status", methods=["GET"])
    def get_status():
        """サーバーステータスを取得"""
        current = app.config["APP_CONFIG"]
        controller = app.config["BLE_CONTROLLER"]
        return jsonify({
            "status": "running",
            "version": __version__,
            "base_stations_registered": len(current.base_stations),
            "default_wait": current.server.default_wait,
            "bluetooth": {
                "scan_timeout": controller.scan_timeout,
                "connection_timeout": controller.connection_timeout,
                "discovery_timeout": controller.discovery_timeout,
                "max_attempts": controller.max_attempts,
                "retry_backoff": controller.retry_backoff,
                "max_concurrency": controller.max_concurrency,
                "command_interval": controller.command_interval,
            },
        })

    @app.route("/api/basestations", methods=["GET"])
    def list_base_stations():
        """登録済みBase Station一覧を取得"""
        current = app.config["APP_CONFIG"]
        return jsonify({
            "base_stations": [bs.to_dict() for bs in current.base_stations]
        })

    @app.route("/api/basestations/scan", methods=["POST"])
    def scan_base_stations():
        """周囲のBase Stationをスキャン"""
        try:
            controller = app.config["BLE_CONTROLLER"]
            stations = run_async(controller.scan_base_stations())
            return jsonify({
                "success": True,
                "found": len(stations),
                "base_stations": [s.to_dict() for s in stations]
            })
        except Exception as e:
            logger.error("Scan error: %s", format_exception(e))
            return jsonify({
                "success": False,
                "error": format_exception(e)
            }), 500

    @app.route("/api/basestations/register", methods=["POST"])
    def register_base_station():
        """Base Stationを登録"""
        try:
            data = request.get_json()
            if not data:
                return jsonify({"success": False, "error": "No JSON data"}), 400

            station_id = data.get("id")
            name = data.get("name", f"Base Station {station_id}")
            mac_address = data.get("mac_address")

            if not station_id or not mac_address:
                return jsonify({
                    "success": False,
                    "error": "id and mac_address are required"
                }), 400

            current = app.config["APP_CONFIG"]
            bs = BaseStationConfig(id=station_id, name=name, mac_address=mac_address)
            current.add_base_station(bs)
            save_config(current)

            return jsonify({
                "success": True,
                "message": f"Registered {name} ({mac_address})"
            })
        except Exception as e:
            logger.error("Register error: %s", format_exception(e))
            return jsonify({"success": False, "error": format_exception(e)}), 500

    @app.route("/api/basestations/jobs/<job_id>", methods=["GET"])
    def get_job(job_id: str):
        """バックグラウンド電源操作の状態を取得"""
        with app.config["JOBS_LOCK"]:
            job = app.config["JOBS"].get(job_id)
            if job is None:
                return jsonify({
                    "success": False,
                    "error": f"Job '{job_id}' not found",
                }), 404
            return jsonify(copy.deepcopy(job))

    @app.route("/api/basestations/<station_id>/on", methods=["POST"])
    def power_on_station(station_id: str):
        """指定Base Stationの電源ON"""
        return _power_one(station_id, "power_on", "power_on")

    @app.route("/api/basestations/<station_id>/off", methods=["POST"])
    def power_off_station(station_id: str):
        """指定Base Stationの電源OFF"""
        return _power_one(station_id, "power_off", "power_off")

    @app.route("/api/basestations/all/on", methods=["POST"])
    def power_on_all():
        """全Base Stationの電源ON"""
        return _power_all("power_on_all", "power_on_all")

    @app.route("/api/basestations/all/off", methods=["POST"])
    def power_off_all():
        """全Base Stationの電源OFF"""
        return _power_all("power_off_all", "power_off_all")

    def _power_one(station_id: str, action: str, method_name: str):
        try:
            current = app.config["APP_CONFIG"]
            controller = app.config["BLE_CONTROLLER"]
            bs = current.get_base_station_by_id(station_id)
            if not bs:
                return jsonify({
                    "success": False,
                    "error": f"Base Station '{station_id}' not found"
                }), 404

            method = getattr(controller, method_name)
            if not should_wait():
                return enqueue(
                    action,
                    lambda: method(bs.mac_address),
                    {"station_id": station_id, "total": 1},
                    present=lambda result: _single_http_body(station_id, action, result),
                )

            result = run_async(method(bs.mac_address))
            body, status = _single_http_body(station_id, action, result)
            return jsonify(body), status
        except Exception as e:
            logger.error("%s error: %s", action, format_exception(e))
            return jsonify({"success": False, "error": format_exception(e)}), 500

    def _power_all(action: str, method_name: str):
        try:
            current = app.config["APP_CONFIG"]
            controller = app.config["BLE_CONTROLLER"]

            if not current.base_stations:
                return jsonify({
                    "success": False,
                    "error": "No base stations registered"
                }), 400

            mac_addresses = current.get_all_mac_addresses()
            method = getattr(controller, method_name)
            if not should_wait():
                # 登録内容が後から変わっても、このリクエスト時点の台を操作する
                targets = list(mac_addresses)
                return enqueue(
                    action,
                    lambda: method(targets),
                    {"total": len(targets)},
                )

            results = run_async(method(mac_addresses))
            body, status = _command_http_body(action, results)
            if status != 200:
                logger.warning("%s incomplete: %s", action, body.get("details"))
            return jsonify(body), status
        except Exception as e:
            logger.error("%s error: %s", action, format_exception(e))
            return jsonify({"success": False, "error": format_exception(e)}), 500

    return app


def _parse_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in _TRUTHY:
        return True
    if text in _FALSEY:
        return False
    return default


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _update_job(jobs: dict, jobs_lock: threading.Lock, job_id: str, **fields) -> None:
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            return
        job.update(fields)


def _single_http_body(station_id: str, action: str, result: CommandResult) -> tuple:
    """1台の結果。失敗時は 503。"""
    body = {
        "success": result.success,
        "station_id": station_id,
        "mac_address": result.mac_address,
        "action": action,
        "attempts": result.attempts,
        "error": result.error,
    }
    return body, (200 if result.success else 503)


def _command_http_body(action: str, detailed) -> tuple:
    """
    複数台、または1台の CommandResult を HTTP ボディにする。

    1台の power_on / power_off は CommandResult を返す。
    全台は mac -> CommandResult。
    どれか失敗していれば 503。全部成功なら 200。
    """
    if isinstance(detailed, CommandResult):
        # バックグラウンドの単台ジョブ。station_id はジョブ側の extra には残るが、
        # 結果ボディは mac と試行回数を持つ。
        body = {
            "success": detailed.success,
            "action": action,
            "mac_address": detailed.mac_address,
            "attempts": detailed.attempts,
            "error": detailed.error,
            "total": 1,
            "successful": 1 if detailed.success else 0,
            "failed": 0 if detailed.success else 1,
            "results": {detailed.mac_address: detailed.status},
            "details": {detailed.mac_address: detailed.to_dict()},
        }
        return body, (200 if detailed.success else 503)

    results = {}
    details = {}
    success_count = 0
    for mac, result in detailed.items():
        results[mac] = result.status
        details[mac] = result.to_dict()
        if result.success:
            success_count += 1
    total = len(detailed)
    ok = total > 0 and success_count == total
    body = {
        "success": ok,
        "action": action,
        "total": total,
        "successful": success_count,
        "failed": total - success_count,
        "results": results,
        "details": details,
    }
    return body, (200 if ok else 503)
