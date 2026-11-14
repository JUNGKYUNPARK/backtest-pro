"""
실제 한국/미국 거래비용 모델
- 한국 주식: 매매 연도별 실제 증권거래세(농특세 포함) 자동 적용
- ETF/ETN: 거래세 면제
- 슬리피지: 체결가 불리하게 조정
"""

# 연도별 매도 시 거래세 (증권거래세 + 농어촌특별세), 단위: 비율
# 출처: 기획재정부 세법 개정 연혁 (2019.6 인하 ~ 2026 환원)
_KOSPI_TAX = {2018: 0.0030, 2019: 0.0025, 2020: 0.0025, 2021: 0.0023, 2022: 0.0023,
              2023: 0.0020, 2024: 0.0018, 2025: 0.0015, 2026: 0.0020}
_KOSDAQ_TAX = {2018: 0.0030, 2019: 0.0025, 2020: 0.0025, 2021: 0.0023, 2022: 0.0023,
               2023: 0.0020, 2024: 0.0018, 2025: 0.0015, 2026: 0.0020}


def kr_stock_tax(year, market='KOSPI'):
    table = _KOSDAQ_TAX if market == 'KOSDAQ' else _KOSPI_TAX
    if year <= min(table):
        return table[min(table)]
    if year >= max(table):
        return table[max(table)]
    return table.get(year, table[max(table)])


ASSET_LABELS = {
    'KOSPI': '🇰🇷 코스피 주식', 'KOSDAQ': '🇰🇷 코스닥 주식', 'KONEX': '🇰🇷 코넥스 주식',
    'KRX': '🇰🇷 한국 주식', 'ETF': '📦 한국 ETF', 'ETN': '📋 한국 ETN',
    'US_STOCK': '🇺🇸 미국 주식', 'US_ETF': '🇺🇸 미국 ETF', 'INDEX': '🌎 지수',
}

# 기본 수수료율 (온라인/MTS 일반 기준)
DEFAULT_COMMISSION = {
    'KOSPI': 0.00015, 'KOSDAQ': 0.00015, 'KONEX': 0.00015, 'KRX': 0.00015,
    'ETF': 0.00015, 'ETN': 0.00015,
    'US_STOCK': 0.0007, 'US_ETF': 0.0007,   # 국내 증권사 해외주식 우대 수수료 수준
    'INDEX': 0.0,                          # 지수는 직접 매매 불가 — 참고용
}


class FeeModel:
    def __init__(self, slippage=0.0005, commission=None, tax=None, auto_tax=True):
        """
        slippage: 체결 미끄러짐 비율 (0.0005 = 0.05%)
        commission: 직접 지정 시 모든 자산 동일 수수료(비율). None이면 자산별 기본값
        tax: 직접 지정 시 매도세(비율). None이면 연도별 실제 세율 자동
        """
        self.slippage = float(slippage or 0)
        self.commission = commission
        self.tax = tax
        self.auto_tax = auto_tax

    def comm_rate(self, asset):
        if self.commission is not None:
            return float(self.commission)
        return DEFAULT_COMMISSION.get(asset, 0.00015)

    def tax_rate(self, asset, year):
        if self.tax is not None:
            return float(self.tax) if asset in ('KOSPI', 'KOSDAQ', 'KONEX', 'KRX') else 0.0
        if asset in ('KOSPI', 'KRX', 'KONEX'):
            return kr_stock_tax(year, 'KOSPI')
        if asset == 'KOSDAQ':
            return kr_stock_tax(year, 'KOSDAQ')
        return 0.0

    def buy_fill(self, price, asset):
        """매수 체결가, 수수료율"""
        return price * (1 + self.slippage), self.comm_rate(asset)

    def sell(self, price, shares, asset, year):
        """매도 체결가, 총 비용(수수료+세금)"""
        fill = price * (1 - self.slippage)
        gross = fill * shares
        cost = gross * (self.comm_rate(asset) + self.tax_rate(asset, year))
        return fill, cost

    def describe(self, asset, year=2026):
        c = self.comm_rate(asset) * 100
        t = self.tax_rate(asset, year) * 100
        parts = [f"수수료 {c:.3f}%"]
        if t > 0:
            parts.append(f"매도세 {t:.2f}%" + (" (연도별 자동)" if self.tax is None else ""))
        else:
            parts.append("매도세 없음")
        parts.append(f"슬리피지 {self.slippage * 100:.2f}%")
        return f"{ASSET_LABELS.get(asset, asset)} · " + " · ".join(parts)


def detect_asset_type(code, market=None):
    """종목코드/시장 → 자산유형"""
    if market:
        m = str(market).upper()
        if m in ('KOSPI', 'KOSDAQ', 'KONEX', 'ETF', 'ETN', 'US_STOCK', 'US_ETF', 'INDEX'):
            return m
        if 'KOSDAQ' in m:
            return 'KOSDAQ'
        if 'KOSPI' in m:
            return 'KOSPI'
    if not code:
        return 'KRX'
    if code.startswith('^'):
        return 'INDEX'
    if not code[:1].isdigit():
        return 'US_STOCK'
    return 'KRX'
