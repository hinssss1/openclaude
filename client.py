#!/usr/bin/env python3
"""
OpenClaude API 客户端
====================
直接调用 Claude 服务的客户端库
"""

import asyncio
import aiohttp
import ssl
import certifi
import json
from typing import Optional, AsyncGenerator


class OpenClaudeClient:
    """OpenClaude 直连客户端"""

    BASE_URL = "https://openclaude.me"

    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self.session: Optional[aiohttp.ClientSession] = None
        self.token: Optional[str] = None
        self.logged_in = False

    async def __aenter__(self):
        await self.login()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            self.session = aiohttp.ClientSession(
                connector=connector,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    "Origin": self.BASE_URL,
                    "Referer": f"{self.BASE_URL}/"
                }
            )
        return self.session

    async def login(self) -> bool:
        """登录获取Token"""
        session = await self._get_session()
        try:
            async with session.post(
                f"{self.BASE_URL}/api/auth/login",
                json={"email": self.email, "password": self.password},
                headers={"Content-Type": "application/json"}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.token = data.get("auth_token")
                    self.logged_in = True
                    return True
                return False
        except Exception as e:
            print(f"登录错误: {e}")
            return False

    async def chat_stream(
        self,
        message: str,
        model: str = "claude-sonnet-4-5",
        conversation_id: Optional[str] = None,
        thinking: bool = False
    ) -> AsyncGenerator[str, None]:
        """流式聊天，逐字返回"""
        if not self.logged_in:
            if not await self.login():
                yield "[登录失败]"
                return

        session = await self._get_session()
        payload = {"message": message, "model": model, "thinking": thinking}
        if conversation_id:
            payload["conversationId"] = conversation_id

        try:
            async with session.post(
                f"{self.BASE_URL}/api/chat/stream",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream"
                }
            ) as resp:
                if resp.status != 200:
                    yield f"[错误: {resp.status}]"
                    return

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
                                    if event.get("type") == "text":
                                        yield event.get("text", "")
                                except json.JSONDecodeError:
                                    pass
        except Exception as e:
            yield f"[错误: {e}]"

    async def chat(
        self,
        message: str,
        model: str = "claude-sonnet-4-5",
        conversation_id: Optional[str] = None,
        thinking: bool = False
    ) -> str:
        """同步聊天，返回完整响应"""
        result = []
        async for text in self.chat_stream(message, model, conversation_id, thinking):
            result.append(text)
        return "".join(result)

    async def chat_full(
        self,
        message: str,
        model: str = "claude-sonnet-4-5",
        conversation_id: Optional[str] = None,
        thinking: bool = False
    ) -> dict:
        """聊天并返回完整信息（包含token统计）"""
        if not self.logged_in:
            await self.login()

        session = await self._get_session()
        payload = {"message": message, "model": model, "thinking": thinking}
        if conversation_id:
            payload["conversationId"] = conversation_id

        result = {
            "response": "",
            "conversation_id": None,
            "input_tokens": 0,
            "output_tokens": 0
        }

        async with session.post(
            f"{self.BASE_URL}/api/chat/stream",
            json=payload,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }
        ) as resp:
            async for chunk in resp.content:
                for line in chunk.decode('utf-8').split('\n'):
                    line = line.strip()
                    if line.startswith('data: '):
                        try:
                            event = json.loads(line[6:])
                            if event.get("type") == "start":
                                result["input_tokens"] = event.get("input_tokens", 0)
                            elif event.get("type") == "done":
                                result["output_tokens"] = event.get("output_tokens", 0)
                                result["response"] = event.get("full_response", "")
                            elif event.get("type") == "conversation_id":
                                result["conversation_id"] = event.get("id")
                        except:
                            pass

        return result

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


# ========== 便捷函数 ==========

async def quick_chat(email: str, password: str, message: str, model: str = "claude-sonnet-4-5") -> str:
    """快速聊天（一次性）"""
    async with OpenClaudeClient(email, password) as client:
        return await client.chat(message, model)


# ========== 命令行接口 ==========

async def interactive_mode(email: str, password: str, model: str):
    """交互式聊天模式"""
    print("正在登录...")
    async with OpenClaudeClient(email, password) as client:
        if not client.logged_in:
            print("登录失败！")
            return

        print(f"登录成功！模型: {model}")
        print("输入消息开始聊天，输入 'quit' 退出\n")

        conversation_id = None
        while True:
            try:
                user_input = input("You: ").strip()
                if user_input.lower() in ['quit', 'exit', 'q']:
                    break
                if not user_input:
                    continue

                print("Claude: ", end="", flush=True)
                async for text in client.chat_stream(user_input, model, conversation_id):
                    print(text, end="", flush=True)
                print("\n")

            except KeyboardInterrupt:
                print("\n退出...")
                break
            except EOFError:
                break


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="OpenClaude 客户端")
    parser.add_argument("--email", "-e", required=True, help="账户邮箱")
    parser.add_argument("--password", "-p", required=True, help="账户密码")
    parser.add_argument("--message", "-m", help="发送消息（不指定则进入交互模式）")
    parser.add_argument("--model", default="claude-sonnet-4-5",
                        help="模型: claude-haiku-4-5, claude-sonnet-4-5, claude-opus-4-5")

    args = parser.parse_args()

    if args.message:
        # 单次消息模式
        async with OpenClaudeClient(args.email, args.password) as client:
            print("Claude: ", end="", flush=True)
            async for text in client.chat_stream(args.message, args.model):
                print(text, end="", flush=True)
            print()
    else:
        # 交互模式
        await interactive_mode(args.email, args.password, args.model)


if __name__ == "__main__":
    asyncio.run(main())
