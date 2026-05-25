"""UN Comtrade API 爬虫 - 获取 HS 2位编码的月度双边贸易数据.

数据来源: https://comtradeapi.un.org/
中国海关数据经 Comtrade 标准化，延迟约1-2个月.

注意: HS 2位分类数据通常滞后 12-18 个月。截至 2026年5月，2024全年数据可用，
但 2025+ 的 HS 分类数据尚未提交。
"""

import time
from typing import List, Optional

from crawlers.base import BaseCrawler
from crawlers.trade_record import TradeRecord
from utils.date_utils import generate_month_range


# HS 2位章 → 22大类(Section) 映射
HS_CHAPTER_TO_SECTION = {
    1: "活动物;动物产品", 2: "活动物;动物产品", 3: "活动物;动物产品",
    4: "活动物;动物产品", 5: "活动物;动物产品",
    6: "植物产品", 7: "植物产品", 8: "植物产品", 9: "植物产品",
    10: "植物产品", 11: "植物产品", 12: "植物产品", 13: "植物产品", 14: "植物产品",
    15: "动/植物油脂",
    16: "食品;饮料;烟草", 17: "食品;饮料;烟草", 18: "食品;饮料;烟草",
    19: "食品;饮料;烟草", 20: "食品;饮料;烟草", 21: "食品;饮料;烟草",
    22: "食品;饮料;烟草", 23: "食品;饮料;烟草", 24: "食品;饮料;烟草",
    25: "矿产品", 26: "矿产品", 27: "矿产品",
    28: "化学工业产品", 29: "化学工业产品", 30: "化学工业产品",
    31: "化学工业产品", 32: "化学工业产品", 33: "化学工业产品",
    34: "化学工业产品", 35: "化学工业产品", 36: "化学工业产品",
    37: "化学工业产品", 38: "化学工业产品",
    39: "塑料;橡胶", 40: "塑料;橡胶",
    41: "皮革;箱包", 42: "皮革;箱包", 43: "皮革;箱包",
    44: "木及木制品", 45: "木及木制品", 46: "木及木制品",
    47: "纸浆;纸制品", 48: "纸浆;纸制品", 49: "纸浆;纸制品",
    50: "纺织原料及制品", 51: "纺织原料及制品", 52: "纺织原料及制品",
    53: "纺织原料及制品", 54: "纺织原料及制品", 55: "纺织原料及制品",
    56: "纺织原料及制品", 57: "纺织原料及制品", 58: "纺织原料及制品",
    59: "纺织原料及制品", 60: "纺织原料及制品", 61: "纺织原料及制品",
    62: "纺织原料及制品", 63: "纺织原料及制品",
    64: "鞋帽;羽毛制品", 65: "鞋帽;羽毛制品", 66: "鞋帽;羽毛制品", 67: "鞋帽;羽毛制品",
    68: "石料;陶瓷;玻璃", 69: "石料;陶瓷;玻璃", 70: "石料;陶瓷;玻璃",
    71: "珍珠;宝石;贵金属",
    72: "贱金属及其制品", 73: "贱金属及其制品", 74: "贱金属及其制品",
    75: "贱金属及其制品", 76: "贱金属及其制品", 78: "贱金属及其制品",
    79: "贱金属及其制品", 80: "贱金属及其制品", 81: "贱金属及其制品",
    82: "贱金属及其制品", 83: "贱金属及其制品",
    84: "机电设备", 85: "机电设备",
    86: "运输设备", 87: "运输设备", 88: "运输设备", 89: "运输设备",
    90: "光学;医疗仪器", 91: "光学;医疗仪器", 92: "光学;医疗仪器",
    93: "武器;弹药",
    94: "杂项制品", 95: "杂项制品", 96: "杂项制品",
    97: "艺术品;收藏品",
    98: "特殊交易品", 99: "特殊交易品",
}

HS_CHAPTER_NAMES = {
    1: "活动物", 2: "肉及食用杂碎", 3: "鱼及其他水生动物",
    4: "乳品;蛋品;蜂蜜", 5: "其他动物产品",
    6: "活植物;花卉", 7: "食用蔬菜", 8: "食用水果;坚果",
    9: "咖啡;茶;香料", 10: "谷物", 11: "制粉工业产品",
    12: "油籽;工业植物", 13: "虫胶;树胶;树脂", 14: "植物编织材料",
    15: "动植物油脂",
    16: "肉/鱼制品", 17: "糖及糖食", 18: "可可及可可制品",
    19: "谷物/淀粉制品", 20: "蔬菜/水果制品", 21: "杂项食品",
    22: "饮料;酒;醋", 23: "食品工业残渣;饲料", 24: "烟草",
    25: "盐;硫磺;石料", 26: "矿砂;矿渣", 27: "矿物燃料;石油",
    28: "无机化学品", 29: "有机化学品", 30: "药品",
    31: "肥料", 32: "鞣料;涂料;油墨", 33: "精油;化妆品",
    34: "肥皂;洗涤剂", 35: "蛋白类物质", 36: "炸药;烟火",
    37: "照相用品", 38: "杂项化学产品",
    39: "塑料及其制品", 40: "橡胶及其制品",
    41: "生皮及皮革", 42: "皮革制品;箱包", 43: "毛皮制品",
    44: "木及木制品", 45: "软木及软木制品", 46: "秸秆编织品",
    47: "纸浆", 48: "纸及纸板", 49: "印刷品",
    50: "蚕丝", 51: "羊毛;动物毛", 52: "棉花",
    53: "其他植物纤维", 54: "化纤长丝", 55: "化纤短纤",
    56: "絮胎;毡呢", 57: "地毯", 58: "特种机织物",
    59: "浸渍/涂层织物", 60: "针织物", 61: "针织服装",
    62: "非针织服装", 63: "其他纺织制品",
    64: "鞋靴", 65: "帽类", 66: "伞具;手杖", 67: "羽毛制品",
    68: "石料;石膏制品", 69: "陶瓷产品", 70: "玻璃制品",
    71: "珍珠;宝石;贵金属",
    72: "钢铁", 73: "钢铁制品", 74: "铜及其制品",
    75: "镍及其制品", 76: "铝及其制品", 78: "铅及其制品",
    79: "锌及其制品", 80: "锡及其制品", 81: "其他贱金属",
    82: "工具;餐具", 83: "杂项金属制品",
    84: "核反应堆;机械器具", 85: "电机;电气设备",
    86: "铁路车辆", 87: "车辆及零件", 88: "航空器;航天器",
    89: "船舶及浮体",
    90: "光学/医疗仪器", 91: "钟表", 92: "乐器",
    93: "武器;弹药",
    94: "家具;寝具;灯具", 95: "玩具;运动用品", 96: "杂项制品",
    97: "艺术品;收藏品;古董",
    98: "特殊交易品", 99: "未分类商品",
}

COUNTRY_M49 = {
    "VN": 704,
    "TH": 764,
    "ID": 360,
}

# 所有 HS 2位编码 (01-97)，逗号分隔供给 API 查询
ALL_HS_CODES = ",".join(f"{i:02d}" for i in range(1, 98))
# 每批查询月数 (控制每批返回记录数不超过500)
MONTHS_PER_BATCH = 5


class ComtradeCrawler(BaseCrawler):
    """通过 UN Comtrade API 获取 HS 2位编码级别的双边贸易数据."""

    API_URL = "https://comtradeapi.un.org/public/v1/preview/C/M/HS"

    def __init__(self, config_path: str = None):
        super().__init__(config_path)
        self.months_back = self.config.get("crawl", {}).get("months_back", 24)
        self.ct_cfg = self.config.get("comtrade", {})
        self.subscription_key = self.ct_cfg.get("subscription_key", "")
        self.request_delay = self.ct_cfg.get("request_delay_seconds", 6.0)

    @property
    def source_name(self) -> str:
        return "comtrade"

    def fetch(self) -> List[TradeRecord]:
        countries = self.config.get("countries", [])
        months = generate_month_range(self.months_back)
        records = []

        # 将月份分批，每批5个月
        month_batches = [months[i:i + MONTHS_PER_BATCH] for i in range(0, len(months), MONTHS_PER_BATCH)]

        total_calls = len(countries) * len(month_batches)
        call_count = 0

        for country in countries:
            country_code = country.get("code", "")
            country_name = country["name"]
            partner_code = COUNTRY_M49.get(country_code)

            if not partner_code:
                self.logger.warning(f"Unknown M49 code for {country_name} ({country_code})")
                continue

            for month_batch in month_batches:
                call_count += 1
                period_str = ",".join(month_batch)

                if call_count % 5 == 0:
                    self.logger.info(f"  Comtrade: {call_count}/{total_calls} calls")

                data = self._call_api(partner_code, period_str)
                if data:
                    parsed = self._parse_response(data, country_name)
                    records.extend(parsed)

                time.sleep(self.request_delay)

        self.logger.info(f"Comtrade: {len(records)} records from {call_count} API calls")
        return records

    def _call_api(self, partner_code: int, period: str) -> Optional[dict]:
        """调用 UN Comtrade preview API, 带重试和退避."""
        params = {
            "reporterCode": 156,
            "partnerCode": partner_code,
            "flowCode": "X",
            "period": period,
            "cmdCode": ALL_HS_CODES,
            "maxRecords": 500,
        }

        url = self.API_URL

        for attempt in range(3):
            try:
                resp = self.session.get(url, params=params, timeout=60)

                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 429:
                    wait = 30 * (attempt + 1)
                    self.logger.warning(f"Comtrade rate limited, waiting {wait}s...")
                    time.sleep(wait)
                    continue
                elif resp.status_code == 404:
                    return None
                else:
                    self.logger.warning(f"Comtrade HTTP {resp.status_code}")
                    if attempt < 2:
                        time.sleep(5)
                    continue
            except Exception as e:
                self.logger.warning(f"Comtrade request error: {e}")
                if attempt < 2:
                    time.sleep(5)
                continue

        return None

    def _parse_response(self, data: dict, country: str) -> List[TradeRecord]:
        records = []

        if not data:
            return records

        rows = data.get("data", [])
        if isinstance(data, list):
            rows = data

        for row in rows:
            if not isinstance(row, dict):
                continue

            hs_code = str(row.get("cmdCode", ""))
            if not hs_code or hs_code == "TOTAL":
                continue

            value = self._safe_float(row.get("primaryValue") or row.get("fobvalue", 0))
            if value == 0:
                continue

            try:
                chapter = int(hs_code)
            except (ValueError, TypeError):
                chapter = 0

            section = HS_CHAPTER_TO_SECTION.get(chapter, "其他")
            chapter_name = HS_CHAPTER_NAMES.get(chapter, f"HS{hs_code}")

            # period: 提取 YYYYMM
            period_id = str(row.get("refPeriodId", ""))
            if len(period_id) >= 6:
                period = period_id[:4] + period_id[4:6]
            else:
                period = ""

            qty = self._safe_float(row.get("qty", 0))

            records.append(TradeRecord(
                source="comtrade",
                country=country,
                direction="export",
                period=period,
                commodity=chapter_name,
                hs_code=hs_code,
                hs_section=section,
                value_usd=value,
                quantity=qty,
                unit=row.get("qtyUnitAbbr", ""),
            ))

        return records

    @staticmethod
    def _safe_float(val) -> float:
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0
