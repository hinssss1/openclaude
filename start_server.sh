#!/bin/bash
# 启动 OpenClaude API 代理服务

cd "$(dirname "$0")"

# 安装依赖
pip3 install -q aiohttp certifi

# 启动服务器
python3 api_server.py "$@"
