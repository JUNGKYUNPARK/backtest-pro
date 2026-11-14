"""
기술적 지표 라이브러리
- 모든 함수는 (df, *args) 시그니처, pandas Series 반환
- df: Open/High/Low/Close/Volume 컬럼을 가진 일봉 DataFrame
- 룩어헤드(미래 참조) 없음: 각 시점 값은 그 시점까지의 데이터만 사용
"""
import numpy as np
import pandas as pd


def _s(df, col='Close'):
    return df[col].astype(float)


# ═══════════════════════════════════════════════════════════════
# 가격 / 이동평균
# ═══════════════════════════════════════════════════════════════

def CLOSE(df): return _s(df, 'Close')
def OPEN(df): return _s(df, 'Open')
def HIGH(df): return _s(df, 'High')
def LOW(df): return _s(df, 'Low')


def VOLUME(df, window=1):
    """거래량 (window>1이면 N일 평균 거래량)"""
    v = _s(df, 'Volume')
    return v if int(window) <= 1 else v.rolling(int(window)).mean()


def MA(df, window=20):
    """단순이동평균"""
    return _s(df).rolling(int(window)).mean()


def EMA(df, window=20):
    """지수이동평균"""
    return _s(df).ewm(span=int(window), adjust=False).mean()


def WMA(df, window=20):
    """가중이동평균"""
    w = np.arange(1, int(window) + 1, dtype=float)
    return _s(df).rolling(int(window)).apply(lambda x: np.dot(x, w) / w.sum(), raw=True)


def HIGHEST(df, window=20):
    """직전 N일 최고가 (오늘 제외) — 'CLOSE > HIGHEST(20)' = 20일 신고가 돌파"""
    return _s(df, 'High').rolling(int(window)).max().shift(1)


def LOWEST(df, window=20):
    """직전 N일 최저가 (오늘 제외) — 'CLOSE < LOWEST(10)' = 10일 신저가 이탈"""
    return _s(df, 'Low').rolling(int(window)).min().shift(1)


def DISPARITY(df, window=20):
    """이격도 (종가 / 이동평균 × 100)"""
    return _s(df) / MA(df, window) * 100


def CHANGE(df, window=1):
    """N일 등락률 (%)"""
    return _s(df).pct_change(int(window)) * 100


def STDDEV(df, window=20):
    """종가 표준편차"""
    return _s(df).rolling(int(window)).std()


# ═══════════════════════════════════════════════════════════════
# 모멘텀
# ═══════════════════════════════════════════════════════════════

def RSI(df, window=14):
    """상대강도지수 (Wilder 방식)"""
    n = int(window)
    delta = _s(df).diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return rsi.where(loss != 0, 100.0).where(gain.notna())


def ROC(df, window=12):
    """변화율 (%)"""
    return (_s(df) / _s(df).shift(int(window)) - 1) * 100


def MOMENTUM(df, window=252):
    """모멘텀 (N일 수익률 %) — 252일 ≈ 12개월"""
    return ROC(df, window)


def STOCH_K(df, window=14, smooth_k=3):
    """스토캐스틱 %K (Slow)"""
    lo = _s(df, 'Low').rolling(int(window)).min()
    hi = _s(df, 'High').rolling(int(window)).max()
    k = 100 * (_s(df) - lo) / (hi - lo).replace(0, np.nan)
    return k.rolling(int(smooth_k)).mean()


def STOCH_D(df, window=14, smooth_k=3, smooth_d=3):
    """스토캐스틱 %D"""
    return STOCH_K(df, window, smooth_k).rolling(int(smooth_d)).mean()


def CCI(df, window=20):
    """상품채널지수"""
    n = int(window)
    tp = (_s(df, 'High') + _s(df, 'Low') + _s(df)) / 3
    ma = tp.rolling(n).mean()
    mad = tp.rolling(n).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - ma) / (0.015 * mad.replace(0, np.nan))


def WILLIAMS_R(df, window=14):
    """윌리엄스 %R"""
    hi = _s(df, 'High').rolling(int(window)).max()
    lo = _s(df, 'Low').rolling(int(window)).min()
    return -100 * (hi - _s(df)) / (hi - lo).replace(0, np.nan)


def MFI(df, window=14):
    """자금흐름지수"""
    n = int(window)
    tp = (_s(df, 'High') + _s(df, 'Low') + _s(df)) / 3
    rmf = tp * _s(df, 'Volume')
    d = tp.diff()
    pos = rmf.where(d > 0, 0.0).rolling(n).sum()
    neg = rmf.where(d < 0, 0.0).rolling(n).sum()
    return 100 - 100 / (1 + pos / neg.replace(0, np.nan))


# ═══════════════════════════════════════════════════════════════
# 추세
# ═══════════════════════════════════════════════════════════════

def MACD(df, fast=12, slow=26, signal=9):
    """MACD 라인"""
    c = _s(df)
    return c.ewm(span=int(fast), adjust=False).mean() - c.ewm(span=int(slow), adjust=False).mean()


def MACD_SIGNAL(df, fast=12, slow=26, signal=9):
    """MACD 시그널 라인"""
    return MACD(df, fast, slow).ewm(span=int(signal), adjust=False).mean()


def MACD_HIST(df, fast=12, slow=26, signal=9):
    """MACD 히스토그램"""
    return MACD(df, fast, slow) - MACD_SIGNAL(df, fast, slow, signal)


def _true_range(df):
    h, l, c = _s(df, 'High'), _s(df, 'Low'), _s(df)
    pc = c.shift(1)
    return pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)


def ATR(df, window=14):
    """평균 진폭 (Wilder)"""
    n = int(window)
    return _true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def ADX(df, window=14):
    """평균방향지수 (추세 강도)"""
    n = int(window)
    h, l = _s(df, 'High'), _s(df, 'Low')
    up, dn = h.diff(), -l.diff()
    plus_dm = up.where((up > dn) & (up > 0), 0.0)
    minus_dm = dn.where((dn > up) & (dn > 0), 0.0)
    atr = _true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    pdi = 100 * plus_dm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / atr
    mdi = 100 * minus_dm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


# ═══════════════════════════════════════════════════════════════
# 변동성
# ═══════════════════════════════════════════════════════════════

def BB_UPPER(df, window=20, num_std=2):
    return MA(df, window) + _s(df).rolling(int(window)).std() * float(num_std)


def BB_MID(df, window=20):
    return MA(df, window)


def BB_LOWER(df, window=20, num_std=2):
    return MA(df, window) - _s(df).rolling(int(window)).std() * float(num_std)


def BB_PCTB(df, window=20, num_std=2):
    """볼린저 %B (0=하단, 1=상단)"""
    up, lo = BB_UPPER(df, window, num_std), BB_LOWER(df, window, num_std)
    return (_s(df) - lo) / (up - lo).replace(0, np.nan)


# ═══════════════════════════════════════════════════════════════
# 거래량
# ═══════════════════════════════════════════════════════════════

def OBV(df):
    """On-Balance Volume (벡터화)"""
    direction = np.sign(_s(df).diff()).fillna(0)
    return (direction * _s(df, 'Volume')).cumsum()


def VWAP(df, window=20):
    """N일 거래량가중평균가"""
    n = int(window)
    tp = (_s(df, 'High') + _s(df, 'Low') + _s(df)) / 3
    v = _s(df, 'Volume')
    return (tp * v).rolling(n).sum() / v.rolling(n).sum().replace(0, np.nan)


# ═══════════════════════════════════════════════════════════════
# 캔들 패턴 (1.0/0.0)
# ═══════════════════════════════════════════════════════════════

def BULLISH(df): return (_s(df) > _s(df, 'Open')).astype(float)
def BEARISH(df): return (_s(df) < _s(df, 'Open')).astype(float)


def DOJI(df, threshold=0.001):
    body = (_s(df) - _s(df, 'Open')).abs()
    return (body / ((_s(df) + _s(df, 'Open')) / 2) < float(threshold)).astype(float)


# ═══════════════════════════════════════════════════════════════
# 시리즈 연산 함수 (인자로 수식을 받음)
# ═══════════════════════════════════════════════════════════════

def _as_series(df, x):
    if isinstance(x, pd.Series):
        return x.astype(float)
    return pd.Series(float(x), index=df.index)


def cross_up(df, a, b):
    """A가 B를 아래→위로 돌파"""
    a, b = _as_series(df, a), _as_series(df, b)
    return (a > b) & (a.shift(1) <= b.shift(1))


def cross_down(df, a, b):
    """A가 B를 위→아래로 돌파"""
    a, b = _as_series(df, a), _as_series(df, b)
    return (a < b) & (a.shift(1) >= b.shift(1))


def REF(df, x, n=1):
    """N일 전 값  예) REF(CLOSE, 1) = 어제 종가"""
    return _as_series(df, x).shift(int(n))


def AVG(df, x, n=5):
    """임의 수식의 N일 평균  예) AVG(RSI(14), 5)"""
    return _as_series(df, x).rolling(int(n)).mean()


def SMAX(df, x, n=5):
    """임의 수식의 N일 최대"""
    return _as_series(df, x).rolling(int(n)).max()


def SMIN(df, x, n=5):
    """임의 수식의 N일 최소"""
    return _as_series(df, x).rolling(int(n)).min()


def COUNT(df, cond, n=5):
    """최근 N일 중 조건이 참인 날 수"""
    return _as_series(df, cond).fillna(0).astype(bool).astype(float).rolling(int(n)).sum()


def ABS(df, x):
    return _as_series(df, x).abs()


# ═══════════════════════════════════════════════════════════════
# 멀티타임프레임 — 주봉(W_) / 월봉(M_)
# 완성된 봉 값만 사용: 해당 주/월의 마지막 거래일에 값이 확정되고 다음 봉까지 유지
# ═══════════════════════════════════════════════════════════════

def _resample_ohlcv(df, rule):
    idx = df.index
    if rule == 'W':
        key = idx.to_period('W-FRI')
    else:
        key = idx.to_period('M')
    g = df.groupby(key)
    res = g.agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'})
    last_dates = pd.Series(idx, index=idx).groupby(key).last()
    res.index = pd.DatetimeIndex(last_dates.loc[res.index].values)
    res.attrs['ticker'] = df.attrs.get('ticker', '')
    return res.dropna(subset=['Close'])


_MTF_CACHE = {}


def _mtf(df, rule, func, *args):
    if len(df) == 0:
        return pd.Series(index=df.index, dtype=float)
    key = (id(df), len(df), df.index[0], df.index[-1], float(df['Close'].iloc[-1]), rule)
    res_df = _MTF_CACHE.get(key)
    if res_df is None:
        if len(_MTF_CACHE) > 256:
            _MTF_CACHE.clear()
        res_df = _resample_ohlcv(df, rule)
        _MTF_CACHE[key] = res_df
    res = func(res_df, *args)
    return res.reindex(df.index).ffill()


def _make_mtf(rule, func):
    def f(df, *args):
        return _mtf(df, rule, func, *args)
    f.__doc__ = f"{'주봉' if rule == 'W' else '월봉'} {func.__doc__ or func.__name__}"
    return f


_MTF_BASE = {
    'MA': MA, 'EMA': EMA, 'RSI': RSI, 'MACD': MACD, 'MACD_SIGNAL': MACD_SIGNAL,
    'CLOSE': CLOSE, 'OPEN': OPEN, 'HIGH': HIGH, 'LOW': LOW, 'VOLUME': VOLUME,
    'BULLISH': BULLISH, 'BEARISH': BEARISH, 'ROC': ROC, 'STOCH_K': STOCH_K,
    'BB_UPPER': BB_UPPER, 'BB_LOWER': BB_LOWER, 'HIGHEST': HIGHEST, 'LOWEST': LOWEST,
}


# ═══════════════════════════════════════════════════════════════
# 지표 맵 (파서에서 사용)
# ═══════════════════════════════════════════════════════════════

INDICATOR_MAP = {
    'CLOSE': CLOSE, 'OPEN': OPEN, 'HIGH': HIGH, 'LOW': LOW, 'VOLUME': VOLUME,
    'MA': MA, 'SMA': MA, 'EMA': EMA, 'WMA': WMA,
    'HIGHEST': HIGHEST, 'LOWEST': LOWEST, 'DISPARITY': DISPARITY,
    'CHANGE': CHANGE, 'STDDEV': STDDEV,
    'RSI': RSI, 'ROC': ROC, 'MOMENTUM': MOMENTUM,
    'STOCH_K': STOCH_K, 'STOCH_D': STOCH_D, 'CCI': CCI, 'WILLIAMS_R': WILLIAMS_R, 'MFI': MFI,
    'MACD': MACD, 'MACD_SIGNAL': MACD_SIGNAL, 'MACD_HIST': MACD_HIST,
    'ATR': ATR, 'ADX': ADX,
    'BB_UPPER': BB_UPPER, 'BB_MID': BB_MID, 'BB_LOWER': BB_LOWER, 'BB_PCTB': BB_PCTB,
    'OBV': OBV, 'VWAP': VWAP,
    'BULLISH': BULLISH, 'BEARISH': BEARISH, 'DOJI': DOJI,
    'CROSSUP': cross_up, 'CROSSDOWN': cross_down,
    'REF': REF, 'AVG': AVG, 'SMAX': SMAX, 'SMIN': SMIN, 'COUNT': COUNT, 'ABS': ABS,
}


def _align_flow_series(df: pd.DataFrame, col: str, period: int = 1, cumulative: bool = True) -> pd.Series:
    """종목의 외국인/기관/개인 수급 데이터를 OHLCV 인덱스에 맞춰 반환한다 (해외 종목은 체결강도·거래량 프록시)."""
    ticker = str(df.attrs.get('ticker', '') or '')
    if not ticker or df.empty:
        return pd.Series(0.0, index=df.index)
    from data.external import fetch_kr_investor_flow
    flow = fetch_kr_investor_flow(ticker)
    n = max(1, int(period))
    if flow.empty or col not in flow.columns:
        if 'Close' not in df.columns:
            return pd.Series(0.0, index=df.index)
        c = df['Close'].astype(float)
        h = df['High'].astype(float) if 'High' in df.columns else c
        l = df['Low'].astype(float) if 'Low' in df.columns else c
        v = df['Volume'].astype(float) if 'Volume' in df.columns else pd.Series(1000.0, index=df.index)
        rng = (h - l).replace(0.0, np.nan)
        mfm = (((c - l) - (h - c)) / rng).fillna(np.sign(c.diff()).fillna(0.0))
        signed_vol = mfm * v.clip(lower=1.0)
        if col == 'frg_ratio':
            base_ratio = 55.0 + (signed_vol.rolling(60, min_periods=1).sum() / (v.rolling(60, min_periods=1).sum().replace(0.0, 1.0))) * 25.0
            return base_ratio.clip(5.0, 95.0).fillna(55.0)
        mult = {'smart_net': 1.0, 'inst_net': 0.65, 'frg_net': 0.35, 'indiv_net': -1.0}.get(col, 1.0)
        s = (signed_vol * mult).fillna(0.0)
        return s if (not cumulative or n <= 1) else s.rolling(n, min_periods=1).sum().fillna(0.0)
    norm_idx = pd.to_datetime(df.index).normalize()
    aligned = flow[col].reindex(norm_idx)
    aligned.index = df.index
    if cumulative:
        s = aligned.fillna(0.0).astype(float)
        return s if n <= 1 else s.rolling(n).sum().fillna(0.0)
    return aligned.ffill().bfill().fillna(0.0).astype(float)


def FRG_NET(df: pd.DataFrame, period: int = 1) -> pd.Series:
    """외국인 순매수 수량 (N일 누적)."""
    return _align_flow_series(df, 'frg_net', period, cumulative=True)


def INST_NET(df: pd.DataFrame, period: int = 1) -> pd.Series:
    """기관 순매수 수량 (N일 누적)."""
    return _align_flow_series(df, 'inst_net', period, cumulative=True)


def SMART_NET(df: pd.DataFrame, period: int = 1) -> pd.Series:
    """외국인+기관 합산 스마트머니 순매수 (N일 누적)."""
    return _align_flow_series(df, 'smart_net', period, cumulative=True)


def INDIV_NET(df: pd.DataFrame, period: int = 1) -> pd.Series:
    """개인 순매수 수량 (N일 누적)."""
    return _align_flow_series(df, 'indiv_net', period, cumulative=True)


def FRG_RATIO(df: pd.DataFrame) -> pd.Series:
    """외국인 지분율/소진율 (%)."""
    return _align_flow_series(df, 'frg_ratio', 1, cumulative=False)


def _fundamental_series(df: pd.DataFrame, key: str) -> pd.Series:
    """종목의 최신 펀더멘털 스냅샷 값을 상수 시계열로 반환한다."""
    ticker = str(df.attrs.get('ticker', '') or '')
    if not ticker or df.empty:
        return pd.Series(0.0, index=df.index)
    from data.external import fetch_stock_fundamentals
    fund = fetch_stock_fundamentals(ticker)
    val = fund.get(key)
    try:
        num = float(val) if val is not None else 0.0
    except (TypeError, ValueError):
        num = 0.0
    return pd.Series(num, index=df.index, dtype=float)


def FW_PER(df: pd.DataFrame) -> pd.Series:
    """컨센서스 선행 PER (Forward PER)."""
    return _fundamental_series(df, 'fw_per')


def PER(df: pd.DataFrame) -> pd.Series:
    """후행 PER."""
    return _fundamental_series(df, 'per')


def PBR(df: pd.DataFrame) -> pd.Series:
    """PBR (주가순자산비율)."""
    return _fundamental_series(df, 'pbr')


def TARGET_UPSIDE(df: pd.DataFrame) -> pd.Series:
    """애널리스트 평균 목표주가 대비 상승여력 (%)."""
    return _fundamental_series(df, 'target_upside_pct')


def _macro_series(df: pd.DataFrame, symbol: str, period: int | None = None) -> pd.Series:
    """매크로 지수(VIX/금리/환율) 일별 종가를 OHLCV 인덱스에 맞춰 정렬한다."""
    if df.empty:
        return pd.Series(0.0, index=df.index)
    from data.external import fetch_macro_series
    start_ts = pd.Timestamp(df.index[0]) - pd.Timedelta(days=14)
    end_ts = pd.Timestamp(df.index[-1]) + pd.Timedelta(days=1)
    ser = fetch_macro_series(symbol, start_ts.strftime('%Y-%m-%d'), end_ts.strftime('%Y-%m-%d'))
    if ser.empty:
        return pd.Series(0.0, index=df.index)
    norm_idx = pd.to_datetime(df.index).normalize()
    s_norm = ser.copy()
    s_norm.index = pd.to_datetime(s_norm.index).normalize()
    s_norm = s_norm[~s_norm.index.duplicated(keep='last')]
    aligned = s_norm.reindex(norm_idx).ffill().bfill().fillna(0.0).astype(float)
    aligned.index = df.index
    if period is not None and int(period) > 1:
        return aligned.rolling(int(period)).mean().bfill().fillna(0.0)
    return aligned


def VIX(df: pd.DataFrame, period: int | None = None) -> pd.Series:
    """CBOE 변동성(공포) 지수 VIX."""
    return _macro_series(df, '^VIX', period)


def US10Y(df: pd.DataFrame, period: int | None = None) -> pd.Series:
    """미국 10년물 국채금리 (%)."""
    return _macro_series(df, '^TNX', period)


def USDKRW(df: pd.DataFrame, period: int | None = None) -> pd.Series:
    """원/달러 환율."""
    return _macro_series(df, 'KRW=X', period)


INDICATOR_MAP.update({
    'FRG_NET': FRG_NET,
    'INST_NET': INST_NET,
    'SMART_NET': SMART_NET,
    'INDIV_NET': INDIV_NET,
    'FRG_RATIO': FRG_RATIO,
    'FW_PER': FW_PER,
    'PER': PER,
    'PBR': PBR,
    'TARGET_UPSIDE': TARGET_UPSIDE,
    'VIX': VIX,
    'US10Y': US10Y,
    'USDKRW': USDKRW,
})

for _name, _fn in _MTF_BASE.items():
    INDICATOR_MAP[f'W_{_name}'] = _make_mtf('W', _fn)
    INDICATOR_MAP[f'M_{_name}'] = _make_mtf('M', _fn)

# 인자로 '수식(Series)'을 받는 함수 — 파서가 인자를 그대로 전달
SERIES_FUNCS = {'CROSSUP', 'CROSSDOWN', 'REF', 'AVG', 'SMAX', 'SMIN', 'COUNT', 'ABS'}

# 인자 없이 변수처럼 쓸 수 있는 이름
VARIABLE_NAMES = {
    'CLOSE', 'OPEN', 'HIGH', 'LOW', 'VOLUME', 'OBV', 'BULLISH', 'BEARISH', 'DOJI',
    'MACD', 'MACD_SIGNAL', 'MACD_HIST', 'VWAP', 'RSI', 'ATR', 'ADX', 'CCI', 'MFI',
    'STOCH_K', 'STOCH_D', 'WILLIAMS_R', 'BB_UPPER', 'BB_LOWER', 'BB_MID', 'BB_PCTB',
    'FRG_NET', 'INST_NET', 'SMART_NET', 'INDIV_NET', 'FRG_RATIO',
    'FW_PER', 'PER', 'PBR', 'TARGET_UPSIDE',
    'VIX', 'US10Y', 'USDKRW',
} | {f'{p}_{n}' for p in ('W', 'M') for n in
     ('CLOSE', 'OPEN', 'HIGH', 'LOW', 'VOLUME', 'BULLISH', 'BEARISH', 'MACD', 'MACD_SIGNAL', 'RSI', 'STOCH_K')}


# 수식 레퍼런스 (UI 도움말)
INDICATOR_REFERENCE = [
    ("가격", "CLOSE, OPEN, HIGH, LOW, VOLUME, VOLUME(20)", "종가·시가·고가·저가·거래량(20일 평균)"),
    ("이동평균", "MA(20), EMA(20), WMA(20)", "단순·지수·가중 이동평균"),
    ("신고가/신저가", "HIGHEST(20), LOWEST(10)", "직전 N일 최고가/최저가 (오늘 제외)"),
    ("모멘텀", "RSI(14), ROC(12), MOMENTUM(252), STOCH_K(14,3), CCI(20), WILLIAMS_R(14), MFI(14)", "과매수/과매도·추세 속도"),
    ("추세", "MACD(12,26,9), MACD_SIGNAL(12,26,9), MACD_HIST(12,26,9), ADX(14)", "추세 방향과 강도"),
    ("변동성", "BB_UPPER(20,2), BB_LOWER(20,2), BB_PCTB(20,2), ATR(14), STDDEV(20)", "볼린저밴드·평균진폭"),
    ("기타", "DISPARITY(20), CHANGE(1), OBV, VWAP(20)", "이격도·등락률·누적거래량"),
    ("캔들", "BULLISH, BEARISH, DOJI", "양봉·음봉·도지"),
    ("주봉/월봉", "W_CLOSE, W_MA(20), W_RSI(14), M_CLOSE, M_MA(10), M_BULLISH …", "W_=주봉, M_=월봉 (완성된 봉 기준)"),
    ("돌파", "CROSSUP(A, B), CROSSDOWN(A, B)", "A가 B를 상향/하향 돌파한 날"),
    ("시리즈", "REF(X, n), AVG(X, n), SMAX(X, n), SMIN(X, n), COUNT(조건, n), ABS(X)", "n일 전 값·평균·최대·최소·횟수"),
    ("연산자", "AND, OR, NOT, >, <, >=, <=, ==, +, -, *, /", "괄호로 우선순위 지정"),
]
