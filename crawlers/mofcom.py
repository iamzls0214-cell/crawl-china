"""商务部数据中心爬虫 - data.mofcom.gov.cn."""

import logging
from typing import List, Optional

import pandas as pd

from crawlers.base import BaseCrawler
from crawlers.trade_record import TradeRecord
from utils.date_utils import is_january, generate_month_range


class MofcomCrawler(BaseCrawler):
    """爬取 data.mofcom.gov.cn 分国别贸易数据.

    路径A (优先): JSON API - /datamofcom/front/totalbycountry/detailquery
    路径B (fallback): HTML 表格 - /hwmy/imexCountry_detail.shtml
    """

    BASE_URL = "https://data.mofcom.gov.cn"
    DETAIL_QUERY = "/datamofcom/front/totalbycountry/detailquery"
    MONTHLY_QUERY = "/datamofcom/front/totalmonth/query"

    def __init__(self, config_path: str = None):
        super().__init__(config_path)
        self.session.headers.update({
            "Referer": "https://data.mofcom.gov.cn/hwmy/imexCountry.shtml",
            "Origin": "https://data.mofcom.gov.cn",
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        })
        self.months_back = self.config.get("crawl", {}).get("months_back", 24)

    @property
    def source_name(self) -> str:
        return "mofcom"

    def fetch(self) -> List[TradeRecord]:
        countries = self.config.get("countries", [])
        records = []

        for country in countries:
            name = country["name"]
            self.logger.info(f"Fetching MOFCOM data for {name}...")
            try:
                country_records = self._fetch_country_detail(name)
                if country_records:
                    records.extend(country_records)
                    self.logger.info(f"  {name}: {len(country_records)} records via JSON API")
                else:
                    self.logger.warning(f"  {name}: JSON API empty, trying HTML fallback")
                    country_records = self._fetch_html_fallback(name)
                    if country_records:
                        records.extend(country_records)
                        self.logger.info(f"  {name}: {len(country_records)} records via HTML")
                    else:
                        self.logger.error(f"  {name}: all methods failed")
            except Exception as e:
                self.logger.error(f"  {name}: error - {e}", exc_info=True)
                try:
                    records.extend(self._fetch_html_fallback(name))
                except Exception as e2:
                    self.logger.error(f"  {name}: HTML fallback also failed - {e2}")

        return records

    def _fetch_country_detail(self, country_name: str) -> List[TradeRecord]:
        """路径A: 调用 detailquery JSON API."""
        url = f"{self.BASE_URL}{self.DETAIL_QUERY}"
        data = {"key": country_name}

        resp = self._request_with_retry("POST", url, data=data)
        if resp is None or resp.status_code >= 400:
            return []

        try:
            result = resp.json()
        except ValueError:
            self.logger.warning(f"  {country_name}: detailquery returned non-JSON")
            return []

        # 响应格式: [数据数组, 图表配置]
        if not isinstance(result, list) or len(result) == 0:
            return []
        rows = result[0]
        if not isinstance(rows, list):
            return []

        return self._parse_detail_rows(rows, country_name)

    def _parse_detail_rows(self, rows: List[dict], country: str) -> List[TradeRecord]:
        """解析 detailquery 返回的累计值数据，转为月度值.

        累计值转换: 月度 = 当月累计 - 上月累计.
        需要包含 cutoff 前一个月的累计值作为基准.
        """
        from utils.date_utils import previous_month

        sorted_rows = sorted(rows, key=lambda r: r.get("trade_date", "000000"))
        cutoff = generate_month_range(self.months_back)[0] if self.months_back > 0 else "000000"

        # 建立累计值索引 (period -> cumulative values)
        cum_index = {}
        for row in sorted_rows:
            period = row.get("trade_date", "")
            cum_index[period] = {
                "export": float(row.get("export_lj_value", 0) or 0),
                "import": float(row.get("import_lj_value", 0) or 0),
                "total": float(row.get("total_lj_value", 0) or 0),
                "export_per": self._parse_pct(row.get("export_lj_per")),
                "import_per": self._parse_pct(row.get("import_lj_per")),
                "total_per": self._parse_pct(row.get("total_lj_per")),
            }

        records = []
        for row in sorted_rows:
            period = row.get("trade_date", "")
            if period < cutoff:
                continue

            # 获取上月累计作为基准
            prev_period = previous_month(period)
            if is_january(period) or prev_period not in cum_index:
                prev_exp = 0.0
                prev_imp = 0.0
                prev_total = 0.0
            else:
                prev_exp = cum_index[prev_period]["export"]
                prev_imp = cum_index[prev_period]["import"]
                prev_total = cum_index[prev_period]["total"]

            cur = cum_index[period]

            records.append(TradeRecord(
                source="mofcom",
                country=country,
                direction="export",
                period=period,
                value_usd=round(cur["export"] - prev_exp, 2),
                yoy_pct=cur["export_per"],
            ))
            records.append(TradeRecord(
                source="mofcom",
                country=country,
                direction="import",
                period=period,
                value_usd=round(cur["import"] - prev_imp, 2),
                yoy_pct=cur["import_per"],
            ))
            records.append(TradeRecord(
                source="mofcom",
                country=country,
                direction="total",
                period=period,
                value_usd=round(cur["total"] - prev_total, 2),
                yoy_pct=cur["total_per"],
            ))

        return records

    def _fetch_html_fallback(self, country_name: str) -> List[TradeRecord]:
        """路径B: 从 HTML 页面解析表格."""
        from urllib.parse import quote

        url = f"{self.BASE_URL}/hwmy/imexCountry_detail.shtml?key={quote(country_name)}"
        resp = self._request_with_retry("GET", url)
        if resp is None or resp.status_code >= 400:
            return []

        try:
            tables = pd.read_html(resp.text)
        except Exception as e:
            self.logger.warning(f"  {country_name}: HTML parse failed - {e}")
            return []

        if not tables:
            return []

        records = []
        for table in tables:
            # 尝试标准化表格格式
            try:
                df = table
                if df.shape[1] < 3:
                    continue
                # 常见格式: 月份 | 出口 | 进口 | 进出口
                for _, row_data in df.iterrows():
                    period = str(row_data.iloc[0]).strip()
                    if len(period) < 5:  # not a valid period string
                        continue
                    # 规范化 period 为 YYYYMM
                    period = period.replace("-", "").replace("/", "").replace(".", "")
                    if len(period) == 6 and period.isdigit():
                        for col_idx, direction in [(1, "export"), (2, "import"), (3, "total")]:
                            if col_idx < len(row_data):
                                try:
                                    val = float(str(row_data.iloc[col_idx]).replace(",", ""))
                                    records.append(TradeRecord(
                                        source="mofcom",
                                        country=country_name,
                                        direction=direction,
                                        period=period,
                                        value_usd=val,
                                    ))
                                except (ValueError, TypeError):
                                    continue
            except Exception:
                continue

        return records

    @staticmethod
    def _parse_pct(val) -> Optional[float]:
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None
