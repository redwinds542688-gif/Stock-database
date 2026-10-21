#!/usr/bin/env bash
# 元大中繼程式安裝(Ubuntu 24.04,照元大文件「Python 設定 / Linux 系統」)
# 用法:sudo bash install.sh   (在這個資料夾裡執行)
set -euo pipefail
APP=/opt/yuanta_bridge
apt-get update && apt-get upgrade -y
apt-get install -y dotnet-sdk-8.0 python3 python3-pip python3.12-venv openssl
id -u yuanta >/dev/null 2>&1 || useradd -r -m -s /bin/bash yuanta
mkdir -p $APP/sdk $APP/cert $APP/log
cp yuanta_bridge.py bars.py test_bars.py trader.py test_trader.py bridge.env.example yuanta-bridge.service $APP/
[ -f $APP/bridge.env ] || cp bridge.env.example $APP/bridge.env
python3 -m venv $APP/venv
$APP/venv/bin/pip install --upgrade pip
$APP/venv/bin/pip install pythonnet==3.0.5
chown -R yuanta:yuanta $APP; chmod 600 $APP/bridge.env
timedatectl set-timezone Asia/Taipei || true
echo
echo "安裝完成。接下來:"
echo " 1. 把元大 SDK 元件包解壓縮到 $APP/sdk(要有 YuantaSparkAPI.dll 和 .so 檔)"
echo " 2. 憑證檔放到 $APP/cert,編輯 $APP/bridge.env 填帳號、密碼、憑證路徑、PUSH_KEY"
echo " 3. 測試:sudo -u yuanta $APP/venv/bin/python $APP/yuanta_bridge.py test 2330"
echo " 4. 常駐:sudo cp $APP/yuanta-bridge.service /etc/systemd/system/ && sudo systemctl enable --now yuanta-bridge"
echo "    看紀錄:journalctl -u yuanta-bridge -f"
