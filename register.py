#!/usr/bin/env python3
"""
OpenClaude.me 自动化注册系统 (HTTP 请求版)
==========================================
直接通过 API 请求注册，无需浏览器模拟
"""

import asyncio
import aiohttp
import ssl
import certifi
import random
import string
import argparse
import json
from datetime import datetime
from typing import Optional


class OpenClaudeRegister:
    """OpenClaude 自动化注册类"""

    SIGNUP_API = "https://openclaude.me/api/auth/signup"

    def __init__(self):
        self.results = []

    @staticmethod
    def generate_random_email(domain: str = "gmail.com", prefix_length: int = 10) -> str:
        """生成随机邮箱地址"""
        prefix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=prefix_length))
        timestamp = datetime.now().strftime("%m%d%H%M%S")
        return f"{prefix}{timestamp}@{domain}"

    @staticmethod
    def generate_random_password(length: int = 16) -> str:
        """生成随机密码"""
        chars = string.ascii_letters + string.digits + "!@#$%^&*"
        password = [
            random.choice(string.ascii_uppercase),
            random.choice(string.ascii_lowercase),
            random.choice(string.digits),
            random.choice("!@#$%^&*")
        ]
        password += random.choices(chars, k=length - 4)
        random.shuffle(password)
        return ''.join(password)

    async def register(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
        session: Optional[aiohttp.ClientSession] = None
    ) -> dict:
        """
        注册单个账户

        Args:
            email: 邮箱地址，为空则自动生成
            password: 密码，为空则自动生成
            session: 可选的 aiohttp session

        Returns:
            注册结果字典
        """
        email = email or self.generate_random_email()
        password = password or self.generate_random_password()

        result = {
            "email": email,
            "password": password,
            "success": False,
            "message": "",
            "timestamp": datetime.now().isoformat()
        }

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Origin": "https://openclaude.me",
            "Referer": "https://openclaude.me/"
        }

        payload = {
            "email": email,
            "password": password
        }

        close_session = False
        if session is None:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            session = aiohttp.ClientSession(connector=connector)
            close_session = True

        try:
            async with session.post(
                self.SIGNUP_API,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                result["status_code"] = response.status

                try:
                    data = await response.json()
                    result["response"] = data
                except:
                    data = await response.text()
                    result["response"] = data

                if response.status == 200:
                    result["success"] = True
                    result["message"] = "注册成功"
                elif response.status == 400:
                    result["message"] = f"请求错误: {data}"
                elif response.status == 409:
                    result["message"] = "邮箱已存在"
                else:
                    result["message"] = f"注册失败 [{response.status}]: {data}"

        except asyncio.TimeoutError:
            result["message"] = "请求超时"
        except aiohttp.ClientError as e:
            result["message"] = f"网络错误: {str(e)}"
        except Exception as e:
            result["message"] = f"未知错误: {str(e)}"
        finally:
            if close_session:
                await session.close()

        self.results.append(result)
        return result

    async def register_batch(
        self,
        count: int,
        email_domain: str = "gmail.com",
        concurrent: int = 5,
        delay: float = 0.5
    ) -> list:
        """
        批量注册账户

        Args:
            count: 注册数量
            email_domain: 邮箱域名
            concurrent: 最大并发数
            delay: 每次请求间隔(秒)

        Returns:
            注册结果列表
        """
        semaphore = asyncio.Semaphore(concurrent)

        async def limited_register(email: str, password: str, session: aiohttp.ClientSession):
            async with semaphore:
                result = await self.register(email, password, session)
                await asyncio.sleep(delay)  # 避免请求过快
                return result

        # 生成账户
        accounts = [
            (self.generate_random_email(domain=email_domain), self.generate_random_password())
            for _ in range(count)
        ]

        ssl_context = ssl.create_default_context(cafile=certifi.where())
        connector = aiohttp.TCPConnector(limit=concurrent, ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [limited_register(email, pwd, session) for email, pwd in accounts]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        # 处理异常结果
        processed = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                processed.append({
                    "email": accounts[i][0],
                    "password": accounts[i][1],
                    "success": False,
                    "message": f"异常: {str(r)}"
                })
            else:
                processed.append(r)

        return processed

    def save_results(self, filepath: str = None) -> str:
        """保存结果到 JSON 文件"""
        if filepath is None:
            filepath = f"accounts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        # 只保存成功的账户（敏感信息）
        successful = [r for r in self.results if r['success']]

        output = {
            "total": len(self.results),
            "success": len(successful),
            "failed": len(self.results) - len(successful),
            "accounts": self.results
        }

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        print(f"结果已保存: {filepath}")
        return filepath

    def print_summary(self):
        """打印统计摘要"""
        success = sum(1 for r in self.results if r['success'])
        failed = len(self.results) - success

        print("\n" + "=" * 50)
        print(f"注册统计: 成功 {success} / 失败 {failed} / 总计 {len(self.results)}")
        print("=" * 50)

        if success > 0:
            print("\n成功账户:")
            for r in self.results:
                if r['success']:
                    print(f"  邮箱: {r['email']}")
                    print(f"  密码: {r['password']}")
                    print()


async def main():
    parser = argparse.ArgumentParser(description="OpenClaude.me 自动化注册系统")
    parser.add_argument("--email", "-e", help="指定邮箱")
    parser.add_argument("--password", "-p", help="指定密码")
    parser.add_argument("--count", "-c", type=int, default=1, help="批量注册数量")
    parser.add_argument("--domain", "-d", default="gmail.com", help="邮箱域名")
    parser.add_argument("--concurrent", type=int, default=5, help="并发数")
    parser.add_argument("--delay", type=float, default=0.5, help="请求间隔(秒)")
    parser.add_argument("--output", "-o", help="输出文件路径")

    args = parser.parse_args()

    register = OpenClaudeRegister()

    print("=" * 50)
    print("OpenClaude.me 自动化注册系统 (HTTP API)")
    print("=" * 50)

    if args.count > 1:
        print(f"\n开始批量注册 {args.count} 个账户...")
        print(f"邮箱域名: {args.domain}")
        print(f"并发数: {args.concurrent}")
        print("-" * 50)

        results = await register.register_batch(
            count=args.count,
            email_domain=args.domain,
            concurrent=args.concurrent,
            delay=args.delay
        )

        for r in results:
            status = "✓" if r['success'] else "✗"
            print(f"[{status}] {r['email']}: {r['message']}")
    else:
        print("\n注册单个账户...")
        result = await register.register(email=args.email, password=args.password)

        print(f"\n邮箱: {result['email']}")
        print(f"密码: {result['password']}")
        print(f"状态: {result['message']}")

    register.print_summary()
    register.save_results(args.output)


if __name__ == "__main__":
    asyncio.run(main())
