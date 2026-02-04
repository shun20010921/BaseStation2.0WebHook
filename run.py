#!/usr/bin/env python3
"""
VIVE Base Station 2.0 Webhook Controller

Webサーバーを起動してWebhook経由でBase Stationを制御します。
"""

import argparse
import logging
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from basestation_controller.webhook_server import create_app
from basestation_controller.config import load_config, save_config, AppConfig, BaseStationConfig
from basestation_controller.ble_controller import BLEController
import asyncio


def setup_logging(verbose: bool = False):
    """ロギング設定"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )


def cmd_serve(args):
    """Webhookサーバーを起動"""
    config = load_config()
    
    # コマンドライン引数で上書き
    if args.host:
        config.server.host = args.host
    if args.port:
        config.server.port = args.port
    
    app = create_app(config)
    
    print(f"""
╔═══════════════════════════════════════════════════════════╗
║  VIVE Base Station 2.0 Webhook Controller                ║
╠═══════════════════════════════════════════════════════════╣
║  Server: http://{config.server.host}:{config.server.port:<5}                            ║
║  Base Stations: {len(config.base_stations):<3} registered                           ║
╠═══════════════════════════════════════════════════════════╣
║  Endpoints:                                               ║
║    GET  /api/status              - Server status          ║
║    GET  /api/basestations        - List stations          ║
║    POST /api/basestations/scan   - Scan for stations      ║
║    POST /api/basestations/{{id}}/on  - Power on           ║
║    POST /api/basestations/{{id}}/off - Power off          ║
║    POST /api/basestations/all/on   - All power on         ║
║    POST /api/basestations/all/off  - All power off        ║
╚═══════════════════════════════════════════════════════════╝
    """)
    
    app.run(
        host=config.server.host,
        port=config.server.port,
        debug=args.debug,
        threaded=True
    )


def cmd_scan(args):
    """Base Stationをスキャン"""
    print("Scanning for VIVE Base Station 2.0...")
    print(f"Timeout: {args.timeout} seconds\n")
    
    controller = BLEController(scan_timeout=args.timeout)
    
    try:
        stations = asyncio.run(controller.scan_base_stations())
        
        if not stations:
            print("No Base Stations found.")
            print("\nTips:")
            print("  - Make sure Base Stations are powered on")
            print("  - Check Bluetooth is enabled on this computer")
            print("  - Try increasing timeout with --timeout")
            return
        
        print(f"Found {len(stations)} Base Station(s):\n")
        print("-" * 60)
        for i, bs in enumerate(stations, 1):
            print(f"  {i}. {bs.name}")
            print(f"     MAC Address: {bs.mac_address}")
            if bs.rssi:
                print(f"     RSSI: {bs.rssi} dBm")
            print()
        print("-" * 60)
        
        if args.register:
            print("\nRegistering found Base Stations...")
            config = load_config()
            for i, bs in enumerate(stations, 1):
                station_id = f"bs{i}"
                bs_config = BaseStationConfig(
                    id=station_id,
                    name=bs.name,
                    mac_address=bs.mac_address
                )
                config.add_base_station(bs_config)
                print(f"  Registered: {station_id} -> {bs.mac_address}")
            
            save_config(config)
            print(f"\nSaved to config file.")
            
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_on(args):
    """Base Stationの電源ON"""
    config = load_config()
    controller = BLEController(connection_timeout=config.bluetooth.connection_timeout)
    
    if args.all:
        mac_addresses = config.get_all_mac_addresses()
        if not mac_addresses:
            print("No Base Stations registered. Run 'scan --register' first.")
            return
        
        print(f"Powering ON {len(mac_addresses)} Base Station(s)...")
        results = asyncio.run(controller.power_on_all(mac_addresses))
        
        for mac, result in results.items():
            status = "✓" if result == "success" else "✗"
            print(f"  {status} {mac}: {result}")
    else:
        if not args.id:
            print("Error: Specify --id or --all")
            return
        
        bs = config.get_base_station_by_id(args.id)
        if not bs:
            print(f"Error: Base Station '{args.id}' not found")
            return
        
        print(f"Powering ON {bs.name} ({bs.mac_address})...")
        success = asyncio.run(controller.power_on(bs.mac_address))
        print("Success!" if success else "Failed!")


def cmd_off(args):
    """Base Stationの電源OFF"""
    config = load_config()
    controller = BLEController(connection_timeout=config.bluetooth.connection_timeout)
    
    if args.all:
        mac_addresses = config.get_all_mac_addresses()
        if not mac_addresses:
            print("No Base Stations registered. Run 'scan --register' first.")
            return
        
        print(f"Powering OFF {len(mac_addresses)} Base Station(s)...")
        results = asyncio.run(controller.power_off_all(mac_addresses))
        
        for mac, result in results.items():
            status = "✓" if result == "success" else "✗"
            print(f"  {status} {mac}: {result}")
    else:
        if not args.id:
            print("Error: Specify --id or --all")
            return
        
        bs = config.get_base_station_by_id(args.id)
        if not bs:
            print(f"Error: Base Station '{args.id}' not found")
            return
        
        print(f"Powering OFF {bs.name} ({bs.mac_address})...")
        success = asyncio.run(controller.power_off(bs.mac_address))
        print("Success!" if success else "Failed!")


def main():
    parser = argparse.ArgumentParser(
        description='VIVE Base Station 2.0 Webhook Controller',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run.py serve                    # Start webhook server
  python run.py scan                     # Scan for Base Stations
  python run.py scan --register          # Scan and register found stations
  python run.py on --all                 # Power on all stations
  python run.py off --all                # Power off all stations
  python run.py on --id bs1              # Power on specific station
        """
    )
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # serve command
    serve_parser = subparsers.add_parser('serve', help='Start webhook server')
    serve_parser.add_argument('--host', type=str, help='Server host (default: 0.0.0.0)')
    serve_parser.add_argument('--port', type=int, help='Server port (default: 5000)')
    serve_parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    serve_parser.set_defaults(func=cmd_serve)
    
    # scan command
    scan_parser = subparsers.add_parser('scan', help='Scan for Base Stations')
    scan_parser.add_argument('--timeout', type=float, default=10.0, help='Scan timeout (seconds)')
    scan_parser.add_argument('--register', action='store_true', help='Register found stations')
    scan_parser.set_defaults(func=cmd_scan)
    
    # on command
    on_parser = subparsers.add_parser('on', help='Power on Base Station(s)')
    on_parser.add_argument('--id', type=str, help='Station ID')
    on_parser.add_argument('--all', action='store_true', help='All registered stations')
    on_parser.set_defaults(func=cmd_on)
    
    # off command
    off_parser = subparsers.add_parser('off', help='Power off Base Station(s)')
    off_parser.add_argument('--id', type=str, help='Station ID')
    off_parser.add_argument('--all', action='store_true', help='All registered stations')
    off_parser.set_defaults(func=cmd_off)
    
    args = parser.parse_args()
    
    setup_logging(args.verbose)
    
    if args.command is None:
        parser.print_help()
        sys.exit(0)
    
    args.func(args)


if __name__ == '__main__':
    main()
