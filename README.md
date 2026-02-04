# VIVE Base Station 2.0 Webhook Controller

Bluetooth Low Energy (BLE) を使用してVIVE Base Station 2.0の電源を制御するWebhookサーバーです。

Home Assistant、HomeBridge などのスマートホームシステムから簡単に統合できます。

## 特徴

- 🔵 Bluetooth LE で Base Station 2.0 を制御
- 🌐 RESTful API / Webhook 対応
- 🏠 Home Assistant / HomeBridge 連携
- 💻 Windows / Linux 両対応
- 📡 複数台の Base Station をサポート

## 動作要件

- Python 3.8 以上
- Bluetooth 4.0+ (BLE対応) アダプタ
- Windows 10 1809以降 または Linux

## インストール

```bash
cd BaseStationWebhook
pip install -r requirements.txt
```

## クイックスタート

### 1. Base Station をスキャン

```bash
python run.py scan
```

### 2. 検出した Base Station を登録

```bash
python run.py scan --register
```

### 3. Webhook サーバーを起動

```bash
python run.py serve
```

サーバーが `http://0.0.0.0:5000` で起動します。

## CLI コマンド

```bash
# サーバー起動
python run.py serve
python run.py serve --port 8080

# スキャン
python run.py scan
python run.py scan --register         # 見つかったBase Stationを登録

# 電源制御
python run.py on --all                # 全Base Station ON
python run.py off --all               # 全Base Station OFF
python run.py on --id bs1             # 特定のBase Station ON
python run.py off --id bs1            # 特定のBase Station OFF
```

## API エンドポイント

| Method | Endpoint | 説明 |
|--------|----------|------|
| GET | `/api/status` | サーバーステータス |
| GET | `/api/basestations` | 登録済みBase Station一覧 |
| POST | `/api/basestations/scan` | Base Stationをスキャン |
| POST | `/api/basestations/register` | Base Stationを登録 |
| POST | `/api/basestations/{id}/on` | 指定Base Station 電源ON |
| POST | `/api/basestations/{id}/off` | 指定Base Station 電源OFF |
| POST | `/api/basestations/all/on` | 全Base Station 電源ON |
| POST | `/api/basestations/all/off` | 全Base Station 電源OFF |

## Home Assistant 連携

`configuration.yaml` に以下を追加:

```yaml
rest_command:
  basestation_on:
    url: "http://YOUR_SERVER_IP:5000/api/basestations/all/on"
    method: POST
  basestation_off:
    url: "http://YOUR_SERVER_IP:5000/api/basestations/all/off"
    method: POST

switch:
  - platform: template
    switches:
      vr_base_stations:
        friendly_name: "VR Base Stations"
        turn_on:
          service: rest_command.basestation_on
        turn_off:
          service: rest_command.basestation_off
```

## HomeBridge 連携

`homebridge-http-switch` プラグインを使用:

```json
{
  "accessory": "HTTP-SWITCH",
  "name": "VR Base Stations",
  "switchType": "stateless",
  "onUrl": "http://YOUR_SERVER_IP:5000/api/basestations/all/on",
  "offUrl": "http://YOUR_SERVER_IP:5000/api/basestations/all/off"
}
```

## 設定ファイル

`config.yaml` を編集して設定を変更できます:

```yaml
server:
  host: "0.0.0.0"
  port: 5000

bluetooth:
  scan_timeout: 10
  connection_timeout: 15

base_stations:
  - id: "bs1"
    name: "LHB-12345678"
    mac_address: "AA:BB:CC:DD:EE:FF"
```

## トラブルシューティング

### Base Station が見つからない

- Base Station が電源に接続されていることを確認
- Bluetooth が有効になっていることを確認
- `--timeout` を増やしてみる: `python run.py scan --timeout 20`

### 接続エラー

- 他のアプリ (SteamVR等) が Base Station に接続していないか確認
- Base Station の近くでスキャンする

### Windows での問題

- Windows 10 バージョン 1809 以降が必要
- Bluetooth ドライバが最新か確認

## ライセンス

MIT License
