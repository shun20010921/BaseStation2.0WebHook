# VIVE Base Station 2.0 Webhook Controller

Bluetooth Low Energy (BLE) を使用してVIVE Base Station 2.0の電源を制御するWebhookサーバーです。

Home Assistant、HomeBridge などのスマートホームシステムから簡単に統合できます。

## 特徴

- 🔵 Bluetooth LE で Base Station 2.0 を制御
- 🌐 RESTful API / Webhook 対応
- 🏠 Home Assistant / HomeBridge 連携
- 💻 Windows / Linux 両対応（bleak 2.x）
- 📡 複数台の Base Station をサポート
- 🔁 接続前スキャンと台ごとの再試行。CSR Bluetooth 4.0 ドングル向けに同時接続数を制限

## 動作要件

- Python 3.10 以上（bleak 2.x の要件。Ubuntu 22.04 の 3.10 で動作）
- bleak 2.x（本番は 2.1.1。3.x は依存関係に含めていません）
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
| GET | `/api/basestations/jobs/{job_id}` | バックグラウンドジョブの状態 |

`/on` と `/off`（単台・全台）は、クエリまたは JSON で待ち方を選べます。

| 指定 | 動作 |
|------|------|
| なし | `server.default_wait`（既定は同期） |
| `?wait=true` | 終わるまで待つ |
| `?wait=false` または `?async=true` | **202** をすぐ返し、裏で実行する |
| JSON `{"wait": false}` | 同上 |

同期で全台成功すると **200**、1台でも最終的に失敗すると **503** です。ボディの `success` は false になり、`results` は MAC ごとの `"success"` / `"failed"`、`details` に試行回数とエラー文字列が入ります。202 は「受け付けた」という意味で、各台の成否はジョブの `result` を見てください。

```bash
curl -X POST "http://127.0.0.1:5000/api/basestations/all/on?wait=false"
curl "http://127.0.0.1:5000/api/basestations/jobs/<job_id>"
```

## Linux Service Setup (systemd)

Ubuntu / Raspberry Pi OS 等で自動起動させるための設定です。

1. リポジトリを `/opt/` に配置し、仮想環境を作成します (推奨):
   ```bash
   sudo mv BaseStationWebhook /opt/
   cd /opt/BaseStationWebhook
   
   # venvパッケージのインストール (Ubuntuの場合)
   sudo apt install -y python3-venv python3-full

   # 仮想環境の作成とライブラリインストール
   python3 -m venv venv
   ./venv/bin/pip install -r requirements.txt
   ```

2. サービスファイルをコピーします:
   ```bash
   sudo cp service/basestation-webhook.service /etc/systemd/system/
   ```

3. サービスファイルを編集してユーザーやパスを調整してください (必要な場合):
   ```bash
   sudo nano /etc/systemd/system/basestation-webhook.service
   ```

4. サービスを有効化・起動します:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable basestation-webhook
   sudo systemctl start basestation-webhook
   ```

5. ステータス確認:
   ```bash
   sudo systemctl status basestation-webhook
   ```

## Home Assistant 連携

`rest_command` のタイムアウトは既定で約 10 秒です。再試行込みの同期実行は台数によってはそれを超えるので、すぐ 202 を返すか、待ち時間を伸ばしてください。

すぐ戻す例（推奨。スイッチは受付完了で ON/OFF になります。各台の最終結果はサーバーログかジョブ URL で確認します）:

```yaml
rest_command:
  basestation_on:
    url: "http://YOUR_SERVER_IP:5000/api/basestations/all/on?wait=false"
    method: POST
    timeout: 10
  basestation_off:
    url: "http://YOUR_SERVER_IP:5000/api/basestations/all/off?wait=false"
    method: POST
    timeout: 10

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

全部の成否を Home Assistant に返したい場合は同期のままタイムアウトを伸ばします。1台でも失敗すると HTTP 503 になり、`rest_command` はエラーとして残ります。3台・試行3回なら 180 秒あれば足りることが多いです。

```yaml
rest_command:
  basestation_on:
    url: "http://YOUR_SERVER_IP:5000/api/basestations/all/on"
    method: POST
    timeout: 180
  basestation_off:
    url: "http://YOUR_SERVER_IP:5000/api/basestations/all/off"
    method: POST
    timeout: 180
```

サーバー側で常に裏実行へ倒すなら `config.yaml` の `server.default_wait` を `false` にします。その場合も `?wait=true` で同期に戻せます。

## HomeBridge 連携

`homebridge-http-switch` プラグインを使用:

```json
{
  "accessory": "HTTP-SWITCH",
  "name": "VR Base Stations",
  "switchType": "stateless",
  "onUrl": "http://YOUR_SERVER_IP:5000/api/basestations/all/on?wait=false",
  "offUrl": "http://YOUR_SERVER_IP:5000/api/basestations/all/off?wait=false"
}
```

## 設定ファイル

`config.yaml` を編集して設定を変更できます:

```yaml
server:
  host: "0.0.0.0"
  port: 5000
  # false にすると /all と単台の電源 API は既定で 202 を返す
  default_wait: true

bluetooth:
  scan_timeout: 10          # 一覧スキャン（秒）
  connection_timeout: 15    # 接続して GATT を書くまでの待ち（秒）
  discovery_timeout: 5      # 接続前の find_device_by_address（秒）
  max_attempts: 3           # 1台あたりの試行回数
  retry_backoff: 1.0        # 失敗後の待ち（秒）。試行番号を掛ける（1s, 2s, ...）
  # CSR Bluetooth 4.0 ドングル (USB 0a12:0001) は接続を同時に複数本出すと
  # "device disconnected" や空のエラーになりやすい。既定の 1 は台ごとに順次。
  # 同時接続が安定するアダプタだけ 2 以上にする。
  max_concurrency: 1
  # max_concurrency が 1 のとき、台と台の間に空ける秒数
  command_interval: 0.5

base_stations:
  - id: "bs1"
    name: "LHB-12345678"
    mac_address: "AA:BB:CC:DD:EE:FF"
```

接続の流れは次のとおりです。

1. `BleakScanner.find_device_by_address` で `BLEDevice` を解決する
2. 見つからなければ同じタイムアウトでもう一度スキャンする
3. 解決した `BLEDevice` を `BleakClient` に渡して電源 characteristic を書く
4. 失敗したら `retry_backoff * 試行番号` 秒待って、`max_attempts` まで繰り返す

ログには `attempt 1/3` と例外の型、`str`、`repr` を出します。`BleakError()` のようにメッセージが空でも、型と repr が残ります。

再試行に [bleak-retry-connector](https://github.com/Bluetooth-Devices/bleak-retry-connector) は使っていません。4.6.3 以降は bleak 3 以上が必要で、bleak 2 で入る 4.6.2 も Home Assistant の常駐スキャナと BlueZ のサービスキャッシュ向けです。このサーバーは bleak 2.x のまま、短時間の接続に合わせて試行回数を自前で数えます。

## トラブルシューティング

### Base Station が見つからない

- Base Station が電源に接続されていることを確認
- Bluetooth が有効になっていることを確認
- `--timeout` を増やしてみる: `python run.py scan --timeout 20`

### 接続エラー

- 他のアプリ (SteamVR等) が Base Station に接続していないか確認
- Base Station の近くでスキャンする
- `Device with address … was not found` や `failed to discover services, device disconnected`、空のエラーは電波が弱くなくても起きます。既定で 3 回までやり直します
- CSR Bluetooth 4.0 (0a12:0001) では `max_concurrency` を 1 のままにしてください。Intel などの新しいアダプタで同時接続が安定する場合だけ 2 以上にします
- Home Assistant が約 10 秒でタイムアウトしてエラーを出す場合は、上の `?wait=false` か `timeout: 180` を使います。同期応答の 200 は全台成功、503 はいずれかが失敗です

## テスト

BLE アダプタは使いません。bleak はモックします。

```bash
pip install -r requirements-dev.txt
pytest
```

### Windows での問題

- Windows 10 バージョン 1809 以降が必要
- Bluetooth ドライバが最新か確認

## ライセンス

MIT License
