"""BaseCrawler - 所有爬虫的抽象基类."""

import logging
import random
import time
from typing import List, Optional
from abc import ABC, abstractmethod

import requests
import yaml

from crawlers.trade_record import TradeRecord


class BaseCrawler(ABC):
    """提供 session 管理、重试、限速、日志的基类."""

    def __init__(self, config_path: str = None):
        self.config = self._load_config(config_path)
        self.logger = logging.getLogger(self.__class__.__name__)
        self.session = self._create_session()
        self._last_request_time = 0.0

    def _load_config(self, config_path: str = None) -> dict:
        if config_path is None:
            import os
            config_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "config.yaml",
            )
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _create_session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        })
        crawl_cfg = self.config.get("crawl", {})
        timeout = crawl_cfg.get("timeout_seconds", 30)
        s.timeout = timeout
        return s

    def _rate_limit(self, interval: float = None):
        """请求间隔控制，带随机抖动."""
        if interval is None:
            interval = self.config.get("crawl", {}).get("request_interval_seconds", 2.0)
        elapsed = time.time() - self._last_request_time
        if elapsed < interval:
            jitter = random.uniform(0, interval * 0.5)
            sleep_time = interval - elapsed + jitter
            time.sleep(max(0, sleep_time))
        self._last_request_time = time.time()

    def _request_with_retry(
        self,
        method: str,
        url: str,
        max_retries: int = None,
        **kwargs,
    ) -> Optional[requests.Response]:
        """带指数退避的 HTTP 请求."""
        if max_retries is None:
            max_retries = self.config.get("crawl", {}).get("max_retries", 3)

        last_exc = None
        for attempt in range(max_retries + 1):
            try:
                self._rate_limit()
                resp = self.session.request(method, url, **kwargs)
                if resp.status_code == 412:
                    self.logger.warning(
                        f"HTTP 412 WAF challenge from {url[:80]} (attempt {attempt+1})"
                    )
                    return resp
                if resp.status_code == 429:
                    wait = 2 ** attempt * 10
                    self.logger.warning(f"HTTP 429 rate limited, waiting {wait}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp
            except requests.HTTPError as e:
                if e.response is not None and 400 <= e.response.status_code < 500:
                    self.logger.error(f"HTTP client error: {e}")
                    return e.response
                last_exc = e
            except (requests.ConnectionError, requests.Timeout) as e:
                last_exc = e
                self.logger.warning(f"Network error (attempt {attempt+1}): {e}")

            if attempt < max_retries:
                wait = 2 ** attempt
                self.logger.info(f"Retrying in {wait}s...")
                time.sleep(wait)

        self.logger.error(f"All {max_retries+1} attempts failed for {url}: {last_exc}")
        return None

    @abstractmethod
    def fetch(self) -> List[TradeRecord]:
        """子类实现：获取数据并返回 TradeRecord 列表."""
        ...

    @property
    @abstractmethod
    def source_name(self) -> str:
        """子类实现：返回数据源名称."""
        ...
