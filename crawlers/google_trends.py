"""Google Trends 爬虫 - 三级 fallback 策略."""

import logging
import time
from datetime import datetime, timedelta
from typing import List

from crawlers.base import BaseCrawler
from crawlers.trade_record import TradeRecord


class GoogleTrendsCrawler(BaseCrawler):
    """pytrends → curl_cffi → 离线占位 三级 fallback."""

    def __init__(self, config_path: str = None):
        super().__init__(config_path)
        self.keywords_config = self.config.get("google_trends", {}).get("keywords", {})

    @property
    def source_name(self) -> str:
        return "google_trends"

    def fetch(self) -> List[TradeRecord]:
        all_records = []

        for group_name, keywords in self.keywords_config.items():
            self.logger.info(f"Fetching Google Trends for {group_name}: {keywords}")

            records = self._try_pytrends(group_name, keywords)
            if records:
                all_records.extend(records)
                continue

            self.logger.warning(f"pytrends failed for {group_name}, trying curl_cffi...")
            records = self._try_curl_cffi(group_name, keywords)
            if records:
                all_records.extend(records)
                continue

            self.logger.error(f"All Trend methods failed for {group_name}, generating placeholder")
            all_records.extend(self._generate_placeholder(group_name, keywords))

        return all_records

    def _try_pytrends(self, group_name: str, keywords: list) -> List[TradeRecord]:
        """尝试 pytrends 库."""
        try:
            from pytrends.request import TrendReq
        except ImportError:
            self.logger.warning("  pytrends not installed")
            return []

        trend_cfg = self.config.get("google_trends", {})
        proxy = trend_cfg.get("proxy")
        timeout_val = trend_cfg.get("timeout_seconds", 45)

        requests_args = {
            "headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
            },
            "timeout": timeout_val,
        }
        if proxy:
            requests_args["proxies"] = {"http": proxy, "https": proxy}

        try:
            pytrends = TrendReq(
                hl="en-US",
                tz=360,
                retries=2,
                backoff_factor=1.0,
                requests_args=requests_args,
            )

            time.sleep(5)

            pytrends.build_payload(
                kw_list=keywords,
                cat=0,
                timeframe="today 5-y",
                geo="",
                gprop="",
            )

            time.sleep(3)

            interest_df = pytrends.interest_over_time()
            if interest_df is None or interest_df.empty:
                self.logger.warning(f"  {group_name}: pytrends returned empty DataFrame")
                return []

            return self._parse_trends_df(interest_df, group_name)

        except Exception as e:
            err_msg = str(e)
            if "429" in err_msg or "rate limit" in err_msg.lower():
                self.logger.warning(f"  {group_name}: pytrends rate limited - {e}")
            elif "timeout" in err_msg.lower() or "timed out" in err_msg.lower():
                self.logger.warning(f"  {group_name}: pytrends timeout (Google inaccessible without proxy)")
            else:
                self.logger.error(f"  {group_name}: pytrends error - {e}")
            return []

    def _try_curl_cffi(self, group_name: str, keywords: list) -> List[TradeRecord]:
        """尝试用 curl_cffi 直接请求 Google Trends API."""
        try:
            from curl_cffi import requests as cffi_requests
            import json
            from urllib.parse import quote

            records = []
            for keyword in keywords:
                # 使用 Google Trends explore API
                # 注意：这是非官方 API，可能随时变化
                try:
                    # 先获取 token
                    explore_url = "https://trends.google.com/trends/api/explore"
                    params = {
                        "hl": "en-US",
                        "tz": "360",
                        "req": json.dumps({
                            "comparisonItem": [
                                {
                                    "keyword": keyword,
                                    "geo": "",
                                    "time": "today 5-y",
                                }
                            ],
                            "category": 0,
                            "property": "",
                        }),
                    }

                    resp = cffi_requests.get(
                        explore_url,
                        params=params,
                        impersonate="chrome124",
                        timeout=30,
                    )
                    # Google 返回前5个字符是 ")]}',\n"
                    text = resp.text
                    if text.startswith(")]}'"):
                        text = text[5:]
                    data = json.loads(text)

                    widgets = data.get("widgets", [])
                    if not widgets:
                        continue

                    token = widgets[0].get("token", "")
                    if not token:
                        continue

                    # 获取时间线数据
                    timeline_url = "https://trends.google.com/trends/api/widgetdata/multiline"
                    req_data = json.dumps({
                        "req": json.dumps({
                            "time": "today 5-y",
                            "resolution": "WEEK",
                            "locale": "en-US",
                            "comparisonItem": [{"geo": {}, "complexKeywordsRestriction": {"keyword": [{"type": "KEYWORD", "value": keyword}]}}],
                        }),
                        "token": token,
                        "tz": "360",
                    })

                    time.sleep(3)
                    resp2 = cffi_requests.post(
                        timeline_url,
                        data={"req": json.dumps({
                            "time": "today 5-y",
                            "resolution": "WEEK",
                            "locale": "en-US",
                            "comparisonItem": [{"geo": {}, "complexKeywordsRestriction": {"keyword": [{"type": "KEYWORD", "value": keyword}]}}],
                        }), "token": token, "tz": "360"},
                        impersonate="chrome124",
                        timeout=30,
                    )

                    text2 = resp2.text
                    if text2.startswith(")]}'"):
                        text2 = text2[5:]
                    trend_data = json.loads(text2)

                    # 解析时间序列
                    timeline = trend_data.get("default", {}).get("timelineData", [])
                    for point in timeline:
                        ts = point.get("time", "")
                        val = point.get("value", [0])[0]
                        try:
                            dt = datetime.fromtimestamp(int(ts))
                            period = dt.strftime("%Y%m")
                        except (ValueError, TypeError):
                            continue

                        records.append(TradeRecord(
                            source="google_trends",
                            country=group_name,
                            direction="trend",
                            period=period,
                            commodity=keyword,
                            value_usd=float(val),
                        ))

                except Exception as e:
                    self.logger.warning(f"  curl_cffi failed for '{keyword}': {e}")
                    continue
                time.sleep(5)

            return records

        except ImportError:
            self.logger.warning("  curl_cffi not installed")
            return []
        except Exception as e:
            self.logger.error(f"  {group_name}: curl_cffi error - {e}")
            return []

    def _parse_trends_df(self, df, group_name: str) -> List[TradeRecord]:
        """将 pytrends DataFrame 转为 TradeRecord."""
        records = []
        for idx, row in df.iterrows():
            period = idx.strftime("%Y%m")
            for col in df.columns:
                if col == "isPartial":
                    continue
                val = row[col]
                if val == 0 or val is None:
                    continue
                records.append(TradeRecord(
                    source="google_trends",
                    country=group_name,
                    direction="trend",
                    period=period,
                    commodity=col,
                    value_usd=float(val),
                ))
        return records

    def _generate_placeholder(self, group_name: str, keywords: list) -> List[TradeRecord]:
        """生成占位记录，标记数据获取失败."""
        today = datetime.now().strftime("%Y%m")
        records = []
        for kw in keywords:
            records.append(TradeRecord(
                source="google_trends",
                country=group_name,
                direction="trend",
                period=today,
                commodity=f"{kw}_FAILED",
                value_usd=-1,  # -1 表示获取失败
            ))
        return records
