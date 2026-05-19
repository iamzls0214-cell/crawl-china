"""标准化数据模型."""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


@dataclass
class TradeRecord:
    source: str              # "customs" | "mofcom" | "google_trends"
    country: str             # "越南" | "泰国" | "印度尼西亚"
    direction: str           # "export" | "import" | "total" | "trend"
    period: str              # "202603" (YYYYMM)
    commodity: str = ""      # 商品名称，总值为"合计"
    hs_code: str = ""        # HS编码（customs特有）
    value_usd: float = 0.0   # 美元值（万美元）
    quantity: float = 0.0    # 数量
    unit: str = ""           # 单位
    yoy_pct: Optional[float] = None  # 同比%
    data_date: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)

    def to_csv_row(self) -> dict:
        return {
            "source": self.source,
            "country": self.country,
            "direction": self.direction,
            "period": self.period,
            "commodity": self.commodity,
            "hs_code": self.hs_code,
            "value_usd": self.value_usd,
            "quantity": self.quantity,
            "unit": self.unit,
            "yoy_pct": self.yoy_pct if self.yoy_pct is not None else "",
            "data_date": self.data_date,
        }
