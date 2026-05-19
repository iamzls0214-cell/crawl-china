"""Playwright cookie 管理器 - 获取 stats.customs.gov.cn 的瑞数6 WAF cookie."""

import json
import logging
import os
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)

CUSTOMS_URL = "http://stats.customs.gov.cn"


def _extract_cookies_from_playwright(cache_path: str) -> Optional[Dict[str, str]]:
    """用 Playwright 启动浏览器，访问 stats.customs.gov.cn 获取瑞数6 cookie."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("playwright not installed. Run: pip install playwright && playwright install chromium")
        return None

    logger.info("Launching Playwright to obtain customs cookies...")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                locale="zh-CN",
            )
            page = context.new_page()

            # 访问首页，等待瑞数6 JS 执行完毕
            page.goto(CUSTOMS_URL, wait_until="domcontentloaded", timeout=60000)
            # 等待足够时间让瑞数6生成 cookie
            page.wait_for_timeout(8000)

            # 可能还需要再等待页面加载完成
            try:
                page.wait_for_load_state("networkidle", timeout=30000)
            except Exception:
                pass

            # 提取所有 cookie
            cookies = context.cookies()
            browser.close()

            cookie_dict = {}
            for c in cookies:
                cookie_dict[c["name"]] = c["value"]

            if cookie_dict:
                logger.info(f"Obtained {len(cookie_dict)} cookies from customs site")
                # 缓存到文件
                cache = {
                    "cookies": cookie_dict,
                    "timestamp": time.time(),
                }
                with open(cache_path, "w") as f:
                    json.dump(cache, f, indent=2)
                return cookie_dict
            else:
                logger.warning("Playwright returned empty cookie set")
                return None

    except Exception as e:
        logger.error(f"Playwright cookie extraction failed: {e}")
        return None


def get_customs_cookies(
    cache_path: str = "/tmp/customs_cookies.json",
    ttl_minutes: int = 30,
    force_refresh: bool = False,
) -> Optional[Dict[str, str]]:
    """获取海关统计平台 cookie，优先使用缓存.

    Args:
        cache_path: cookie 缓存文件路径
        ttl_minutes: 缓存有效期（分钟）
        force_refresh: 强制刷新 cookie

    Returns:
        cookie 字典，失败返回 None
    """
    # 检查缓存
    if not force_refresh and os.path.exists(cache_path):
        try:
            with open(cache_path, "r") as f:
                cache = json.load(f)
            age = time.time() - cache.get("timestamp", 0)
            if age < ttl_minutes * 60:
                logger.info(f"Using cached cookies (age: {age:.0f}s)")
                return cache.get("cookies", {})
            else:
                logger.info(f"Cookie cache expired (age: {age:.0f}s), refreshing...")
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Corrupted cookie cache: {e}")

    # 获取新 cookie
    return _extract_cookies_from_playwright(cache_path)


def cookie_dict_to_header(cookies: Dict[str, str]) -> str:
    """将 cookie 字典转为 HTTP Cookie header 字符串."""
    return "; ".join(f"{k}={v}" for k, v in cookies.items())
