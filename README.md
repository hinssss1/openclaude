# OpenClaude 号池管理系统

一个功能完整的 Claude API 代理服务，支持批量注册、号池轮询、负载均衡和 OpenAI 兼容接口。

## 功能特性

- **批量注册** - 一键注册多个账号
- **号池轮询** - Round-Robin 负载均衡
- **故障转移** - 自动切换问题账号
- **健康检查** - 定时检测账号状态
- **持久化存储** - 账号数据自动保存
- **OpenAI 兼容** - 可替代 OpenAI API
- **Web 管理界面** - 可视化操作

---

## 快速开始

### 1. 安装依赖

```bash
pip install aiohttp certifi
```

### 2. 启动服务

```bash
# 基本启动
python3 pool_server.py

# 指定端口
python3 pool_server.py -p 8080

# 启动时自动注册10个账号
python3 pool_server.py -p 8080 --register 10
```

### 3. 访问管理界面

浏览器打开：http://localhost:8000

---

## 文件说明

```
├── pool_server.py      # 主服务器 (号池+API+前端)
├── client.py           # 命令行客户端
├── register.py         # 独立注册工具
├── account_pool.json   # 号池数据 (自动生成)
├── requirements.txt    # 依赖
└── web/
    └── index.html      # 管理界面
```

---

## API 接口

### 号池管理

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/pool/register` | POST | 批量注册 `{"count": 10}` |
| `/api/pool/login` | POST | 登录所有账号 |
| `/api/pool/accounts` | GET | 获取账号列表 |
| `/api/pool/add` | POST | 添加账号 `{"email": "", "password": ""}` |
| `/api/pool/remove/{email}` | DELETE | 删除账号 |
| `/api/pool/health` | POST | 执行健康检查 |

### 聊天接口

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/chat` | POST | 同步聊天 |
| `/api/chat/stream` | POST | 流式聊天 (SSE) |

### OpenAI 兼容

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/chat/completions` | POST | 聊天补全 |
| `/v1/models` | GET | 模型列表 |

---

## 使用示例

### 1. 批量注册账号

```bash
curl -X POST http://localhost:8000/api/pool/register \
  -H "Content-Type: application/json" \
  -d '{"count": 5, "concurrent": 3}'
```

### 2. 普通聊天

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "你好", "model": "claude-sonnet-4-5"}'
```

### 3. OpenAI 兼容调用

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-5",
    "messages": [{"role": "user", "content": "你好"}]
  }'
```

### 4. 流式响应

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-5",
    "stream": true,
    "messages": [{"role": "user", "content": "写一首诗"}]
  }'
```

---

## Python 调用

### 使用 OpenAI SDK

```python
from openai import OpenAI

client = OpenAI(
    api_key="any",  # 任意值即可
    base_url="http://localhost:8000/v1"
)

response = client.chat.completions.create(
    model="claude-sonnet-4-5",
    messages=[{"role": "user", "content": "你好"}]
)

print(response.choices[0].message.content)
```

### 使用内置客户端

```python
from client import OpenClaudeClient
import asyncio

async def main():
    async with OpenClaudeClient("email@example.com", "password") as client:
        # 流式输出
        async for text in client.chat_stream("你好"):
            print(text, end="", flush=True)

        # 完整响应
        response = await client.chat("你好")
        print(response)

asyncio.run(main())
```

---

## 支持的模型

| 模型 | 说明 |
|------|------|
| `claude-haiku-4-5` | 快速响应，适合简单任务 |
| `claude-sonnet-4-5` | 平衡性能，推荐使用 |
| `claude-opus-4-5` | 最强能力，复杂任务 |

### OpenAI 模型映射

| OpenAI 模型 | 映射到 |
|-------------|--------|
| `gpt-3.5-turbo` | claude-haiku-4-5 |
| `gpt-4` | claude-sonnet-4-5 |
| `gpt-4o` | claude-sonnet-4-5 |
| `gpt-4-turbo` | claude-sonnet-4-5 |

---

## 管理界面功能

### 仪表盘
- 账号统计 (总数/活跃/错误)
- 请求统计
- 状态分布
- 快捷操作

### 账号管理
- 查看所有账号
- 添加/删除账号
- 导入/导出账号
- 状态监控

### 聊天测试
- 实时对话测试
- 模型选择
- Token 统计
- 账号显示

### 系统设置
- API 配置
- 参数调整

---

## 导入已有账号

### 方式一：管理界面导入

1. 打开管理界面
2. 点击"导入账号"
3. 输入格式：每行一个 `邮箱:密码` 或 `邮箱,密码`

### 方式二：API 导入

```bash
curl -X POST http://localhost:8000/api/pool/add \
  -H "Content-Type: application/json" \
  -d '{"email": "your@email.com", "password": "yourpassword"}'
```

### 方式三：直接编辑号池文件

编辑 `account_pool.json`：

```json
{
  "accounts": [
    {
      "email": "account1@gmail.com",
      "password": "password1",
      "status": "inactive"
    }
  ]
}
```

然后调用登录接口：`POST /api/pool/login`

---

## 常见问题

### Q: 如何后台运行？

```bash
nohup python3 pool_server.py -p 8000 > server.log 2>&1 &
```

### Q: 如何查看日志？

```bash
tail -f server.log
```

### Q: 账号被限流怎么办？

系统会自动将限流账号标记为 `rate_limited`，并切换到其他账号。等待一段时间后会自动恢复。

### Q: 如何增加账号？

1. 管理界面点击"批量注册"
2. 或使用 API：`POST /api/pool/register {"count": 10}`

