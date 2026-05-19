"""日期工具 - 生成需要爬取的月份范围."""

from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from typing import List


def generate_month_range(months_back: int, end_date: datetime = None) -> List[str]:
    """生成 YYYYMM 格式的月份列表，从 end_date 往前 months_back 个月."""
    if end_date is None:
        end_date = datetime.now()
    months = []
    for i in range(months_back):
        d = end_date - relativedelta(months=i)
        months.append(d.strftime("%Y%m"))
    return sorted(months)


def current_yearmonth() -> str:
    return datetime.now().strftime("%Y%m")


def parse_yearmonth(ym: str) -> datetime:
    return datetime.strptime(ym, "%Y%m")


def is_january(ym: str) -> bool:
    return ym.endswith("01")


def previous_month(ym: str) -> str:
    dt = datetime.strptime(ym, "%Y%m")
    prev = dt - relativedelta(months=1)
    return prev.strftime("%Y%m")
