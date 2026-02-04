"""
Webhook Server for VIVE Base Station 2.0

Flask ベースのWebhookサーバー
Home Assistant / HomeBridge から呼び出し可能なAPIを提供します。
"""

import asyncio
import logging
from flask import Flask, jsonify, request
from .ble_controller import BLEController, BaseStation
from .config import AppConfig, BaseStationConfig, load_config, save_config

logger = logging.getLogger(__name__)


def create_app(config: AppConfig = None) -> Flask:
    """
    Flaskアプリケーションを作成します。
    
    Args:
        config: アプリケーション設定（省略時は設定ファイルから読み込み）
    
    Returns:
        Flask アプリケーション
    """
    app = Flask(__name__)
    
    # 設定読み込み
    if config is None:
        config = load_config()
    
    app.config['APP_CONFIG'] = config
    
    # BLEコントローラー初期化
    ble_controller = BLEController(
        scan_timeout=config.bluetooth.scan_timeout,
        connection_timeout=config.bluetooth.connection_timeout
    )
    app.config['BLE_CONTROLLER'] = ble_controller
    
    # イベントループ（Windows対応）
    def get_or_create_eventloop():
        try:
            return asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop
    
    def run_async(coro):
        """非同期関数を同期的に実行"""
        loop = get_or_create_eventloop()
        return loop.run_until_complete(coro)
    
    # ===== API Endpoints =====
    
    @app.route('/api/status', methods=['GET'])
    def get_status():
        """サーバーステータスを取得"""
        config = app.config['APP_CONFIG']
        return jsonify({
            "status": "running",
            "version": "1.0.0",
            "base_stations_registered": len(config.base_stations)
        })
    
    @app.route('/api/basestations', methods=['GET'])
    def list_base_stations():
        """登録済みBase Station一覧を取得"""
        config = app.config['APP_CONFIG']
        return jsonify({
            "base_stations": [bs.to_dict() for bs in config.base_stations]
        })
    
    @app.route('/api/basestations/scan', methods=['POST'])
    def scan_base_stations():
        """周囲のBase Stationをスキャン"""
        try:
            controller = app.config['BLE_CONTROLLER']
            stations = run_async(controller.scan_base_stations())
            return jsonify({
                "success": True,
                "found": len(stations),
                "base_stations": [s.to_dict() for s in stations]
            })
        except Exception as e:
            logger.error(f"Scan error: {e}")
            return jsonify({
                "success": False,
                "error": str(e)
            }), 500
    
    @app.route('/api/basestations/register', methods=['POST'])
    def register_base_station():
        """Base Stationを登録"""
        try:
            data = request.get_json()
            if not data:
                return jsonify({"success": False, "error": "No JSON data"}), 400
            
            station_id = data.get('id')
            name = data.get('name', f'Base Station {station_id}')
            mac_address = data.get('mac_address')
            
            if not station_id or not mac_address:
                return jsonify({
                    "success": False, 
                    "error": "id and mac_address are required"
                }), 400
            
            config = app.config['APP_CONFIG']
            bs = BaseStationConfig(id=station_id, name=name, mac_address=mac_address)
            config.add_base_station(bs)
            save_config(config)
            
            return jsonify({
                "success": True,
                "message": f"Registered {name} ({mac_address})"
            })
        except Exception as e:
            logger.error(f"Register error: {e}")
            return jsonify({"success": False, "error": str(e)}), 500
    
    @app.route('/api/basestations/<station_id>/on', methods=['POST'])
    def power_on_station(station_id: str):
        """指定Base Stationの電源ON"""
        try:
            config = app.config['APP_CONFIG']
            controller = app.config['BLE_CONTROLLER']
            
            bs = config.get_base_station_by_id(station_id)
            if not bs:
                return jsonify({
                    "success": False,
                    "error": f"Base Station '{station_id}' not found"
                }), 404
            
            success = run_async(controller.power_on(bs.mac_address))
            
            return jsonify({
                "success": success,
                "station_id": station_id,
                "action": "power_on"
            })
        except Exception as e:
            logger.error(f"Power on error: {e}")
            return jsonify({"success": False, "error": str(e)}), 500
    
    @app.route('/api/basestations/<station_id>/off', methods=['POST'])
    def power_off_station(station_id: str):
        """指定Base Stationの電源OFF"""
        try:
            config = app.config['APP_CONFIG']
            controller = app.config['BLE_CONTROLLER']
            
            bs = config.get_base_station_by_id(station_id)
            if not bs:
                return jsonify({
                    "success": False,
                    "error": f"Base Station '{station_id}' not found"
                }), 404
            
            success = run_async(controller.power_off(bs.mac_address))
            
            return jsonify({
                "success": success,
                "station_id": station_id,
                "action": "power_off"
            })
        except Exception as e:
            logger.error(f"Power off error: {e}")
            return jsonify({"success": False, "error": str(e)}), 500
    
    @app.route('/api/basestations/all/on', methods=['POST'])
    def power_on_all():
        """全Base Stationの電源ON"""
        try:
            config = app.config['APP_CONFIG']
            controller = app.config['BLE_CONTROLLER']
            
            if not config.base_stations:
                return jsonify({
                    "success": False,
                    "error": "No base stations registered"
                }), 400
            
            mac_addresses = config.get_all_mac_addresses()
            results = run_async(controller.power_on_all(mac_addresses))
            
            success_count = sum(1 for r in results.values() if r == "success")
            
            return jsonify({
                "success": success_count == len(results),
                "action": "power_on_all",
                "total": len(results),
                "successful": success_count,
                "results": results
            })
        except Exception as e:
            logger.error(f"Power on all error: {e}")
            return jsonify({"success": False, "error": str(e)}), 500
    
    @app.route('/api/basestations/all/off', methods=['POST'])
    def power_off_all():
        """全Base Stationの電源OFF"""
        try:
            config = app.config['APP_CONFIG']
            controller = app.config['BLE_CONTROLLER']
            
            if not config.base_stations:
                return jsonify({
                    "success": False,
                    "error": "No base stations registered"
                }), 400
            
            mac_addresses = config.get_all_mac_addresses()
            results = run_async(controller.power_off_all(mac_addresses))
            
            success_count = sum(1 for r in results.values() if r == "success")
            
            return jsonify({
                "success": success_count == len(results),
                "action": "power_off_all",
                "total": len(results),
                "successful": success_count,
                "results": results
            })
        except Exception as e:
            logger.error(f"Power off all error: {e}")
            return jsonify({"success": False, "error": str(e)}), 500
    
    return app
