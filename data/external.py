"""외부 금융 데이터 연동 모듈 (수급 · 밸류에이션 · 매크로 · 시점별 뉴스)

별도 API 키 없이 공개 엔드포인트만으로 백테스트 수식 지표와 대시보드 인텔리전스를 제공한다:
  - 한국 종목 외국인/기관/개인 일별 순매수 & 외인 보유비율 (네이버 모바일 /trend API)
  - 펀더멘털 & 컨센서스(PER, Forward PER, PBR, 목표주가 괴리율, 증권사 리포트) (네이버 /integration · yfinance)
  - 글로벌 매크로 지표 (VIX, 미국 10년물 국채금리, 원/달러 환율)
  - 주가 급변동일 자동 탐지 + 해당 날짜 Google News RSS 시점별 뉴스 매칭
"""
from __future__ import annotations

import datetime
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
import math
import re
import threading
import time
from typing import Any
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
import requests
import yfinance as yf

try:
    from curl_cffi import requests as _cffi_requests
except ImportError:  # pragma: no cover
    _cffi_requests = None

from data.loader import is_us_ticker, load_price_data

_CACHE_TTL_SECONDS = 1800
_ERROR_TTL_SECONDS = 60
_LOCK = threading.RLock()

_FLOW_CACHE: dict[str, tuple[float, float, pd.DataFrame]] = {}
_FUND_CACHE: dict[str, tuple[float, float, dict[str, Any]]] = {}
_MACRO_SERIES_CACHE: dict[tuple[str, str, str], tuple[float, float, pd.Series]] = {}
_MACRO_SNAP_CACHE: tuple[float, float, dict[str, Any]] | None = None
_NEWS_CACHE: dict[tuple[str, str, str, int], tuple[float, float, dict[str, Any]]] = {}

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://m.stock.naver.com/",
}


def _normalize_kr_code(ticker: str) -> str:
    """6자리 한국 종목코드를 추출한다 (미국/지수 티커는 빈 문자열)."""
    raw = str(ticker or "").strip().upper()
    if raw.endswith(".KS") or raw.endswith(".KQ"):
        raw = raw[:-3]
    return raw if (len(raw) == 6 and raw.isdigit()) else ""


def _parse_num(val: Any) -> float | None:
    """쉼표, %, 배, 원 등 단위가 섞인 문자열을 float로 변환."""
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float, np.integer, np.floating)):
        v = float(val)
        return None if (math.isnan(v) or math.isinf(v)) else v
    s = str(val).strip()
    if not s or s in {"-", "N/A", "nan", "None", "null"}:
        return None
    cleaned = re.sub(r"[,+%\s배원ptP]", "", s)
    try:
        v = float(cleaned)
        return None if (math.isnan(v) or math.isinf(v)) else v
    except ValueError:
        m = re.search(r"-?\d+(?:\.\d+)?", cleaned)
        if m:
            try:
                return float(m.group(0))
            except ValueError:
                return None
        return None


def _http_get_text(url: str, timeout: float = 4.0, prefer_cffi: bool = True) -> str | None:
    """TLS 핑거프린트 차단을 회피해 HTTP GET 텍스트를 가져온다."""
    if prefer_cffi and _cffi_requests is not None:
        try:
            resp = _cffi_requests.get(
                url,
                headers=_DEFAULT_HEADERS,
                impersonate="chrome",
                timeout=timeout,
            )
            if resp.status_code == 200 and resp.text:
                return resp.text
        except Exception:
            pass
    try:
        resp = requests.get(url, headers=_DEFAULT_HEADERS, timeout=timeout)
        if resp.status_code == 200 and resp.text:
            return resp.text
    except Exception:
        pass
    return None


def _http_get_json(url: str, timeout: float = 4.0) -> Any:
    """HTTP GET JSON 응답을 안전하게 파싱한다."""
    if _cffi_requests is not None:
        try:
            resp = _cffi_requests.get(
                url,
                headers=_DEFAULT_HEADERS,
                impersonate="chrome",
                timeout=timeout,
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
    try:
        resp = requests.get(url, headers=_DEFAULT_HEADERS, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def fetch_kr_investor_flow(ticker: str, max_chunks: int = 16) -> pd.DataFrame:
    """한국 종목의 일별 외국인/기관/개인 순매수량과 외국인 보유비율을 조회한다."""
    code = _normalize_kr_code(ticker)
    if not code:
        return pd.DataFrame()

    now = time.time()
    with _LOCK:
        cached = _FLOW_CACHE.get(code)
        if cached and (now - cached[0]) < cached[1]:
            return cached[2].copy()

    today = datetime.date.today()
    urls = [f"https://m.stock.naver.com/api/stock/{code}/trend?pageSize=60"]
    for i in range(1, max_chunks):
        bd = (today - datetime.timedelta(days=80 * i)).strftime("%Y%m%d")
        urls.append(f"https://m.stock.naver.com/api/stock/{code}/trend?pageSize=60&bizdate={bd}")

    rows_by_date: dict[str, dict[str, Any]] = {}

    def _fetch_one(u: str) -> list[dict[str, Any]]:
        payload = _http_get_json(u, timeout=4.0)
        return payload if isinstance(payload, list) else []

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for chunk in pool.map(_fetch_one, urls):
                for item in chunk:
                    if not isinstance(item, dict):
                        continue
                    bd = str(item.get("bizdate") or "").strip()
                    if len(bd) == 8 and bd.isdigit():
                        rows_by_date[bd] = item
    except Exception:
        pass

    if not rows_by_date:
        with _LOCK:
            _FLOW_CACHE[code] = (now, _ERROR_TTL_SECONDS, pd.DataFrame())
        return pd.DataFrame()

    records = []
    for bd, item in rows_by_date.items():
        frg = _parse_num(item.get("foreignerPureBuyQuant")) or 0.0
        inst = _parse_num(item.get("organPureBuyQuant")) or 0.0
        indiv = _parse_num(item.get("individualPureBuyQuant")) or 0.0
        hold_ratio = _parse_num(item.get("foreignerHoldRatio"))
        records.append({
            "Date": pd.to_datetime(bd, format="%Y%m%d"),
            "frg_net": frg,
            "inst_net": inst,
            "smart_net": frg + inst,
            "indiv_net": indiv,
            "frg_ratio": hold_ratio if hold_ratio is not None else np.nan,
        })

    df = pd.DataFrame(records).set_index("Date").sort_index()
    df["frg_ratio"] = df["frg_ratio"].ffill().fillna(0.0)

    with _LOCK:
        if len(_FLOW_CACHE) > 96:
            _FLOW_CACHE.clear()
        _FLOW_CACHE[code] = (now, _CACHE_TTL_SECONDS, df.copy())
    return df


def _fmt_yyyymmdd(raw: str) -> str:
    s = str(raw or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def fetch_stock_fundamentals(ticker: str) -> dict[str, Any]:
    """종목의 밸류에이션(PER, Forward PER, PBR), 목표주가 괴리율, 수급/리포트 요약을 반환한다."""
    t = str(ticker or "").strip().upper()
    if not t:
        return {}

    now = time.time()
    with _LOCK:
        cached = _FUND_CACHE.get(t)
        if cached and (now - cached[0]) < cached[1]:
            return dict(cached[2])

    kr_code = _normalize_kr_code(t)
    out: dict[str, Any] = {
        "ticker": t,
        "is_kr": bool(kr_code),
        "source": "fallback",
        "name": t,
        "per": None,
        "fw_per": None,
        "pbr": None,
        "roe_pct": None,
        "div_yield_pct": None,
        "target_price": None,
        "last_price": None,
        "target_upside_pct": None,
        "recomm_mean": None,
        "frg_ratio": None,
        "inst_hold_pct": None,
        "insider_hold_pct": None,
        "short_float_pct": None,
        "market_cap_str": None,
        "researches": [],
        "flow_summary": None,
    }

    ok = False
    if kr_code:
        url = f"https://m.stock.naver.com/api/stock/{kr_code}/integration"
        data = _http_get_json(url, timeout=4.0)
        if isinstance(data, dict) and data.get("itemCode"):
            ok = True
            out["source"] = "naver"
            out["name"] = str(data.get("stockName") or t)
            info_map = {
                str(x.get("code")): x.get("value")
                for x in (data.get("totalInfos") or [])
                if isinstance(x, dict) and x.get("code")
            }
            out["per"] = _parse_num(info_map.get("per"))
            out["fw_per"] = _parse_num(info_map.get("cnsPer"))
            out["pbr"] = _parse_num(info_map.get("pbr"))
            out["div_yield_pct"] = _parse_num(info_map.get("dividendYieldRatio"))
            out["frg_ratio"] = _parse_num(info_map.get("foreignRate"))
            out["last_price"] = _parse_num(info_map.get("lastClosePrice"))
            out["market_cap_str"] = info_map.get("marketValue")

            cons = data.get("consensusInfo") or {}
            if isinstance(cons, dict):
                tgt = _parse_num(cons.get("priceTargetMean"))
                out["target_price"] = tgt
                out["recomm_mean"] = _parse_num(cons.get("recommMean"))
                if tgt and out["last_price"] and out["last_price"] > 0:
                    out["target_upside_pct"] = round(((tgt / out["last_price"]) - 1.0) * 100.0, 1)

            researches = []
            for r in (data.get("researches") or [])[:5]:
                if isinstance(r, dict) and r.get("tit"):
                    researches.append({
                        "broker": str(r.get("bnm") or ""),
                        "title": str(r.get("tit") or ""),
                        "date": _fmt_yyyymmdd(str(r.get("wdt") or "")),
                    })
            out["researches"] = researches

        # 최근 수급 요약(5일/20일) 병행 첨부
        flow_df = fetch_kr_investor_flow(kr_code)
        if not flow_df.empty:
            ok = True
            tail5 = flow_df.tail(5)
            tail20 = flow_df.tail(20)
            latest = flow_df.iloc[-1]
            out["flow_summary"] = {
                "latest_date": flow_df.index[-1].strftime("%Y-%m-%d"),
                "frg_net_1d": int(round(float(latest["frg_net"]))),
                "inst_net_1d": int(round(float(latest["inst_net"]))),
                "frg_net_5d": int(round(float(tail5["frg_net"].sum()))),
                "inst_net_5d": int(round(float(tail5["inst_net"].sum()))),
                "smart_net_5d": int(round(float(tail5["smart_net"].sum()))),
                "frg_net_20d": int(round(float(tail20["frg_net"].sum()))),
                "inst_net_20d": int(round(float(tail20["inst_net"].sum()))),
                "smart_net_20d": int(round(float(tail20["smart_net"].sum()))),
                "frg_ratio": round(float(latest["frg_ratio"]), 2),
            }
            if out["frg_ratio"] is None:
                out["frg_ratio"] = round(float(latest["frg_ratio"]), 2)
    else:
        try:
            info = yf.Ticker(t).info or {}
            if info:
                ok = True
                out["source"] = "yfinance"
                out["name"] = str(info.get("shortName") or info.get("longName") or t)
                out["per"] = _parse_num(info.get("trailingPE"))
                out["fw_per"] = _parse_num(info.get("forwardPE"))
                out["pbr"] = _parse_num(info.get("priceToBook"))
                roe = _parse_num(info.get("returnOnEquity"))
                if roe is not None:
                    out["roe_pct"] = round(roe * 100.0, 1)
                last_p = _parse_num(info.get("currentPrice") or info.get("regularMarketPrice"))
                tgt = _parse_num(info.get("targetMeanPrice"))
                out["last_price"] = last_p
                out["target_price"] = tgt
                if tgt and last_p and last_p > 0:
                    out["target_upside_pct"] = round(((tgt / last_p) - 1.0) * 100.0, 1)
                ih = _parse_num(info.get("heldPercentInstitutions"))
                inh = _parse_num(info.get("heldPercentInsiders"))
                sf = _parse_num(info.get("shortPercentOfFloat"))
                if ih is not None:
                    out["inst_hold_pct"] = round(ih * 100.0, 1)
                if inh is not None:
                    out["insider_hold_pct"] = round(inh * 100.0, 1)
                if sf is not None:
                    out["short_float_pct"] = round(sf * 100.0, 2)
                mcap = _parse_num(info.get("marketCap"))
                if mcap is not None and mcap > 0:
                    if mcap >= 1e12:
                        out["market_cap_str"] = f"${mcap / 1e12:.2f}T"
                    elif mcap >= 1e9:
                        out["market_cap_str"] = f"${mcap / 1e9:.1f}B"
                    else:
                        out["market_cap_str"] = f"${mcap / 1e6:.0f}M"
        except Exception:
            pass

    ttl = _CACHE_TTL_SECONDS if ok else _ERROR_TTL_SECONDS
    with _LOCK:
        if len(_FUND_CACHE) > 128:
            _FUND_CACHE.clear()
        _FUND_CACHE[t] = (now, ttl, dict(out))
    return out


def fetch_macro_series(symbol: str, start_date: str, end_date: str) -> pd.Series:
    """VIX(^VIX), 미국 10년물 국채금리(^TNX), 원/달러 환율(KRW=X) 일별 종가 시계열을 조회한다."""
    sym = str(symbol).strip()
    key = (sym, str(start_date), str(end_date))
    now = time.time()
    with _LOCK:
        cached = _MACRO_SERIES_CACHE.get(key)
        if cached and (now - cached[0]) < cached[1]:
            return cached[2].copy()

    s = pd.Series(dtype=float)
    try:
        df = load_price_data(sym, start_date, end_date)
        if df is not None and not df.empty and "Close" in df.columns:
            s = df["Close"].dropna().astype(float)
    except Exception:
        pass

    ttl = _CACHE_TTL_SECONDS if not s.empty else _ERROR_TTL_SECONDS
    with _LOCK:
        if len(_MACRO_SERIES_CACHE) > 64:
            _MACRO_SERIES_CACHE.clear()
        _MACRO_SERIES_CACHE[key] = (now, ttl, s.copy())
    return s


def fetch_macro_snapshot() -> dict[str, Any]:
    """최근 60일 기준 글로벌 매크로(VIX, 미국 10년물 금리, 원/달러 환율) 스냅샷."""
    global _MACRO_SNAP_CACHE
    now = time.time()
    with _LOCK:
        if _MACRO_SNAP_CACHE and (now - _MACRO_SNAP_CACHE[0]) < _MACRO_SNAP_CACHE[1]:
            return dict(_MACRO_SNAP_CACHE[2])

    end_d = datetime.date.today().strftime("%Y-%m-%d")
    start_d = (datetime.date.today() - datetime.timedelta(days=45)).strftime("%Y-%m-%d")

    def _load_one(sym: str) -> tuple[float | None, float | None]:
        ser = fetch_macro_series(sym, start_d, end_d)
        if ser.empty:
            return None, None
        last = float(ser.iloc[-1])
        prev = float(ser.iloc[-2]) if len(ser) >= 2 else last
        return round(last, 2), round(last - prev, 2)

    snap: dict[str, Any] = {
        "vix": None,
        "vix_chg": None,
        "vix_regime": "-",
        "us10y": None,
        "us10y_chg": None,
        "usdkrw": None,
        "usdkrw_chg": None,
    }
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            f_vix = pool.submit(_load_one, "^VIX")
            f_tnx = pool.submit(_load_one, "^TNX")
            f_fx = pool.submit(_load_one, "KRW=X")
            vix, vix_c = f_vix.result()
            tnx, tnx_c = f_tnx.result()
            fx, fx_c = f_fx.result()
        snap["vix"], snap["vix_chg"] = vix, vix_c
        snap["us10y"], snap["us10y_chg"] = tnx, tnx_c
        snap["usdkrw"], snap["usdkrw_chg"] = fx, fx_c
        if vix is not None:
            if vix >= 28:
                snap["vix_regime"] = "공포 경계 (VIX≥28)"
            elif vix >= 20:
                snap["vix_regime"] = "변동성 확대 (20~28)"
            else:
                snap["vix_regime"] = "안정 국면 (<20)"
    except Exception:
        pass

    ok = any(snap[k] is not None for k in ("vix", "us10y", "usdkrw"))
    ttl = _CACHE_TTL_SECONDS if ok else _ERROR_TTL_SECONDS
    with _LOCK:
        _MACRO_SNAP_CACHE = (now, ttl, dict(snap))
    return snap


def _clean_news_title(title: str, source: str = "") -> str:
    t = re.sub(r"\s+", " ", str(title or "")).strip()
    if source and t.endswith(f" - {source}"):
        t = t[: -len(f" - {source}")].strip()
    return t


def _fetch_google_news_rss(query: str, is_kr: bool = True, limit: int = 3) -> list[dict[str, Any]]:
    """Google News RSS 검색으로 헤드라인 목록을 가져온다."""
    hl, gl, ceid = ("ko", "KR", "KR:ko") if is_kr else ("en-US", "US", "US:en")
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl={hl}&gl={gl}&ceid={ceid}"
    xml_text = _http_get_text(url, timeout=4.0, prefer_cffi=False)
    if not xml_text:
        return []
    out: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(xml_text)
        for item in root.findall(".//item")[:limit]:
            raw_title = item.findtext("title") or ""
            link = (item.findtext("link") or "").strip()
            pub_raw = (item.findtext("pubDate") or "").strip()
            source_el = item.find("source")
            src = (source_el.text or "").strip() if source_el is not None else ""
            title = _clean_news_title(raw_title, src)
            pub_date = ""
            if pub_raw:
                try:
                    pub_date = parsedate_to_datetime(pub_raw).strftime("%Y-%m-%d")
                except Exception:
                    pub_date = pub_raw[:16]
            if title:
                out.append({
                    "title": title,
                    "source": src,
                    "date": pub_date,
                    "url": link,
                })
    except Exception:
        return []
    return out


def fetch_point_in_time_shock_news(
    ticker: str,
    stock_name: str,
    price_df: pd.DataFrame | None,
    max_events: int = 5,
) -> dict[str, Any]:
    """종목의 역사적 급등/급락일을 자동 탐지하고 해당 시점의 실제 뉴스 헤드라인을 붙인다."""
    t = str(ticker or "").strip().upper()
    q_name = str(stock_name or t).strip() or t
    is_kr = bool(_normalize_kr_code(t)) or not is_us_ticker(t)

    date_key = ""
    shock_days: list[tuple[pd.Timestamp, float]] = []
    if price_df is not None and not price_df.empty and "Close" in price_df.columns:
        close = price_df["Close"].dropna()
        if len(close) >= 5:
            date_key = f"{close.index[0].strftime('%Y-%m-%d')}:{close.index[-1].strftime('%Y-%m-%d')}"
            rets = (close.pct_change() * 100.0).dropna()
            picked: list[tuple[pd.Timestamp, float]] = []
            recent_cutoff = close.index[-1] - pd.Timedelta(days=365)
            recent_rets = rets[rets.index >= recent_cutoff]
            recent_cands = recent_rets[recent_rets.abs() >= 2.5]
            if len(recent_cands) < 2:
                recent_cands = recent_rets[recent_rets.abs() >= 1.8]
            recent_sorted = recent_cands.reindex(recent_cands.abs().sort_values(ascending=False).index)
            recent_quota = max(2, (max_events + 1) // 2)
            for dt, r in recent_sorted.items():
                if all(abs((dt - p_dt).days) >= 25 for p_dt, _ in picked):
                    picked.append((pd.Timestamp(dt), float(r)))
                if len(picked) >= recent_quota:
                    break

            candidates = rets[rets.abs() >= 3.5].reindex(
                rets[rets.abs() >= 3.5].abs().sort_values(ascending=False).index
            )
            for dt, r in candidates.items():
                if len(picked) >= max_events:
                    break
                if all(abs((dt - p_dt).days) >= 25 for p_dt, _ in picked):
                    picked.append((pd.Timestamp(dt), float(r)))
            shock_days = sorted(picked, key=lambda x: x[0])

    cache_key = ("kr_v2", t, q_name, date_key, max_events)
    now = time.time()
    with _LOCK:
        cached = _NEWS_CACHE.get(cache_key)
        if cached and (now - cached[0]) < cached[1]:
            return dict(cached[2])

    kr_query_map = {
        "^KS11": "코스피 증시",
        "^KQ11": "코스닥 증시",
        "^GSPC": "뉴욕증시 S&P500",
        "^IXIC": "뉴욕증시 나스닥",
        "^SOX": "필라델피아 반도체지수",
        "^N225": "일본 닛케이 증시",
        "SPY": "뉴욕증시 S&P500",
        "QQQ": "뉴욕증시 나스닥",
        "TQQQ": "나스닥 기술주",
        "SOXL": "미국 반도체주",
        "NVDA": "엔비디아 주가",
        "TSLA": "테슬라 주가",
        "AAPL": "애플 주가",
        "MSFT": "마이크로소프트 주가",
        "AMZN": "아마존 주가",
        "GOOGL": "구글 알파벳 주가",
        "META": "메타 주가",
        "AVGO": "브로드컴 주가",
    }
    has_hangul = bool(re.search(r"[가-힣]", q_name))
    kr_search_term = kr_query_map.get(t) or (
        f"{q_name} 증시" if t.startswith("^") else (q_name if has_hangul else f"{q_name} 미국주식 주가")
    )

    def _query_for_day(pair: tuple[pd.Timestamp, float]) -> dict[str, Any]:
        dt, ret = pair
        d_str = dt.strftime("%Y-%m-%d")
        after_d = (dt - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        before_d = (dt + datetime.timedelta(days=2)).strftime("%Y-%m-%d")
        items = _fetch_google_news_rss(
            f"{kr_search_term} after:{after_d} before:{before_d}",
            is_kr=True,
            limit=2,
        )
        if not items and not is_kr:
            items = _fetch_google_news_rss(
                f"{q_name} stock after:{after_d} before:{before_d}",
                is_kr=False,
                limit=2,
            )
        top = items[0] if items else None
        arrow = "📈" if ret > 0 else "📉"
        if top:
            head = top["title"]
            short_head = head if len(head) <= 13 else (head[:12] + "…")
            label = f"{arrow} {ret:+.1f}% {short_head}"
            full_title = f"[{d_str} {ret:+.1f}%] {head}"
            src = top.get("source", "")
            url = top.get("url", "")
        else:
            label = f"{arrow} {d_str[5:]} ({ret:+.1f}%)"
            full_title = f"[{d_str} {ret:+.1f}%] {q_name} 일간 급변동"
            src = "가격 변동 자동탐지"
            url = ""
        return {
            "date": d_str,
            "label": label,
            "full_title": full_title,
            "source": src,
            "url": url,
            "ret_pct": round(ret, 2),
            "type": "stock_up" if ret > 0 else "stock_down",
            "category": "stock",
        }

    auto_events: list[dict[str, Any]] = []
    latest_news: list[dict[str, Any]] = []
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            f_latest = pool.submit(_fetch_google_news_rss, kr_search_term, True, 5)
            f_shocks = [pool.submit(_query_for_day, pair) for pair in shock_days]
            latest_news = f_latest.result()
            auto_events = [f.result() for f in f_shocks]
    except Exception:
        pass

    result = {
        "auto_events": auto_events,
        "latest_news": latest_news,
        "fetched_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    ok = bool(auto_events or latest_news)
    ttl = _CACHE_TTL_SECONDS if ok else _ERROR_TTL_SECONDS
    with _LOCK:
        if len(_NEWS_CACHE) > 96:
            _NEWS_CACHE.clear()
        _NEWS_CACHE[cache_key] = (now, ttl, dict(result))
    return result
