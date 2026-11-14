"""
주가 데이터 로더 (한국/미국/지수 혼합 지원 + 이중 폴백 + 워밍업 확보 + 고속 메모리 TTL 캐시)
"""
import os
import threading
import time
from datetime import timedelta
import FinanceDataReader as fdr
import yfinance as yf
import pandas as pd

_IS_LOW_MEM_CLOUD = bool(os.environ.get("RENDER") or os.environ.get("LOW_MEM_CLOUD"))
_DEFAULT_WARMUP_YEARS = 4 if _IS_LOW_MEM_CLOUD else 8
_MAX_CACHE_ENTRIES = 12 if _IS_LOW_MEM_CLOUD else 128

_PRICE_CACHE = {}
_CACHE_TTL_SECONDS = 3600
_CACHE_LOCK = threading.RLock()
_YF_LOCK = threading.Lock()


def _calc_warmup_start(start_date, warmup_years=_DEFAULT_WARMUP_YEARS):
    if isinstance(start_date, str):
        start_date = pd.to_datetime(start_date).date()
    return str(start_date - timedelta(days=int(round(float(warmup_years) * 365.25)) + 31))


def is_us_ticker(ticker: str) -> bool:
    """미국/글로벌 티커 여부 판별 (한국 6자리 숫자 코드만 False)"""
    if not ticker:
        return False
    t = str(ticker).strip()
    if t.isdigit() and len(t) == 6:
        return False
    return True


def _clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if 'Close' not in df.columns and 'Adj Close' in df.columns:
        df = df.rename(columns={'Adj Close': 'Close'})
    cols = [c for c in ('Open', 'High', 'Low', 'Close', 'Volume') if c in df.columns]
    if 'Close' not in cols:
        return pd.DataFrame()
    out = df[cols].copy()
    for c in ('Open', 'High', 'Low'):
        if c not in out.columns:
            out[c] = out['Close']
    if 'Volume' not in out.columns:
        out['Volume'] = 0.0
    out = out[['Open', 'High', 'Low', 'Close', 'Volume']]
    out.index = pd.to_datetime(out.index)
    if out.index.tz is not None:
        out.index = out.index.tz_localize(None)
    out = out[~out.index.duplicated(keep='last')].sort_index()
    out = out.dropna(subset=['Close'])
    out = out[out['Close'] > 0]
    # 시가/고가/저가가 0 또는 NaN이면 종가로 보정
    for c in ('Open', 'High', 'Low'):
        bad = out[c].isna() | (out[c] <= 0)
        out.loc[bad, c] = out.loc[bad, 'Close']
    out['Volume'] = out['Volume'].fillna(0.0)
    return out


def load_price_data(ticker, start_date, end_date, is_us=None, warmup_years=_DEFAULT_WARMUP_YEARS):
    """단일 종목 가격 데이터 로딩 (이중 폴백 + 워밍업 포함 + 메모리 TTL 캐시)"""
    t = str(ticker).strip()
    actual_start = _calc_warmup_start(start_date, warmup_years)
    end_str = str(pd.to_datetime(end_date).date() + timedelta(days=1))
    us_flag = is_us_ticker(t) if is_us is None else is_us

    cache_key = (t, actual_start, end_str, bool(us_flag))
    now = time.time()
    with _CACHE_LOCK:
        cached = _PRICE_CACHE.get(cache_key)
        if cached is not None:
            ts, cached_df = cached
            if now - ts < _CACHE_TTL_SECONDS:
                out = cached_df.copy()
                out.attrs["ticker"] = t
                return out

    def _store_and_return(res_df):
        res_df.attrs["ticker"] = t
        with _CACHE_LOCK:
            if len(_PRICE_CACHE) >= _MAX_CACHE_ENTRIES:
                oldest_key = min(_PRICE_CACHE.items(), key=lambda kv: kv[1][0])[0]
                _PRICE_CACHE.pop(oldest_key, None)
            cached_copy = res_df.copy()
            cached_copy.attrs["ticker"] = t
            _PRICE_CACHE[cache_key] = (now, cached_copy)
        return res_df

    # 1) 한국 6자리 종목: FDR 우선 → yfinance(.KS / .KQ) 폴백
    if not us_flag and not t.startswith('^'):
        try:
            df = _clean_ohlcv(fdr.DataReader(t, actual_start, end_str))
            if not df.empty:
                return _store_and_return(df)
        except Exception:
            pass
        for suffix in ('.KS', '.KQ'):
            try:
                with _YF_LOCK:
                    raw_yf = yf.Ticker(f"{t}{suffix}").history(start=actual_start, end=end_str, auto_adjust=True)
                df = _clean_ohlcv(raw_yf)
                if not df.empty:
                    return _store_and_return(df)
            except Exception:
                pass
        return pd.DataFrame()

    # 2) 미국 주식/ETF/지수: yfinance 우선 → FDR 폴백
    yf_sym = t
    try:
        with _YF_LOCK:
            raw_yf = yf.Ticker(yf_sym).history(start=actual_start, end=end_str, auto_adjust=True)
        df = _clean_ohlcv(raw_yf)
        if not df.empty:
            return _store_and_return(df)
    except Exception:
        pass

    try:
        fdr_sym = 'KS11' if t == '^KS11' else ('KQ11' if t == '^KQ11' else t)
        df = _clean_ohlcv(fdr.DataReader(fdr_sym, actual_start, end_str))
        if not df.empty:
            return _store_and_return(df)
    except Exception:
        pass
    return pd.DataFrame()


def get_cached_only(ticker: str, start_date: str, end_date: str):
    """이미 메모리에 캐시된 종목 데이터가 있으면 네트워크 호출 없이 즉시 반환"""
    t = str(ticker).strip()
    actual_start = _calc_warmup_start(start_date, _DEFAULT_WARMUP_YEARS)
    end_str = str(pd.to_datetime(end_date).date() + timedelta(days=1))
    us_flag = is_us_ticker(t)
    with _CACHE_LOCK:
        cached = _PRICE_CACHE.get((t, actual_start, end_str, bool(us_flag)))
        if cached is not None and (time.time() - cached[0]) < _CACHE_TTL_SECONDS:
            out = cached[1].copy()
            out.attrs["ticker"] = t
            return out
        # 시작일/종료일이 같거나 더 넓은 동일 티커 캐시 검색
        for (ct, cs, ce, _), (ts, cdf) in list(_PRICE_CACHE.items()):
            if ct == t and (time.time() - ts) < _CACHE_TTL_SECONDS and ce >= end_str and cs <= actual_start:
                out = cdf.copy()
                out.attrs["ticker"] = t
                return out
    return None


def get_cached_or_load(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    """실시간 수식 미리보기용 캐시 우선 로드 (워밍업 포함 후 필요 시 사용)"""
    df = load_price_data(ticker, start_date, end_date)
    return df


def get_multiple_data(ticker_list, start_date, end_date, is_us=None,
                      progress_callback=None, warmup_years=_DEFAULT_WARMUP_YEARS):
    """여러 종목 데이터 로딩 (한국/미국 혼합 포트폴리오 지원)"""
    data_dict = {}
    unique_tickers = list(dict.fromkeys([str(t).strip() for t in ticker_list if str(t).strip()]))
    if not unique_tickers:
        return data_dict

    us_only = [t for t in unique_tickers if is_us_ticker(t) and not t.startswith('^')]
    kr_or_idx = [t for t in unique_tickers if t not in us_only]

    done = 0
    total = len(unique_tickers)

    # 미국 종목이 2개 이상이면 배치 다운로드 시도
    if len(us_only) >= 2:
        actual_start = _calc_warmup_start(start_date, warmup_years)
        end_str = str(pd.to_datetime(end_date).date() + timedelta(days=1))
        try:
            with _YF_LOCK:
                raw = yf.download(" ".join(us_only), start=actual_start, end=end_str, progress=False, auto_adjust=True, group_by='ticker', threads=False)
            for t in us_only:
                done += 1
                if progress_callback:
                    progress_callback(done / total, f"📡 {t} 데이터 로딩 중...")
                try:
                    sub = raw[t] if isinstance(raw.columns, pd.MultiIndex) and t in raw.columns.get_level_values(0) else pd.DataFrame()
                    cleaned = _clean_ohlcv(sub)
                    if not cleaned.empty:
                        cleaned.attrs["ticker"] = t
                        data_dict[t] = cleaned
                except Exception:
                    pass
        except Exception:
            pass

    # 미수집 US 종목 + 한국 종목 + 지수 개별 로딩
    for t in unique_tickers:
        if t in data_dict:
            continue
        if t not in us_only or len(us_only) < 2:
            done += 1
        if progress_callback:
            progress_callback(min(1.0, done / total), f"📡 {t} 데이터 로딩 중...")
        df = load_price_data(t, start_date, end_date, is_us=is_us_ticker(t), warmup_years=warmup_years)
        if not df.empty:
            data_dict[t] = df

    return data_dict
