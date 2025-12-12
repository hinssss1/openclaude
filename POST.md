# OpenClaude 号池管理系统 - 注册机 Pro 版来了

之前发过一个 OpenClaude 的自动注册脚本，反响还不错。这段时间根据大家的反馈，搞了个升级版，功能更完善了。

## 这次更新了啥

上次那个注册机只能批量注册，注册完还得自己管理账号。这次直接做成了一套完整的系统：

- 号池管理 + 轮询负载
- Web 可视化界面
- OpenAI 兼容接口

简单说就是：注册完直接能用，还能当 API 服务跑。

## 先看效果

### 管理后台

启动服务后浏览器打开就是管理界面：

- 仪表盘能看到账号状态、请求统计
- 账号管理页可以增删改查、批量导入导出
- 还有个聊天测试页面，方便调试

### 核心功能

**1. 号池轮询**

不用担心单号被限流了，系统会自动轮换账号发请求。哪个号出问题了自动跳过，等恢复了再用。

**2. OpenAI 兼容**

这个比较实用。接口格式完全兼容 OpenAI，改个 base_url 就能用：

```python
from openai import OpenAI

client = OpenAI(
    api_key="随便填",
    base_url="http://localhost:8000/v1"
)

response = client.chat.completions.create(
    model="claude-sonnet-4-5",
    messages=[{"role": "user", "content": "你好"}]
)
```

流式输出也支持，体验和调 OpenAI 一样。

## 怎么用

### 安装

```bash
git clone https://github.com/xxx/openclaude-pool
cd openclaude-pool
pip install aiohttp certifi
```

### 启动

```bash
# 直接启动
python3 pool_server.py

# 启动时顺便注册10个号
python3 pool_server.py --register 10
```

然后打开 http://localhost:8000 就能看到管理界面了。

### 几个常用操作

**批量注册：**
```bash
curl -X POST http://localhost:8000/api/pool/register -d '{"count": 10}'
```

**聊天：**
```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "你好"}]}'
```

**导入已有账号：**

管理界面里点"导入账号"，格式是每行一个 `邮箱:密码`，批量粘贴进去就行。

## 文件说明

```
├── pool_server.py    # 主程序，跑这个就行
├── client.py         # 命令行客户端，想在终端里聊天用这个
├── register.py       # 单独的注册脚本，就是之前发的那个
├── account_pool.json # 号池数据，自动生成的
└── web/index.html    # 前端页面
```

## 支持的模型

- `claude-haiku-4-5` - 速度快，简单问题用这个
- `claude-sonnet-4-5` - 均衡，日常够用
- `claude-opus-4-5` - 最强，复杂任务用

用 OpenAI 的模型名也行，会自动映射：
- gpt-3.5-turbo → haiku
- gpt-4 / gpt-4o → sonnet

## 几点说明

1. 账号数据存在 `account_pool.json` 里，记得备份
2. 建议多注册几个号，轮换着用不容易触发限流
3. 健康检查是自动的，问题账号会被暂时跳过

## 后续计划

- [ ] 加个简单的鉴权，部署到公网用
- [ ] 支持多个 OpenClaude 站点
- [ ] 账号自动补充（数量低于阈值自动注册）

有问题欢迎反馈，觉得有用的话点个 star~

---

GitHub: [项目地址]

相关帖子：[之前的注册机帖子链接]
