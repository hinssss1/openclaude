#!/usr/bin/env python3
"""
OpenClaude 号池轮询系统
=======================
- 批量注册账号到号池
- 轮询负载均衡
- 健康检查与故障转移
- 持久化存储
- OpenAI 兼容接口
"""

import asyncio
import aiohttp
import ssl
import certifi
import json
import uuid
import random
import string
import time
import os
from datetime import datetime
from typing import Optional, AsyncGenerator, List, Dict
from dataclasses import dataclass, field, asdict
from enum import Enum
from aiohttp import web
from pathlib import Path
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


# ============ 数据模型 ============

class AccountStatus(Enum):
    ACTIVE = "active"           # 正常可用
    INACTIVE = "inactive"       # 未激活/未登录
    RATE_LIMITED = "rate_limited"  # 被限流
    BANNED = "banned"           # 被封禁
    ERROR = "error"             # 出错


@dataclass
class Account:
    """账户信息"""
    email: str
    password: str
    status: AccountStatus = AccountStatus.INACTIVE
    token: Optional[str] = None
    created_at: str = ""
    last_used: str = ""
    request_count: int = 0
    error_count: int = 0
    consecutive_errors: int = 0

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if isinstance(self.status, str):
            self.status = AccountStatus(self.status)

    def to_dict(self) -> dict:
        d = asdict(self)
        d['status'] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> 'Account':
        return cls(**d)


# ============ 账号池管理器 ============

class AccountPool:
    """账号池管理器 - 支持轮询和健康检查"""

    BASE_URL = "https://openclaude.me"
    POOL_FILE = "account_pool.json"

    def __init__(self, pool_file: str = None):
        self.pool_file = pool_file or self.POOL_FILE
        self.accounts: Dict[str, Account] = {}
        self.active_clients: Dict[str, aiohttp.ClientSession] = {}
        self.tokens: Dict[str, str] = {}  # email -> token

        # 轮询索引
        self._robin_index = 0
        self._active_list: List[str] = []

        # 配置
        self.max_consecutive_errors = 3  # 连续错误次数阈值
        self.health_check_interval = 300  # 健康检查间隔(秒)
        self.rate_limit_cooldown = 60  # 限流冷却时间(秒)

        # SSL
        self._ssl_context = ssl.create_default_context(cafile=certifi.where())

    # ---- 持久化 ----

    def save(self):
        """保存号池到文件"""
        data = {
            "updated_at": datetime.now().isoformat(),
            "accounts": [acc.to_dict() for acc in self.accounts.values()]
        }
        with open(self.pool_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"号池已保存: {len(self.accounts)} 个账号")

    def load(self):
        """从文件加载号池"""
        if not Path(self.pool_file).exists():
            logger.info("号池文件不存在，创建新号池")
            return

        try:
            with open(self.pool_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            for acc_data in data.get("accounts", []):
                acc = Account.from_dict(acc_data)
                self.accounts[acc.email] = acc

            logger.info(f"已加载 {len(self.accounts)} 个账号")
            self._refresh_active_list()
        except Exception as e:
            logger.error(f"加载号池失败: {e}")

    # ---- 账号管理 ----

    @staticmethod
    def generate_email(domain: str = "gmail.com") -> str:
        prefix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
        ts = datetime.now().strftime("%m%d%H%M%S")
        return f"{prefix}{ts}@{domain}"

    @staticmethod
    def generate_password(length: int = 16) -> str:
        chars = string.ascii_letters + string.digits + "!@#$%^&*"
        pwd = [
            random.choice(string.ascii_uppercase),
            random.choice(string.ascii_lowercase),
            random.choice(string.digits),
            random.choice("!@#$%^&*")
        ]
        pwd += random.choices(chars, k=length - 4)
        random.shuffle(pwd)
        return ''.join(pwd)

    async def register_account(self, email: str = None, password: str = None) -> Optional[Account]:
        """注册新账号"""
        email = email or self.generate_email()
        password = password or self.generate_password()

        connector = aiohttp.TCPConnector(ssl=self._ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            try:
                async with session.post(
                    f"{self.BASE_URL}/api/auth/signup",
                    json={"email": email, "password": password},
                    headers={"Content-Type": "application/json", "Origin": self.BASE_URL}
                ) as resp:
                    if resp.status == 200:
                        acc = Account(email=email, password=password)
                        self.accounts[email] = acc
                        logger.info(f"注册成功: {email}")
                        return acc
                    else:
                        error = await resp.text()
                        logger.error(f"注册失败 [{resp.status}]: {error[:100]}")
                        return None
            except Exception as e:
                logger.error(f"注册异常: {e}")
                return None

    async def register_batch(self, count: int, concurrent: int = 5) -> List[Account]:
        """批量注册账号"""
        logger.info(f"开始批量注册 {count} 个账号 (并发: {concurrent})")
        semaphore = asyncio.Semaphore(concurrent)
        results = []

        async def register_one():
            async with semaphore:
                acc = await self.register_account()
                if acc:
                    results.append(acc)
                await asyncio.sleep(0.5)  # 避免请求过快

        tasks = [register_one() for _ in range(count)]
        await asyncio.gather(*tasks)

        self.save()
        logger.info(f"批量注册完成: 成功 {len(results)}/{count}")
        return results

    async def login_account(self, email: str) -> bool:
        """登录账号获取token"""
        acc = self.accounts.get(email)
        if not acc:
            return False

        connector = aiohttp.TCPConnector(ssl=self._ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            try:
                async with session.post(
                    f"{self.BASE_URL}/api/auth/login",
                    json={"email": acc.email, "password": acc.password},
                    headers={"Content-Type": "application/json", "Origin": self.BASE_URL}
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        acc.token = data.get("auth_token")
                        acc.status = AccountStatus.ACTIVE
                        acc.consecutive_errors = 0
                        self.tokens[email] = acc.token
                        logger.info(f"登录成功: {email}")
                        return True
                    else:
                        acc.status = AccountStatus.ERROR
                        logger.error(f"登录失败: {email}")
                        return False
            except Exception as e:
                acc.status = AccountStatus.ERROR
                logger.error(f"登录异常 {email}: {e}")
                return False

    async def login_all(self, concurrent: int = 5):
        """登录所有账号"""
        semaphore = asyncio.Semaphore(concurrent)
        success = 0

        async def login_one(email: str):
            nonlocal success
            async with semaphore:
                if await self.login_account(email):
                    success += 1
                await asyncio.sleep(0.3)

        tasks = [login_one(email) for email in self.accounts.keys()]
        await asyncio.gather(*tasks)

        self._refresh_active_list()
        self.save()
        logger.info(f"批量登录完成: {success}/{len(self.accounts)}")

    def _refresh_active_list(self):
        """刷新可用账号列表"""
        self._active_list = [
            email for email, acc in self.accounts.items()
            if acc.status == AccountStatus.ACTIVE and acc.token
        ]
        logger.info(f"可用账号: {len(self._active_list)}")

    # ---- 轮询调度 ----

    def get_next_account(self) -> Optional[Account]:
        """轮询获取下一个可用账号"""
        if not self._active_list:
            self._refresh_active_list()
            if not self._active_list:
                return None

        # Round Robin
        self._robin_index = (self._robin_index + 1) % len(self._active_list)
        email = self._active_list[self._robin_index]
        acc = self.accounts.get(email)

        if acc and acc.status == AccountStatus.ACTIVE:
            acc.last_used = datetime.now().isoformat()
            acc.request_count += 1
            return acc

        # 账号不可用，尝试下一个
        self._refresh_active_list()
        return self.get_next_account() if self._active_list else None

    def mark_error(self, email: str, error_type: str = "error"):
        """标记账号出错"""
        acc = self.accounts.get(email)
        if not acc:
            return

        acc.error_count += 1
        acc.consecutive_errors += 1

        if error_type == "rate_limit":
            acc.status = AccountStatus.RATE_LIMITED
            logger.warning(f"账号被限流: {email}")
        elif error_type == "banned":
            acc.status = AccountStatus.BANNED
            logger.error(f"账号被封禁: {email}")
        elif acc.consecutive_errors >= self.max_consecutive_errors:
            acc.status = AccountStatus.ERROR
            logger.warning(f"账号连续错误过多: {email}")

        self._refresh_active_list()

    def mark_success(self, email: str):
        """标记账号成功"""
        acc = self.accounts.get(email)
        if acc:
            acc.consecutive_errors = 0
            if acc.status != AccountStatus.ACTIVE:
                acc.status = AccountStatus.ACTIVE
                self._refresh_active_list()

    # ---- 健康检查 ----

    async def health_check(self, email: str) -> bool:
        """检查账号健康状态"""
        acc = self.accounts.get(email)
        if not acc or not acc.token:
            return False

        connector = aiohttp.TCPConnector(ssl=self._ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            try:
                async with session.get(
                    f"{self.BASE_URL}/api/user/me",
                    headers={"Authorization": f"Bearer {acc.token}"}
                ) as resp:
                    if resp.status == 200:
                        acc.status = AccountStatus.ACTIVE
                        return True
                    elif resp.status == 401:
                        # Token 过期，重新登录
                        return await self.login_account(email)
                    else:
                        acc.status = AccountStatus.ERROR
                        return False
            except:
                return False

    async def health_check_all(self):
        """检查所有账号健康状态"""
        logger.info("开始健康检查...")
        tasks = [self.health_check(email) for email in self.accounts.keys()]
        results = await asyncio.gather(*tasks)
        healthy = sum(results)
        self._refresh_active_list()
        logger.info(f"健康检查完成: {healthy}/{len(self.accounts)} 正常")

    # ---- 聊天 ----

    async def chat_stream(
        self,
        message: str,
        model: str = "claude-sonnet-4-5",
        conversation_id: str = None,
        thinking: bool = False,
        account: Account = None
    ) -> AsyncGenerator[dict, None]:
        """使用号池聊天（流式）"""
        # 获取账号
        acc = account or self.get_next_account()
        if not acc:
            yield {"type": "error", "message": "没有可用账号"}
            return

        connector = aiohttp.TCPConnector(ssl=self._ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            payload = {"message": message, "model": model, "thinking": thinking}
            if conversation_id:
                payload["conversationId"] = conversation_id

            try:
                async with session.post(
                    f"{self.BASE_URL}/api/chat/stream",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {acc.token}",
                        "Content-Type": "application/json",
                        "Accept": "text/event-stream"
                    },
                    timeout=aiohttp.ClientTimeout(total=120)
                ) as resp:
                    if resp.status == 401:
                        # Token 失效，重新登录
                        if await self.login_account(acc.email):
                            async for event in self.chat_stream(message, model, conversation_id, thinking, acc):
                                yield event
                        else:
                            self.mark_error(acc.email, "error")
                            yield {"type": "error", "message": "登录失败"}
                        return

                    if resp.status == 429:
                        self.mark_error(acc.email, "rate_limit")
                        # 尝试其他账号
                        new_acc = self.get_next_account()
                        if new_acc and new_acc.email != acc.email:
                            async for event in self.chat_stream(message, model, conversation_id, thinking, new_acc):
                                yield event
                        else:
                            yield {"type": "error", "message": "所有账号被限流"}
                        return

                    if resp.status != 200:
                        self.mark_error(acc.email)
                        error = await resp.text()
                        yield {"type": "error", "message": f"请求失败: {error[:100]}"}
                        return

                    # 成功，标记
                    self.mark_success(acc.email)

                    # 解析 SSE
                    buffer = ""
                    async for chunk in resp.content:
                        buffer += chunk.decode('utf-8')
                        while '\n' in buffer:
                            line, buffer = buffer.split('\n', 1)
                            line = line.strip()
                            if line.startswith('data: '):
                                data = line[6:]
                                if data and data != '[DONE]':
                                    try:
                                        event = json.loads(data)
                                        event["_account"] = acc.email
                                        yield event
                                    except:
                                        pass

            except asyncio.TimeoutError:
                self.mark_error(acc.email)
                yield {"type": "error", "message": "请求超时"}
            except Exception as e:
                self.mark_error(acc.email)
                yield {"type": "error", "message": str(e)}

    async def chat(self, message: str, model: str = "claude-sonnet-4-5") -> dict:
        """同步聊天"""
        result = {"success": False, "response": "", "account": ""}

        async for event in self.chat_stream(message, model):
            t = event.get("type")
            if t == "error":
                result["message"] = event.get("message")
                return result
            elif t == "text":
                result["response"] += event.get("text", "")
            elif t == "done":
                result["response"] = event.get("full_response", result["response"])
                result["input_tokens"] = event.get("input_tokens", 0)
                result["output_tokens"] = event.get("output_tokens", 0)
            elif t == "conversation_id":
                result["conversation_id"] = event.get("id")

            if "_account" in event:
                result["account"] = event["_account"]

        result["success"] = True
        return result

    def get_stats(self) -> dict:
        """获取统计信息"""
        stats = {
            "total": len(self.accounts),
            "active": len(self._active_list),
            "by_status": {},
            "total_requests": 0,
            "total_errors": 0
        }

        for acc in self.accounts.values():
            status = acc.status.value
            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1
            stats["total_requests"] += acc.request_count
            stats["total_errors"] += acc.error_count

        return stats


# ============ API 服务器 ============

class PoolServer:
    """号池 API 服务器"""

    def __init__(self, host: str = "0.0.0.0", port: int = 8000):
        self.host = host
        self.port = port
        self.pool = AccountPool()
        self.app = web.Application()
        self._setup_routes()

    def _setup_routes(self):
        # 静态文件和前端页面
        self.app.router.add_get("/", self.handle_dashboard)
        self.app.router.add_get("/api", self.handle_api_info)
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_get("/stats", self.handle_stats)

        # 账号管理
        self.app.router.add_post("/api/pool/register", self.handle_register)
        self.app.router.add_post("/api/pool/login", self.handle_login)
        self.app.router.add_post("/api/pool/health", self.handle_health_check)
        self.app.router.add_get("/api/pool/accounts", self.handle_list_accounts)
        self.app.router.add_post("/api/pool/add", self.handle_add_account)
        self.app.router.add_delete("/api/pool/remove/{email}", self.handle_remove_account)

        # 聊天
        self.app.router.add_post("/api/chat", self.handle_chat)
        self.app.router.add_post("/api/chat/stream", self.handle_chat_stream)

        # OpenAI 兼容
        self.app.router.add_post("/v1/chat/completions", self.handle_openai)
        self.app.router.add_get("/v1/models", self.handle_models)

    async def handle_dashboard(self, request: web.Request) -> web.Response:
        """返回前端管理页面"""
        web_dir = Path(__file__).parent / "web"
        index_file = web_dir / "index.html"
        if index_file.exists():
            return web.FileResponse(index_file)
        else:
            return web.Response(text="前端页面未找到，请访问 /api 查看API信息", content_type="text/html")

    async def handle_api_info(self, request: web.Request) -> web.Response:
        return web.json_response({
            "service": "OpenClaude Pool Server",
            "version": "2.0.0",
            "stats": self.pool.get_stats(),
            "endpoints": {
                "GET /": "管理界面",
                "POST /api/pool/register": "批量注册 {count, concurrent?}",
                "POST /api/pool/login": "登录所有账号",
                "POST /api/pool/health": "健康检查",
                "GET /api/pool/accounts": "列出账号",
                "POST /api/pool/add": "添加已有账号 {email, password}",
                "POST /api/chat": "同步聊天 {message, model?}",
                "POST /api/chat/stream": "流式聊天 (SSE)",
                "POST /v1/chat/completions": "OpenAI 兼容接口",
                "GET /v1/models": "模型列表"
            }
        })

    async def handle_health_check(self, request: web.Request) -> web.Response:
        """执行健康检查"""
        await self.pool.health_check_all()
        return web.json_response({"success": True, "stats": self.pool.get_stats()})

    async def handle_health(self, request: web.Request) -> web.Response:
        stats = self.pool.get_stats()
        return web.json_response({
            "status": "ok" if stats["active"] > 0 else "degraded",
            "active_accounts": stats["active"],
            "total_accounts": stats["total"]
        })

    async def handle_stats(self, request: web.Request) -> web.Response:
        return web.json_response(self.pool.get_stats())

    async def handle_register(self, request: web.Request) -> web.Response:
        """批量注册"""
        try:
            data = await request.json()
            count = data.get("count", 5)
            concurrent = data.get("concurrent", 3)

            accounts = await self.pool.register_batch(count, concurrent)
            await self.pool.login_all()

            return web.json_response({
                "success": True,
                "registered": len(accounts),
                "accounts": [{"email": a.email, "password": a.password} for a in accounts]
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_login(self, request: web.Request) -> web.Response:
        """登录所有账号"""
        await self.pool.login_all()
        return web.json_response({
            "success": True,
            "active": len(self.pool._active_list)
        })

    async def handle_list_accounts(self, request: web.Request) -> web.Response:
        """列出账号"""
        accounts = []
        for acc in self.pool.accounts.values():
            accounts.append({
                "email": acc.email,
                "password": acc.password,
                "status": acc.status.value,
                "request_count": acc.request_count,
                "error_count": acc.error_count,
                "last_used": acc.last_used,
                "created_at": acc.created_at
            })
        return web.json_response({"accounts": accounts, "total": len(accounts)})

    async def handle_add_account(self, request: web.Request) -> web.Response:
        """添加已有账号"""
        try:
            data = await request.json()
            email = data.get("email")
            password = data.get("password")

            if not email or not password:
                return web.json_response({"error": "需要 email 和 password"}, status=400)

            acc = Account(email=email, password=password)
            self.pool.accounts[email] = acc

            if await self.pool.login_account(email):
                self.pool.save()
                return web.json_response({"success": True, "message": f"账号 {email} 添加成功"})
            else:
                del self.pool.accounts[email]
                return web.json_response({"success": False, "message": "登录失败"}, status=401)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_remove_account(self, request: web.Request) -> web.Response:
        """移除账号"""
        email = request.match_info.get("email")
        if email in self.pool.accounts:
            del self.pool.accounts[email]
            self.pool._refresh_active_list()
            self.pool.save()
            return web.json_response({"success": True})
        return web.json_response({"error": "账号不存在"}, status=404)

    async def handle_chat(self, request: web.Request) -> web.Response:
        """同步聊天"""
        try:
            data = await request.json()
            message = data.get("message")
            if not message:
                return web.json_response({"error": "需要 message"}, status=400)

            result = await self.pool.chat(
                message=message,
                model=data.get("model", "claude-sonnet-4-5")
            )
            return web.json_response(result)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_chat_stream(self, request: web.Request) -> web.StreamResponse:
        """流式聊天"""
        try:
            data = await request.json()
            message = data.get("message")
            if not message:
                return web.json_response({"error": "需要 message"}, status=400)

            response = web.StreamResponse(
                status=200,
                headers={
                    "Content-Type": "text/event-stream",
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive"
                }
            )
            await response.prepare(request)

            async for event in self.pool.chat_stream(
                message=message,
                model=data.get("model", "claude-sonnet-4-5"),
                conversation_id=data.get("conversation_id"),
                thinking=data.get("thinking", False)
            ):
                sse = f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                await response.write(sse.encode())

            await response.write(b"data: [DONE]\n\n")
            return response
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_openai(self, request: web.Request) -> web.Response:
        """OpenAI 兼容接口"""
        try:
            data = await request.json()
            messages = data.get("messages", [])
            stream = data.get("stream", False)

            # 提取用户消息
            user_message = ""
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    user_message = msg.get("content", "")
                    break

            if not user_message:
                return web.json_response({"error": "没有用户消息"}, status=400)

            # 模型映射
            model_map = {
                "gpt-3.5-turbo": "claude-haiku-4-5",
                "gpt-4": "claude-sonnet-4-5",
                "gpt-4-turbo": "claude-sonnet-4-5",
                "gpt-4o": "claude-sonnet-4-5",
                "claude-3-opus": "claude-opus-4-5",
                "claude-3-sonnet": "claude-sonnet-4-5",
                "claude-3-haiku": "claude-haiku-4-5",
            }
            model = data.get("model", "gpt-4")
            actual_model = model_map.get(model, "claude-sonnet-4-5")

            if stream:
                response = web.StreamResponse(
                    status=200,
                    headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}
                )
                await response.prepare(request)

                async for event in self.pool.chat_stream(user_message, actual_model):
                    if event.get("type") == "text":
                        chunk = {
                            "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": model,
                            "choices": [{
                                "index": 0,
                                "delta": {"content": event.get("text", "")},
                                "finish_reason": None
                            }]
                        }
                        await response.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    elif event.get("type") == "done":
                        chunk = {
                            "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": model,
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
                        }
                        await response.write(f"data: {json.dumps(chunk)}\n\n".encode())

                await response.write(b"data: [DONE]\n\n")
                return response
            else:
                result = await self.pool.chat(user_message, actual_model)

                return web.json_response({
                    "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": result.get("response", "")},
                        "finish_reason": "stop"
                    }],
                    "usage": {
                        "prompt_tokens": result.get("input_tokens", 0),
                        "completion_tokens": result.get("output_tokens", 0),
                        "total_tokens": result.get("input_tokens", 0) + result.get("output_tokens", 0)
                    }
                })
        except Exception as e:
            logger.error(f"OpenAI接口错误: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def handle_models(self, request: web.Request) -> web.Response:
        """模型列表"""
        models = [
            {"id": "claude-haiku-4-5", "object": "model", "owned_by": "anthropic"},
            {"id": "claude-sonnet-4-5", "object": "model", "owned_by": "anthropic"},
            {"id": "claude-opus-4-5", "object": "model", "owned_by": "anthropic"},
            {"id": "gpt-3.5-turbo", "object": "model", "owned_by": "openai"},
            {"id": "gpt-4", "object": "model", "owned_by": "openai"},
            {"id": "gpt-4o", "object": "model", "owned_by": "openai"},
        ]
        return web.json_response({"object": "list", "data": models})

    async def start(self):
        """启动服务器"""
        # 加载号池
        self.pool.load()

        # 登录所有账号
        if self.pool.accounts:
            await self.pool.login_all()

        # 启动定时健康检查
        asyncio.create_task(self._health_check_loop())

        # 启动服务
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()

        logger.info(f"服务器启动: http://{self.host}:{self.port}")

        while True:
            await asyncio.sleep(3600)

    async def _health_check_loop(self):
        """定时健康检查"""
        while True:
            await asyncio.sleep(self.pool.health_check_interval)
            await self.pool.health_check_all()
            self.pool.save()


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="OpenClaude 号池轮询服务")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", "-p", type=int, default=8000, help="端口")
    parser.add_argument("--register", "-r", type=int, default=0, help="启动时注册账号数量")
    parser.add_argument("--pool-file", default="account_pool.json", help="号池文件路径")

    args = parser.parse_args()

    server = PoolServer(host=args.host, port=args.port)
    server.pool.pool_file = args.pool_file

    print("=" * 60)
    print("OpenClaude 号池轮询系统 v2.0")
    print("=" * 60)

    # 启动时注册
    if args.register > 0:
        print(f"\n正在注册 {args.register} 个账号...")
        await server.pool.register_batch(args.register)

    print(f"\n服务地址: http://{args.host}:{args.port}")
    print("\n功能特性:")
    print("  - 轮询负载均衡")
    print("  - 自动故障转移")
    print("  - 健康检查")
    print("  - OpenAI 兼容接口")
    print("\nAPI 端点:")
    print("  POST /api/pool/register  - 批量注册")
    print("  POST /api/chat           - 聊天")
    print("  POST /v1/chat/completions - OpenAI兼容")
    print("=" * 60 + "\n")

    await server.start()


if __name__ == "__main__":
    asyncio.run(main())
