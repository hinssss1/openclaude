#!/usr/bin/env python3
"""
OpenClaude API 代理服务
======================
提供 RESTful API 直接访问 Claude 服务，无需网页
"""

import asyncio
import aiohttp
import ssl
import certifi
import json
import uuid
from datetime import datetime
from typing import Optional, AsyncGenerator
from dataclasses import dataclass, asdict
from aiohttp import web
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


@dataclass
class Account:
    """账户信息"""
    email: str
    password: str
    session_token: Optional[str] = None
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


class OpenClaudeClient:
    """OpenClaude API 客户端"""

    BASE_URL = "https://openclaude.me"

    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self.session: Optional[aiohttp.ClientSession] = None
        self.cookies = None
        self.logged_in = False

    async def _ensure_session(self):
        """确保session存在"""
        if self.session is None or self.session.closed:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            self.session = aiohttp.ClientSession(
                connector=connector,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    "Accept": "application/json",
                    "Origin": self.BASE_URL,
                    "Referer": f"{self.BASE_URL}/"
                }
            )

    async def login(self) -> bool:
        """登录获取session"""
        await self._ensure_session()

        try:
            async with self.session.post(
                f"{self.BASE_URL}/api/auth/login",
                json={"email": self.email, "password": self.password}
            ) as resp:
                if resp.status == 200:
                    self.cookies = resp.cookies
                    self.logged_in = True
                    logger.info(f"登录成功: {self.email}")
                    return True
                else:
                    data = await resp.text()
                    logger.error(f"登录失败: {data}")
                    return False
        except Exception as e:
            logger.error(f"登录异常: {e}")
            return False

    async def chat(
        self,
        message: str,
        conversation_id: Optional[str] = None,
        model: str = "claude-sonnet-4-5",
        thinking: bool = False
    ) -> AsyncGenerator[dict, None]:
        """
        发送聊天消息（流式响应）

        Args:
            message: 用户消息
            conversation_id: 会话ID（可选，不传则创建新会话）
            model: 模型名称
            thinking: 是否启用扩展思考

        Yields:
            SSE事件数据
        """
        if not self.logged_in:
            if not await self.login():
                yield {"type": "error", "message": "登录失败"}
                return

        await self._ensure_session()

        payload = {
            "message": message,
            "model": model,
            "thinking": thinking
        }

        if conversation_id:
            payload["conversationId"] = conversation_id

        try:
            async with self.session.post(
                f"{self.BASE_URL}/api/chat/stream",
                json=payload,
                headers={"Accept": "text/event-stream"}
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    yield {"type": "error", "message": f"请求失败: {error_text}"}
                    return

                # 解析SSE流
                async for line in resp.content:
                    line = line.decode('utf-8').strip()
                    if line.startswith('data: '):
                        data = line[6:]
                        if data:
                            try:
                                event = json.loads(data)
                                yield event
                            except json.JSONDecodeError:
                                yield {"type": "text", "text": data}
                    elif line and not line.startswith(':'):
                        # 非SSE格式，可能是纯文本
                        try:
                            event = json.loads(line)
                            yield event
                        except:
                            pass

        except Exception as e:
            yield {"type": "error", "message": str(e)}

    async def chat_sync(
        self,
        message: str,
        conversation_id: Optional[str] = None,
        model: str = "claude-sonnet-4-5",
        thinking: bool = False
    ) -> dict:
        """
        发送聊天消息（同步响应，等待完整回复）
        """
        full_response = ""
        result = {
            "success": False,
            "message": "",
            "response": "",
            "conversation_id": None,
            "input_tokens": 0,
            "output_tokens": 0
        }

        async for event in self.chat(message, conversation_id, model, thinking):
            event_type = event.get("type", "")

            if event_type == "error":
                result["message"] = event.get("message", "未知错误")
                return result
            elif event_type == "start":
                result["input_tokens"] = event.get("input_tokens", 0)
            elif event_type == "text":
                full_response += event.get("text", "")
            elif event_type == "done":
                result["output_tokens"] = event.get("output_tokens", 0)
                result["response"] = event.get("full_response", full_response)
            elif event_type == "conversation_id":
                result["conversation_id"] = event.get("id")

        result["success"] = True
        result["message"] = "成功"
        if not result["response"]:
            result["response"] = full_response

        return result

    async def close(self):
        """关闭连接"""
        if self.session and not self.session.closed:
            await self.session.close()


class AccountPool:
    """账户池管理"""

    def __init__(self):
        self.accounts: dict[str, OpenClaudeClient] = {}
        self.default_account: Optional[str] = None

    async def add_account(self, email: str, password: str) -> bool:
        """添加账户到池"""
        client = OpenClaudeClient(email, password)
        if await client.login():
            self.accounts[email] = client
            if self.default_account is None:
                self.default_account = email
            return True
        return False

    def get_client(self, email: Optional[str] = None) -> Optional[OpenClaudeClient]:
        """获取客户端"""
        if email:
            return self.accounts.get(email)
        elif self.default_account:
            return self.accounts.get(self.default_account)
        return None

    async def close_all(self):
        """关闭所有连接"""
        for client in self.accounts.values():
            await client.close()


# ========== API 服务器 ==========

class APIServer:
    """API 服务器"""

    def __init__(self, host: str = "0.0.0.0", port: int = 8000):
        self.host = host
        self.port = port
        self.pool = AccountPool()
        self.app = web.Application()
        self._setup_routes()

    def _setup_routes(self):
        """设置路由"""
        self.app.router.add_get("/", self.handle_index)
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_post("/api/account/add", self.handle_add_account)
        self.app.router.add_get("/api/account/list", self.handle_list_accounts)
        self.app.router.add_post("/api/chat", self.handle_chat)
        self.app.router.add_post("/api/chat/stream", self.handle_chat_stream)
        self.app.router.add_post("/v1/chat/completions", self.handle_openai_compatible)

    async def handle_index(self, request: web.Request) -> web.Response:
        """首页"""
        info = {
            "service": "OpenClaude API Proxy",
            "version": "1.0.0",
            "endpoints": {
                "POST /api/account/add": "添加账户 {email, password}",
                "GET /api/account/list": "列出账户",
                "POST /api/chat": "同步聊天 {message, model?, conversation_id?, thinking?, account?}",
                "POST /api/chat/stream": "流式聊天（SSE）",
                "POST /v1/chat/completions": "OpenAI 兼容接口"
            }
        }
        return web.json_response(info)

    async def handle_health(self, request: web.Request) -> web.Response:
        """健康检查"""
        return web.json_response({"status": "ok", "accounts": len(self.pool.accounts)})

    async def handle_add_account(self, request: web.Request) -> web.Response:
        """添加账户"""
        try:
            data = await request.json()
            email = data.get("email")
            password = data.get("password")

            if not email or not password:
                return web.json_response({"error": "需要 email 和 password"}, status=400)

            success = await self.pool.add_account(email, password)
            if success:
                return web.json_response({"success": True, "message": f"账户 {email} 添加成功"})
            else:
                return web.json_response({"success": False, "message": "登录失败"}, status=401)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_list_accounts(self, request: web.Request) -> web.Response:
        """列出账户"""
        accounts = list(self.pool.accounts.keys())
        return web.json_response({
            "accounts": accounts,
            "default": self.pool.default_account,
            "count": len(accounts)
        })

    async def handle_chat(self, request: web.Request) -> web.Response:
        """同步聊天"""
        try:
            data = await request.json()
            message = data.get("message")

            if not message:
                return web.json_response({"error": "需要 message 参数"}, status=400)

            account = data.get("account")
            client = self.pool.get_client(account)

            if not client:
                return web.json_response({"error": "没有可用账户，请先添加账户"}, status=400)

            result = await client.chat_sync(
                message=message,
                conversation_id=data.get("conversation_id"),
                model=data.get("model", "claude-sonnet-4-20250514"),
                thinking=data.get("thinking", False)
            )

            return web.json_response(result)
        except Exception as e:
            logger.error(f"聊天错误: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def handle_chat_stream(self, request: web.Request) -> web.StreamResponse:
        """流式聊天（SSE）"""
        try:
            data = await request.json()
            message = data.get("message")

            if not message:
                return web.json_response({"error": "需要 message 参数"}, status=400)

            account = data.get("account")
            client = self.pool.get_client(account)

            if not client:
                return web.json_response({"error": "没有可用账户"}, status=400)

            response = web.StreamResponse(
                status=200,
                headers={
                    "Content-Type": "text/event-stream",
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive"
                }
            )
            await response.prepare(request)

            async for event in client.chat(
                message=message,
                conversation_id=data.get("conversation_id"),
                model=data.get("model", "claude-sonnet-4-20250514"),
                thinking=data.get("thinking", False)
            ):
                sse_data = f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                await response.write(sse_data.encode('utf-8'))

            await response.write(b"data: [DONE]\n\n")
            return response

        except Exception as e:
            logger.error(f"流式聊天错误: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def handle_openai_compatible(self, request: web.Request) -> web.Response:
        """OpenAI 兼容接口"""
        try:
            data = await request.json()
            messages = data.get("messages", [])
            stream = data.get("stream", False)

            # 提取最后一条用户消息
            user_message = ""
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    user_message = msg.get("content", "")
                    break

            if not user_message:
                return web.json_response({"error": "没有用户消息"}, status=400)

            client = self.pool.get_client()
            if not client:
                return web.json_response({"error": "没有可用账户"}, status=400)

            model = data.get("model", "claude-sonnet-4-20250514")

            if stream:
                # 流式响应
                response = web.StreamResponse(
                    status=200,
                    headers={
                        "Content-Type": "text/event-stream",
                        "Cache-Control": "no-cache"
                    }
                )
                await response.prepare(request)

                async for event in client.chat(user_message, model=model):
                    if event.get("type") == "text":
                        chunk = {
                            "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
                            "object": "chat.completion.chunk",
                            "created": int(datetime.now().timestamp()),
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
                            "created": int(datetime.now().timestamp()),
                            "model": model,
                            "choices": [{
                                "index": 0,
                                "delta": {},
                                "finish_reason": "stop"
                            }]
                        }
                        await response.write(f"data: {json.dumps(chunk)}\n\n".encode())

                await response.write(b"data: [DONE]\n\n")
                return response
            else:
                # 同步响应
                result = await client.chat_sync(user_message, model=model)

                response_data = {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
                    "object": "chat.completion",
                    "created": int(datetime.now().timestamp()),
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": result.get("response", "")
                        },
                        "finish_reason": "stop"
                    }],
                    "usage": {
                        "prompt_tokens": result.get("input_tokens", 0),
                        "completion_tokens": result.get("output_tokens", 0),
                        "total_tokens": result.get("input_tokens", 0) + result.get("output_tokens", 0)
                    }
                }
                return web.json_response(response_data)

        except Exception as e:
            logger.error(f"OpenAI兼容接口错误: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def start(self):
        """启动服务器"""
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        logger.info(f"API服务器启动: http://{self.host}:{self.port}")

        # 保持运行
        while True:
            await asyncio.sleep(3600)


async def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="OpenClaude API 代理服务")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", "-p", type=int, default=8000, help="监听端口")
    parser.add_argument("--account", "-a", nargs=2, metavar=("EMAIL", "PASSWORD"),
                        action="append", help="预加载账户")

    args = parser.parse_args()

    server = APIServer(host=args.host, port=args.port)

    # 预加载账户
    if args.account:
        for email, password in args.account:
            await server.pool.add_account(email, password)

    print("=" * 60)
    print("OpenClaude API 代理服务")
    print("=" * 60)
    print(f"\n服务地址: http://{args.host}:{args.port}")
    print("\nAPI 端点:")
    print("  POST /api/account/add     - 添加账户")
    print("  GET  /api/account/list    - 列出账户")
    print("  POST /api/chat            - 同步聊天")
    print("  POST /api/chat/stream     - 流式聊天(SSE)")
    print("  POST /v1/chat/completions - OpenAI兼容接口")
    print("\n" + "=" * 60)

    await server.start()


if __name__ == "__main__":
    asyncio.run(main())
