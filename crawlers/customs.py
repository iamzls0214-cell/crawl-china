"""海关总署数据爬虫 - stats.customs.gov.cn.

策略: Playwright 获取瑞数6 cookie → 缓存 → requests 调用 API.
Fallback: 如果 stats.customs.gov.cn 不可用，回退到 MOFCOM 数据.
"""

import json
import logging
import time
from typing import List, Optional

from crawlers.base import BaseCrawler
from crawlers.trade_record import TradeRecord
from utils.cookie_manager import get_customs_cookies, cookie_dict_to_header
from utils.date_utils import generate_month_range


class CustomsCrawler(BaseCrawler):
    """爬取 stats.customs.gov.cn 海关统计数据查询平台.

    API: http://stats.customs.gov.cn/paramManager/selMainExportList
    支持按 HS 编码、国别、时间范围查询商品明细.
    """

    STATS_BASE = "http://stats.customs.gov.cn"
    STATS_API = "/paramManager/selMainExportList"

    # 重点商品对应的 HS 编码分类（两位章级）
    COMMODITY_CATEGORIES = {
        "机电产品": ["84", "85"],
        "高新技术产品": [],  # 需要具体编码
        "农产品": ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10",
                  "11", "12", "13", "14", "15", "16", "17", "18", "19", "20",
                  "21", "22", "23", "24"],
        "纺织服装": ["50", "51", "52", "53", "54", "55", "56", "57", "58", "59",
                    "60", "61", "62", "63"],
        "钢铁": ["72", "73"],
        "塑料制品": ["39"],
        "家具": ["94"],
        "鞋靴": ["64"],
    }

    def __init__(self, config_path: str = None):
        super().__init__(config_path)
        self.months_back = self.config.get("crawl", {}).get("months_back", 24)
        self.customs_cfg = self.config.get("customs", {})
        self.cookie_cache_path = self.customs_cfg.get(
            "cookie_cache_path", "/tmp/customs_cookies.json"
        )
        self.cookie_ttl = self.customs_cfg.get("cookie_cache_ttl_minutes", 30)
        self.fallback_to_mofcom = self.customs_cfg.get("fallback_to_mofcom", True)
        self._cookies: Optional[dict] = None

    @property
    def source_name(self) -> str:
        return "customs"

    def fetch(self) -> List[TradeRecord]:
        records = []

        # 尝试获取 cookie
        self._cookies = self._get_cookies()
        if self._cookies:
            records = self._fetch_via_api()
            if records:
                self.logger.info(f"Customs API returned {len(records)} records")
                return records
            self.logger.warning("Customs API returned empty, trying fallback...")
        else:
            self.logger.warning("Could not obtain customs cookies, trying fallback...")

        # HTML 页面尝试
        records = self._try_html_scrape()
        if records:
            self.logger.info(f"Customs HTML returned {len(records)} records")
            return records

        # 最终 fallback 到 MOFCOM
        if self.fallback_to_mofcom:
            self.logger.warning("Falling back to MOFCOM for customs data")
            return self._mofcom_fallback()

        return []

    def _get_cookies(self) -> Optional[dict]:
        return get_customs_cookies(
            cache_path=self.cookie_cache_path,
            ttl_minutes=self.cookie_ttl,
        )

    def _fetch_via_api(self) -> List[TradeRecord]:
        """通过 Playwright 在浏览器内调用 stats.customs.gov.cn API."""
        countries = self.config.get("countries", [])
        months = generate_month_range(self.months_back)
        records = []

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.logger.warning("playwright not installed")
            return []

        self.logger.info("Launching Playwright for customs API...")
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

                # 访问首页，让瑞数6完成 cookie 初始化
                self.logger.info("  Loading homepage...")
                page.goto("http://stats.customs.gov.cn/", wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(5000)
                try:
                    page.wait_for_load_state("networkidle", timeout=30000)
                except Exception:
                    pass

                api_url = f"{self.STATS_BASE}{self.STATS_API}"

                for country in countries:
                    country_name = country["name"]
                    country_code = country.get("code", "")

                    for ym in months:
                        try:
                            params = {
                                "pageNum": 1,
                                "pageSize": 500,
                                "tradeType": "出口",
                                "startDate": ym,
                                "endDate": ym,
                                "tradeCountry": country_code,
                                "customs": "",
                                "tradeMode": "",
                                "productCode": "",
                                "currency": "USD",
                            }

                            # 用 fetch 在浏览器上下文里调 API
                            result = page.evaluate("""
                                async (params) => {
                                    const resp = await fetch('/paramManager/selMainExportList', {
                                        method: 'POST',
                                        headers: {
                                            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                                            'X-Requested-With': 'XMLHttpRequest',
                                        },
                                        body: new URLSearchParams(params).toString(),
                                    });
                                    if (!resp.ok) return {_error: resp.status};
                                    return await resp.json();
                                }
                            """, params)

                            if isinstance(result, dict) and result.get("_error") == 412:
                                self.logger.warning(
                                    f"Customs WAF re-triggered (412) for {country_name} {ym}"
                                )
                                page.goto("http://stats.customs.gov.cn/", wait_until="domcontentloaded", timeout=30000)
                                page.wait_for_timeout(3000)
                                continue

                            if isinstance(result, dict) and "_error" in result:
                                self.logger.warning(
                                    f"Customs API returned {result['_error']} for {country_name} {ym}"
                                )
                                continue

                            country_records = self._parse_api_response(
                                result, country_name, ym
                            )
                            records.extend(country_records)

                        except Exception as e:
                            self.logger.warning(
                                f"Customs API error for {country_name} {ym}: {e}"
                            )
                            continue

                        self._rate_limit(
                            self.customs_cfg.get("request_interval_seconds", 5.0)
                        )

                browser.close()

        except Exception as e:
            self.logger.error(f"Playwright API fetch failed: {e}")
            return []

        return records

    def _parse_api_response(
        self, data: dict, country: str, period: str
    ) -> List[TradeRecord]:
        """解析 stats.customs.gov.cn API 响应."""
        records = []

        # API 响应格式 (推测，需实际验证):
        # {"rows": [{"tradeName": "...", "value": ..., "count": ..., ...}]}
        rows = data.get("rows", [])
        if isinstance(data, list):
            rows = data

        for row in rows:
            if not isinstance(row, dict):
                continue
            commodity = row.get("tradeName", "") or row.get("commodityName", "") or row.get("goodsName", "")
            value = self._safe_float(row.get("value", 0) or row.get("exportValue", 0))
            quantity = self._safe_float(row.get("count", 0) or row.get("quantity", 0))
            unit = row.get("unit", "") or row.get("quantityUnit", "")
            yoy = self._safe_float_optional(row.get("yoyPercent", row.get("yoy")) or row.get("yoy"))

            records.append(TradeRecord(
                source="customs",
                country=country,
                direction="export",
                period=period,
                commodity=commodity,
                hs_code=str(row.get("tradeCode", "") or row.get("hsCode", "")),
                value_usd=value,
                quantity=quantity,
                unit=unit,
                yoy_pct=yoy,
            ))

        return records

    def _try_html_scrape(self) -> List[TradeRecord]:
        """尝试从 customs.gov.cn HTML 页面抓取月度统计报表.

        访问月度报告索引页，解析文章链接，下载 Excel 文件.
        """
        records = []

        # 2024 和 2025 年月报索引页
        yearly_pages = [
            "http://www.customs.gov.cn/customs/302249/zfxxgk/2799825/302274/302277/5668662/index.html",  # 2024
            "http://www.customs.gov.cn/customs/302249/zfxxgk/2799825/302274/302277/6348926/index.html",  # 2025
        ]

        for page_url in yearly_pages:
            try:
                self._rate_limit(5.0)
                resp = self.session.get(page_url, timeout=30)

                if resp.status_code == 412:
                    self.logger.warning(f"Customs HTML page WAF blocked: {page_url[:80]}")
                    continue
                if resp.status_code != 200:
                    continue

                # 从页面提取 Excel 下载链接
                from bs4 import BeautifulSoup

                soup = BeautifulSoup(resp.text, "lxml")
                for link in soup.find_all("a", href=True):
                    href = link["href"]
                    if href.endswith((".xls", ".xlsx")):
                        if not href.startswith("http"):
                            href = "http://www.customs.gov.cn" + href
                        excel_records = self._parse_excel(href)
                        if excel_records:
                            records.extend(excel_records)

            except Exception as e:
                self.logger.warning(f"Customs HTML scrape error: {e}")
                continue

        return records

    def _parse_excel(self, url: str) -> List[TradeRecord]:
        """下载并解析海关 Excel 文件."""
        try:
            import io
            import tempfile
            import pandas as pd

            self._rate_limit(5.0)
            resp = self.session.get(url, timeout=60)

            if resp.status_code != 200:
                return []

            content = resp.content
            if len(content) < 1000:
                return []

            # 尝试用 pandas 读取
            xls = pd.ExcelFile(io.BytesIO(content))
            records = []

            for sheet_name in xls.sheet_names:
                try:
                    df = pd.read_excel(xls, sheet_name=sheet_name)
                    # 跳过前几行标题行
                    # 海关 Excel 格式通常是: 商品名称 | 数量 | 金额 | 同比
                    if df.shape[0] < 5:
                        continue

                    # 尝试找到表头行
                    for i, row in df.iterrows():
                        cells = [str(c).strip() for c in row if str(c).strip()]
                        if any("商品" in c for c in cells):
                            # 数据从下一行开始
                            data_start = i + 1
                            break
                    else:
                        continue

                    for j in range(data_start, len(df)):
                        row_data = df.iloc[j]
                        try:
                            commodity = str(row_data.iloc[0]).strip()
                            if not commodity or commodity == "nan":
                                continue

                            # 尝试提取数量、金额列
                            records.append(TradeRecord(
                                source="customs",
                                country="",  # Excel 可能不区分国别
                                direction="export",
                                period="",
                                commodity=commodity,
                                value_usd=self._safe_float(row_data.iloc[-3]) if len(row_data) > 2 else 0,
                                quantity=self._safe_float(row_data.iloc[-4]) if len(row_data) > 3 else 0,
                            ))
                        except Exception:
                            continue

                except Exception as e:
                    self.logger.debug(f"Failed to parse sheet {sheet_name}: {e}")
                    continue

            return records

        except ImportError:
            self.logger.warning("pandas or openpyxl not installed for Excel parsing")
            return []
        except Exception as e:
            self.logger.warning(f"Excel parse error: {e}")
            return []

    def _mofcom_fallback(self) -> List[TradeRecord]:
        """回退到 MOFCOM 数据（同样来源于海关总署）."""
        from crawlers.mofcom import MofcomCrawler

        self.logger.info("Delegating to MOFCOM crawler (customs-sourced data)")
        mofcom = MofcomCrawler()
        mofcom.months_back = self.months_back
        return mofcom.fetch()

    @staticmethod
    def _safe_float(val) -> float:
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _safe_float_optional(val) -> Optional[float]:
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None
