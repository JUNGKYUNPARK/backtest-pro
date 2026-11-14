"""
Backtest Pro — 초고속 Quant Workbench 웹 서버 (FastAPI + Uvicorn)
- 페이지 새로고침/깜빡임 없는 SPA 프론트엔드 서빙
- 실시간 수식 AST 검증 · 한국어 번역 · 숫자 슬라이더 추출 · 신호 미리보기 API
- 전략 백테스트 / 듀얼 레짐 스위칭 / 자산배분 적립식 / 13개 전략 대결 / 2D 파라미터 최적화 API
"""
import argparse
import atexit
import base64
import datetime
import hmac
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
STRATEGIES_DIR = os.path.join(BASE_DIR, "data", "strategies")
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(STRATEGIES_DIR, exist_ok=True)

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from data.universe import QUICK_PICKS, CURATED_UNIVERSE, format_universe_label, get_all_universe
from data.loader import load_price_data, get_multiple_data, get_cached_only, is_us_ticker
from engine.fees import FeeModel, detect_asset_type
from engine.indicators import (
    INDICATOR_MAP,
    INDICATOR_REFERENCE,
    SERIES_FUNCS,
    VARIABLE_NAMES,
    MA,
    BB_UPPER,
    BB_LOWER,
    _resample_ohlcv,
)
from engine.parser import ConditionParser, to_signal, uses_timeframe
from engine.strategies import STRATEGIES, STRATEGY_MAP, CATEGORY_ORDER
from engine.simulator import SimConfig, run_backtest, run_vbo_backtest
from engine.regime_simulator import run_regime_switch, run_allocation_backtest
from engine.tearsheet import (
    METRIC_HELP,
    analyze_live_signal,
    calculate_tearsheet,
    run_monte_carlo,
)
from engine.optimizer import (
    build_custom_formula_slots,
    compare_all_strategies,
    optimize_custom_formula,
    optimize_strategy_params,
)
from engine.formula_tools import (
    append_formula_block,
    build_formula_snippets,
    convert_formula_timeframe,
    extract_chart_overlays,
    extract_formula_numbers,
    inspect_formula_live_values,
    replace_formula_numbers,
    translate_formula_to_korean,
)

app = FastAPI(title="Backtest Pro Quant Workbench", version="3.5")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ═══════════════════════════════════════════════════════════════
# 주요 역사적 시장 사건 (국가지수/수익률 차트 매핑용) & 멀티스캐너 바스켓
# ═══════════════════════════════════════════════════════════════


def _build_external_intel(ticker: str, price_df: Optional[pd.DataFrame] = None) -> dict:
    """종목의 수급/밸류/매크로/시점별 급등락 뉴스 인텔리전스 페이로드를 구성한다."""
    t = str(ticker or "").strip().upper()
    if not t:
        return {}
    try:
        from concurrent.futures import ThreadPoolExecutor
        from data.external import (
            fetch_macro_snapshot,
            fetch_point_in_time_shock_news,
            fetch_stock_fundamentals,
        )

        with ThreadPoolExecutor(max_workers=2) as pool:
            f_fund = pool.submit(fetch_stock_fundamentals, t)
            f_macro = pool.submit(fetch_macro_snapshot)
            fund = f_fund.result()
            macro = f_macro.result()

        stock_name = str((fund or {}).get("name") or t)
        news_pack = fetch_point_in_time_shock_news(t, stock_name, price_df, max_events=5)
        return {
            "ticker": t,
            "fundamentals": fund or {},
            "macro": macro or {},
            "auto_events": (news_pack or {}).get("auto_events", []),
            "latest_news": (news_pack or {}).get("latest_news", []),
            "fetched_at": (news_pack or {}).get("fetched_at", ""),
        }
    except Exception:
        return {"ticker": t, "fundamentals": {}, "macro": {}, "auto_events": [], "latest_news": []}


HISTORICAL_EVENTS = [
    {"date": "2008-09-15", "label": "리먼 파산", "market": "GLOBAL", "type": "crisis", "category": "macro"},
    {"date": "2011-08-05", "label": "미국 신용강등", "market": "GLOBAL", "type": "crisis", "category": "macro"},
    {"date": "2015-08-24", "label": "차이나 쇼크", "market": "GLOBAL", "type": "crisis", "category": "macro"},
    {"date": "2016-06-24", "label": "브렉시트 쇼크", "market": "GLOBAL", "type": "shock", "category": "macro"},
    {"date": "2018-10-10", "label": "미·중 무역분쟁", "market": "GLOBAL", "type": "crisis", "category": "macro"},
    {"date": "2020-03-23", "label": "코로나 저점", "market": "GLOBAL", "type": "shock", "category": "macro"},
    {"date": "2020-11-09", "label": "백신 랠리", "market": "GLOBAL", "type": "bull", "category": "macro"},
    {"date": "2021-01-11", "label": "동학개미 피크", "market": "KR", "type": "bull", "category": "macro"},
    {"date": "2022-02-24", "label": "우크라 전쟁", "market": "GLOBAL", "type": "crisis", "category": "macro"},
    {"date": "2022-06-16", "label": "연준 자이언트스텝", "market": "GLOBAL", "type": "crisis", "category": "macro"},
    {"date": "2022-10-21", "label": "레고랜드·긴축 저점", "market": "KR", "type": "shock", "category": "macro"},
    {"date": "2023-03-10", "label": "SVB 은행파산", "market": "GLOBAL", "type": "crisis", "category": "macro"},
    {"date": "2023-05-25", "label": "엔비디아 AI 쇼크", "market": "GLOBAL", "type": "bull", "category": "macro"},
    {"date": "2023-11-06", "label": "공매도 전면금지", "market": "KR", "type": "policy", "category": "macro"},
    {"date": "2024-02-26", "label": "밸류업 발표", "market": "KR", "type": "policy", "category": "macro"},
    {"date": "2024-08-05", "label": "엔캐리 블랙먼데이", "market": "GLOBAL", "type": "shock", "category": "macro"},
    {"date": "2024-09-18", "label": "연준 빅컷 인하", "market": "GLOBAL", "type": "policy", "category": "macro"},
    {"date": "2024-11-06", "label": "트럼프 2기 당선", "market": "GLOBAL", "type": "bull", "category": "macro"},
    {"date": "2024-12-04", "label": "국내 계엄·정치충격", "market": "KR", "type": "shock", "category": "macro"},
    {"date": "2025-04-07", "label": "관세 쇼크", "market": "GLOBAL", "type": "crisis", "category": "macro"},
]

SCANNER_BASKETS = {
    "core10": ["005930", "000660", "069500", "122630", "SPY", "QQQ", "TQQQ", "NVDA", "AAPL", "TLT"],
    "kr_top": ["005930", "000660", "069500", "122630", "005380", "035420", "105560", "012450"],
    "us_top": ["SPY", "QQQ", "TQQQ", "SOXL", "NVDA", "AAPL", "MSFT", "TSLA", "TLT", "GLD"],
}


# ═══════════════════════════════════════════════════════════════
# 원클릭 추천 시나리오 레시피 (처음 사용자 / 수식 테스트용)
# ═══════════════════════════════════════════════════════════════

SCENARIO_RECIPES = [
    {
        "id": "kodex_monthly",
        "title": "📦 KODEX200 월봉 눌림목",
        "badge": "실전 월봉",
        "desc": "월봉 60개월선 -1% 이탈 매수 · 데드눌림 매도",
        "mode": "strategy",
        "tickers": ["069500"],
        "strategy_id": "custom",
        "entry_expr": "M_CLOSE <= M_MA(60) * 0.99",
        "exit_expr": "M_BEARISH AND M_MA(5) > M_MA(10) AND M_CLOSE <= M_MA(5) * 0.99",
        "start_date": "2010-01-01",
    },
    {
        "id": "samsung_rsi",
        "title": "🇰🇷 삼성전자 RSI 과매도",
        "badge": "역추세",
        "desc": "RSI(14) 30 미만 과매도 진입 · 70 초과 익절",
        "mode": "strategy",
        "tickers": ["005930"],
        "strategy_id": "rsi",
        "entry_expr": "RSI(14) < 30",
        "exit_expr": "RSI(14) > 70",
        "start_date": "2018-01-01",
    },
    {
        "id": "qqq_golden",
        "title": "🚀 QQQ 골든크로스",
        "badge": "추세추종",
        "desc": "20일선이 60일선 돌파 매수 · 이탈 매도",
        "mode": "strategy",
        "tickers": ["QQQ"],
        "strategy_id": "golden_cross",
        "entry_expr": "CROSSUP(MA(20), MA(60))",
        "exit_expr": "CROSSDOWN(MA(20), MA(60))",
        "start_date": "2015-01-01",
    },
    {
        "id": "tqqq_weekly",
        "title": "🔥 TQQQ 주봉 방어추세",
        "badge": "주봉 레버리지",
        "desc": "주봉 20주선 & 주봉 RSI>50 상승장만 탑승",
        "mode": "strategy",
        "tickers": ["TQQQ"],
        "strategy_id": "custom",
        "entry_expr": "W_CLOSE > W_MA(20) AND W_RSI(14) > 50",
        "exit_expr": "W_CLOSE < W_MA(10)",
        "start_date": "2016-01-01",
    },
    {
        "id": "nasdaq_regime",
        "title": "🔄 나스닥 1x↔3x 레짐",
        "badge": "레짐 스위칭",
        "desc": "QQQ 200일선 위엔 TQQQ 3배, 아래엔 현금 피신",
        "mode": "regime",
        "tickers": ["QQQ"],
        "start_date": "2015-01-01",
        "regime": {
            "signal_ticker": "QQQ",
            "t1_ticker": "TQQQ",
            "t2_ticker": "",
            "entry_expr": "CLOSE > MA(200)",
            "eval_freq": "daily",
        },
    },
    {
        "id": "allweather_dca",
        "title": "⚖️ 올웨더 4자산 적립식",
        "badge": "자산배분+월적립",
        "desc": "SPY·TLT·IEF·GLD 분기 리밸런싱 + 월 50만원 적립",
        "mode": "allocation",
        "tickers": ["SPY", "TLT", "IEF", "GLD"],
        "start_date": "2015-01-01",
        "allocation": {
            "weights": {"SPY": 30, "TLT": 40, "IEF": 15, "GLD": 15},
            "rebalance_freq": "quarterly",
            "monthly_contribution": 500000,
        },
    },
    {
        "id": "kr_smart_flow",
        "title": "🏦 삼성전자 외인·기관 + 선행PER",
        "badge": "수급+밸류",
        "desc": "외국인+기관 5일 순매수 & 선행PER 25배 이하 추세 돌파",
        "mode": "strategy",
        "tickers": ["005930"],
        "start_date": "2023-08-01",
        "strategy_id": "custom",
        "entry_expr": "SMART_NET(5) > 0 AND CLOSE > MA(20) AND FW_PER < 25",
        "exit_expr": "SMART_NET(5) < 0 AND CLOSE < MA(20)",
    },
    {
        "id": "vix_fear_reversal",
        "title": "🌪️ VIX 공포 진정 QQQ 반등",
        "badge": "매크로+기술적",
        "desc": "공포지수 VIX 25 미만 안정 국면에서 20일선 상향 매수",
        "mode": "strategy",
        "tickers": ["QQQ"],
        "start_date": "2020-01-01",
        "strategy_id": "custom",
        "entry_expr": "VIX < 25 AND CLOSE > MA(20)",
        "exit_expr": "VIX >= 28 OR CLOSE < MA(60)",
    },
]


def _build_autocomplete_items() -> List[dict]:
    """수식 입력창 IntelliSense 자동완성 항목 목록 생성"""
    items = [
        # 기본 가격/캔들
        {"token": "CLOSE", "insert": "CLOSE", "cat": "가격/일봉", "desc": "오늘 종가"},
        {"token": "OPEN", "insert": "OPEN", "cat": "가격/일봉", "desc": "오늘 시가"},
        {"token": "HIGH", "insert": "HIGH", "cat": "가격/일봉", "desc": "오늘 고가"},
        {"token": "LOW", "insert": "LOW", "cat": "가격/일봉", "desc": "오늘 저가"},
        {"token": "VOLUME", "insert": "VOLUME", "cat": "가격/일봉", "desc": "오늘 거래량 (평균: VOLUME(20))"},
        {"token": "BULLISH", "insert": "BULLISH", "cat": "캔들", "desc": "양봉 (종가 > 시가)"},
        {"token": "BEARISH", "insert": "BEARISH", "cat": "캔들", "desc": "음봉 (종가 < 시가)"},
        {"token": "DOJI", "insert": "DOJI", "cat": "캔들", "desc": "도지 캔들"},
        # 주요 지표 함수
        {"token": "MA(n)", "insert": "MA(20)", "cat": "이동평균", "desc": "N일 단순 이동평균선"},
        {"token": "EMA(n)", "insert": "EMA(20)", "cat": "이동평균", "desc": "N일 지수 이동평균선"},
        {"token": "WMA(n)", "insert": "WMA(20)", "cat": "이동평균", "desc": "N일 가중 이동평균선"},
        {"token": "RSI(n)", "insert": "RSI(14)", "cat": "모멘텀", "desc": "RSI 과매수(>70)/과매도(<30) 지표"},
        {"token": "DISPARITY(n)", "insert": "DISPARITY(20)", "cat": "이격도", "desc": "N일 이평선 대비 현재가 비율 (100=동일)"},
        {"token": "HIGHEST(n)", "insert": "HIGHEST(20)", "cat": "신고가/신저가", "desc": "직전 N일 최고가 (미래참조 제거)"},
        {"token": "LOWEST(n)", "insert": "LOWEST(10)", "cat": "신고가/신저가", "desc": "직전 N일 최저가 (미래참조 제거)"},
        {"token": "BB_UPPER(n,k)", "insert": "BB_UPPER(20, 2)", "cat": "변동성", "desc": "볼린저 밴드 상단"},
        {"token": "BB_MID(n)", "insert": "BB_MID(20)", "cat": "변동성", "desc": "볼린저 밴드 중심선"},
        {"token": "BB_LOWER(n,k)", "insert": "BB_LOWER(20, 2)", "cat": "변동성", "desc": "볼린저 밴드 하단"},
        {"token": "BB_PCTB(n,k)", "insert": "BB_PCTB(20, 2)", "cat": "변동성", "desc": "볼린저 %B (0=하단, 1=상단)"},
        {"token": "MACD(f,s,sig)", "insert": "MACD(12, 26, 9)", "cat": "추세", "desc": "MACD 선"},
        {"token": "MACD_SIGNAL", "insert": "MACD_SIGNAL(12, 26, 9)", "cat": "추세", "desc": "MACD 시그널 선"},
        {"token": "MACD_HIST", "insert": "MACD_HIST(12, 26, 9)", "cat": "추세", "desc": "MACD 히스토그램"},
        {"token": "STOCH_K", "insert": "STOCH_K(14, 3)", "cat": "모멘텀", "desc": "스토캐스틱 Fast/Slow %K"},
        {"token": "STOCH_D", "insert": "STOCH_D(14, 3, 3)", "cat": "모멘텀", "desc": "스토캐스틱 %D"},
        {"token": "MOMENTUM(n)", "insert": "MOMENTUM(252)", "cat": "모멘텀", "desc": "N일 전 대비 수익률(%)"},
        {"token": "ROC(n)", "insert": "ROC(12)", "cat": "모멘텀", "desc": "N일 가격 변화율(%)"},
        {"token": "CHANGE(n)", "insert": "CHANGE(1)", "cat": "가격/일봉", "desc": "N일 전 대비 등락률(%)"},
        {"token": "ATR(n)", "insert": "ATR(14)", "cat": "변동성", "desc": "평균 진폭 (Average True Range)"},
        {"token": "ADX(n)", "insert": "ADX(14)", "cat": "추세", "desc": "추세 강도 지수 (25 이상 강한 추세)"},
        {"token": "CCI(n)", "insert": "CCI(20)", "cat": "모멘텀", "desc": "상품 채널 지수 (±100)"},
        {"token": "MFI(n)", "insert": "MFI(14)", "cat": "모멘텀", "desc": "거래량 반영 자금흐름 RSI"},
        {"token": "WILLIAMS_R(n)", "insert": "WILLIAMS_R(14)", "cat": "모멘텀", "desc": "윌리엄스 %R (-80 이하 과매도)"},
        {"token": "OBV", "insert": "OBV", "cat": "거래량", "desc": "누적 거래량 지표"},
        {"token": "VWAP(n)", "insert": "VWAP(20)", "cat": "거래량", "desc": "거래량 가중 평균가"},
        # 돌파 / 시리즈 함수
        {"token": "CROSSUP(A, B)", "insert": "CROSSUP(MA(5), MA(20))", "cat": "돌파/시리즈", "desc": "A가 B를 아래→위로 돌파한 날"},
        {"token": "CROSSDOWN(A, B)", "insert": "CROSSDOWN(MA(5), MA(20))", "cat": "돌파/시리즈", "desc": "A가 B를 위→아래로 이탈한 날"},
        {"token": "REF(X, n)", "insert": "REF(CLOSE, 1)", "cat": "돌파/시리즈", "desc": "N봉 전의 X 값"},
        {"token": "AVG(X, n)", "insert": "AVG(RSI(14), 5)", "cat": "돌파/시리즈", "desc": "임의 수식 X의 N봉 평균"},
        {"token": "SMAX(X, n)", "insert": "SMAX(HIGH, 5)", "cat": "돌파/시리즈", "desc": "임의 수식 X의 N봉 최댓값"},
        {"token": "SMIN(X, n)", "insert": "SMIN(LOW, 5)", "cat": "돌파/시리즈", "desc": "임의 수식 X의 N봉 최솟값"},
        {"token": "COUNT(cond, n)", "insert": "COUNT(BULLISH, 5)", "cat": "돌파/시리즈", "desc": "최근 N봉 중 조건이 참인 횟수"},
        {"token": "ABS(X)", "insert": "ABS(CHANGE(1))", "cat": "돌파/시리즈", "desc": "절댓값"},
        # 논리 연산자
        {"token": "AND", "insert": "AND ", "cat": "연산자", "desc": "그리고 (두 조건 모두 참일 때)"},
        {"token": "OR", "insert": "OR ", "cat": "연산자", "desc": "또는 (둘 중 하나라도 참일 때)"},
        {"token": "NOT", "insert": "NOT ", "cat": "연산자", "desc": "조건 부정 (참 ↔ 거짓 반전)"},
        {"token": "TRUE", "insert": "TRUE", "cat": "연산자", "desc": "항상 참 (무조건 보유 등)"},
        {"token": "FALSE", "insert": "FALSE", "cat": "연산자", "desc": "항상 거짓 (매도 안 함 등)"},
    ]

    # 주봉(W_) & 월봉(M_) 접두사 항목 추가
    mtf_specs = [
        ("CLOSE", "CLOSE", "종가"),
        ("OPEN", "OPEN", "시가"),
        ("HIGH", "HIGH", "고가"),
        ("LOW", "LOW", "저가"),
        ("VOLUME", "VOLUME", "거래량"),
        ("MA(n)", "MA(20)", "이동평균선"),
        ("EMA(n)", "EMA(20)", "지수 이동평균선"),
        ("RSI(n)", "RSI(14)", "RSI 지표"),
        ("MACD", "MACD(12, 26, 9)", "MACD 선"),
        ("MACD_SIGNAL", "MACD_SIGNAL(12, 26, 9)", "MACD 시그널"),
        ("BULLISH", "BULLISH", "양봉"),
        ("BEARISH", "BEARISH", "음봉"),
        ("ROC(n)", "ROC(12)", "변화율"),
        ("STOCH_K", "STOCH_K(14, 3)", "스토캐스틱 %K"),
        ("BB_UPPER", "BB_UPPER(20, 2)", "볼린저 상단"),
        ("BB_LOWER", "BB_LOWER(20, 2)", "볼린저 하단"),
        ("HIGHEST(n)", "HIGHEST(20)", "직전 N봉 최고가"),
        ("LOWEST(n)", "LOWEST(10)", "직전 N봉 최저가"),
    ]
    ext_specs = [
        ("FRG_NET(n)", "FRG_NET(5)", "[한국] 외국인 N일 누적 순매수"),
        ("INST_NET(n)", "INST_NET(5)", "[한국] 기관 N일 누적 순매수"),
        ("SMART_NET(n)", "SMART_NET(5)", "[한국] 외국인+기관 N일 누적 순매수"),
        ("INDIV_NET(n)", "INDIV_NET(5)", "[한국] 개인 N일 누적 순매수"),
        ("FRG_RATIO", "FRG_RATIO", "[한국] 외국인 보유지분율 (%)"),
        ("FW_PER", "FW_PER", "컨센서스 선행 PER (Forward PER)"),
        ("PER", "PER", "후행 PER"),
        ("PBR", "PBR", "주가순자산비율 (PBR)"),
        ("TARGET_UPSIDE", "TARGET_UPSIDE", "애널리스트 목표가 대비 상승여력 (%)"),
        ("VIX", "VIX", "CBOE 공포지수 VIX (VIX(n) 이동평균 지원)"),
        ("US10Y", "US10Y", "미국 10년물 국채금리 (%)"),
        ("USDKRW", "USDKRW", "원/달러 환율"),
    ]
    for tok, ins, desc in ext_specs:
        items.append({"token": tok, "insert": ins, "cat": "🏦 수급·밸류·매크로", "desc": desc})

    for prefix, p_label, cat in (("W_", "주봉", "🟦 주봉"), ("M_", "월봉", "🟧 월봉")):
        for tok, ins, desc in mtf_specs:
            # 예: M_MA(60) 많이 쓰이므로 기본 예시 살짝 조정
            if prefix == "M_" and ins == "MA(20)":
                ins_val = "M_MA(10)"
            else:
                ins_val = f"{prefix}{ins}"
            items.append({
                "token": f"{prefix}{tok}",
                "insert": ins_val,
                "cat": cat,
                "desc": f"[{p_label}] {desc}",
            })
    return items


AUTOCOMPLETE_ITEMS = _build_autocomplete_items()


# ═══════════════════════════════════════════════════════════════
# 직렬화 & 공통 헬퍼
# ═══════════════════════════════════════════════════════════════

def _clean_json(obj: Any) -> Any:
    """NumPy / Pandas / Timestamp 객체를 안전하게 JSON 직렬화"""
    if obj is None:
        return None
    if isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        v = float(obj)
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(obj, (pd.Timestamp, datetime.datetime, datetime.date)):
        return obj.strftime("%Y-%m-%d")
    if isinstance(obj, pd.Series):
        return [_clean_json(x) for x in obj.tolist()]
    if isinstance(obj, np.ndarray):
        return [_clean_json(x) for x in obj.tolist()]
    if isinstance(obj, pd.DataFrame):
        return [_clean_json(rec) for rec in obj.to_dict(orient="records")]
    if isinstance(obj, dict):
        return {str(k): _clean_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean_json(x) for x in obj]
    return str(obj)


def _list_saved_strategies() -> List[dict]:
    items = []
    if not os.path.exists(STRATEGIES_DIR):
        return items
    for fname in sorted(os.listdir(STRATEGIES_DIR)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(STRATEGIES_DIR, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["filename"] = fname
            items.append(data)
        except Exception:
            continue
    return items


def _serialize_strategies() -> List[dict]:
    out = []
    for s in STRATEGIES:
        en, ex = s.formulas(s.defaults())
        out.append({
            "id": s.id,
            "name": s.name,
            "emoji": s.emoji,
            "label": s.label,
            "category": s.category,
            "level": s.level,
            "summary": s.summary,
            "how": s.how,
            "good_when": s.good_when,
            "bad_when": s.bad_when,
            "kind": s.kind,
            "default_entry": en,
            "default_exit": ex,
            "params": [
                {
                    "key": p.key,
                    "label": p.label,
                    "min": p.min,
                    "max": p.max,
                    "default": p.default,
                    "step": p.step,
                    "help": p.help,
                    "grid": list(p.grid) if p.grid else [],
                }
                for p in s.params
            ],
        })
    return out


def _detect_asset_types(tickers: List[str], universe_list: List[dict]) -> Dict[str, str]:
    mkt_map = {u["code"]: u.get("market") for u in universe_list}
    return {t: detect_asset_type(t, mkt_map.get(t)) for t in tickers}


def _build_sim_config(payload: dict) -> SimConfig:
    slippage = float(payload.get("slippage_pct", 0.05) or 0.0) / 100.0
    comm_val = payload.get("custom_comm_pct", None)
    comm = float(comm_val) / 100.0 if comm_val is not None and comm_val != "" else None
    tax_exempt = bool(payload.get("tax_exempt", False))
    fee = FeeModel(slippage=slippage, commission=comm, tax=0.0 if tax_exempt else None)
    return SimConfig(
        initial_capital=float(payload.get("initial_capital", 10_000_000) or 10_000_000),
        max_positions=max(1, int(payload.get("max_positions", 1) or 1)),
        execution=str(payload.get("execution", "close") or "close"),
        stop_loss=float(payload.get("stop_loss_pct", 0.0) or 0.0) / 100.0,
        take_profit=float(payload.get("take_profit_pct", 0.0) or 0.0) / 100.0,
        trailing_stop=float(payload.get("trailing_stop_pct", 0.0) or 0.0) / 100.0,
        atr_stop=float(payload.get("atr_stop", 0.0) or 0.0),
        max_hold_days=int(payload.get("max_hold_days", 0) or 0),
        mtf_close_only=bool(payload.get("mtf_close_only", True)),
        rank_expr=str(payload.get("rank_expr", "") or ""),
        rank_desc=bool(payload.get("rank_desc", True)),
        fee=fee,
    )


def _load_market_filter(market_filter: str, start_date: str, end_date: str) -> Optional[pd.Series]:
    if not market_filter or market_filter == "none":
        return None
    sym = "^KS11" if market_filter == "kospi200" else ("^GSPC" if market_filter == "sp500" else None)
    if not sym:
        return None
    mdf = load_price_data(sym, start_date, end_date)
    if mdf is None or mdf.empty:
        return None
    return mdf["Close"] > MA(mdf, 200)


def _build_candle_payload(res, tickers: List[str], raw_data: Optional[dict] = None, candle_tf: str = "auto") -> Optional[dict]:
    """첫 번째 종목의 캔들(일봉/주봉/월봉) + 수식 기반 동적 지표선/오실레이터 + 매수/매도 마커 생성"""
    if not tickers:
        return None
    primary = tickers[0]
    df_sliced = (res.price_data or {}).get(primary)
    df_warm = (raw_data or {}).get(primary)
    if df_warm is None or df_warm.empty:
        df_warm = df_sliced
    if df_warm is None or df_warm.empty:
        return None

    sim_start = df_sliced.index[0] if (df_sliced is not None and not df_sliced.empty) else df_warm.index[0]
    sim_end = df_sliced.index[-1] if (df_sliced is not None and not df_sliced.empty) else df_warm.index[-1]

    entry_expr = getattr(res, "entry_expr", "") or ""
    exit_expr = getattr(res, "exit_expr", "") or ""
    eff_tf = candle_tf if candle_tf in ("D", "W", "M") else (uses_timeframe(f"{entry_expr} {exit_expr}") or "D")

    if eff_tf in ("W", "M"):
        df_res = _resample_ohlcv(df_warm, eff_tf)
        df_view = df_res[(df_res.index >= sim_start - pd.Timedelta(days=31)) & (df_res.index <= sim_end)]
        if df_view.empty:
            df_view = df_res.tail(240)
    else:
        df_sub = df_warm[(df_warm.index >= sim_start) & (df_warm.index <= sim_end)]
        df_view = df_sub.tail(4500)

    if df_view.empty:
        return None

    dates_str = [d.strftime("%Y-%m-%d") for d in df_view.index]

    ma20 = MA(df_warm, 20).reindex(df_view.index)
    ma60 = MA(df_warm, 60).reindex(df_view.index)
    bb_up = BB_UPPER(df_warm, 20, 2).reindex(df_view.index)
    bb_lo = BB_LOWER(df_warm, 20, 2).reindex(df_view.index)

    # 사용자가 입력한 수식의 실제 지표선 & 보조지표 평가 (워밍업 포함 데이터로 계산)
    custom_overlays = []
    oscillators = []
    try:
        ext = extract_chart_overlays(entry_expr, exit_expr)
        parser = ConditionParser(df_warm)
        for ov_expr in ext.get("price_overlays", []):
            try:
                s = parser.evaluate(ov_expr)
                if isinstance(s, pd.Series) and not s.empty:
                    vals = s.reindex(df_view.index).ffill().tolist()
                    custom_overlays.append({"name": ov_expr, "values": vals})
            except Exception:
                pass
        for osc_expr in ext.get("oscillators", []):
            try:
                s = parser.evaluate(osc_expr)
                if isinstance(s, pd.Series) and not s.empty:
                    vals = s.reindex(df_view.index).ffill().tolist()
                    oscillators.append({"name": osc_expr, "values": vals})
            except Exception:
                pass
    except Exception:
        pass

    def _snap_date(dt_ts):
        if eff_tf == "D":
            return dt_ts.strftime("%Y-%m-%d")
        pos = df_view.index.searchsorted(dt_ts)
        pos = min(max(0, pos), len(df_view.index) - 1)
        return df_view.index[pos].strftime("%Y-%m-%d")

    buy_markers = []
    sell_markers = []
    if res.trades is not None and not res.trades.empty:
        tdf = res.trades[res.trades["ticker"] == primary] if "ticker" in res.trades.columns else res.trades
        for _, tr in tdf.iterrows():
            ed_ts = pd.to_datetime(tr["entry_date"])
            buy_markers.append({
                "date": _snap_date(ed_ts),
                "raw_date": ed_ts.strftime("%Y-%m-%d"),
                "price": float(tr["entry_price"]),
                "shares": int(tr.get("shares", 0) or 0),
            })
            if not bool(tr.get("is_open", False)):
                xd_ts = pd.to_datetime(tr["exit_date"])
                sell_markers.append({
                    "date": _snap_date(xd_ts),
                    "raw_date": xd_ts.strftime("%Y-%m-%d"),
                    "price": float(tr["exit_price"]),
                    "return_pct": float(tr.get("return_pct", 0.0) or 0.0),
                    "reason": str(tr.get("reason", "")),
                })

    return {
        "ticker": primary,
        "tf": eff_tf,
        "dates": dates_str,
        "open": df_view["Open"].tolist(),
        "high": df_view["High"].tolist(),
        "low": df_view["Low"].tolist(),
        "close": df_view["Close"].tolist(),
        "volume": df_view["Volume"].tolist(),
        "ma20": ma20.tolist(),
        "ma60": ma60.tolist(),
        "bb_upper": bb_up.tolist(),
        "bb_lower": bb_lo.tolist(),
        "custom_overlays": custom_overlays,
        "oscillators": oscillators,
        "buys": buy_markers,
        "sells": sell_markers,
    }


def _safe_filename(name: str) -> str:
    clean = re.sub(r'[\\/:*?"<>|]+', "_", name.strip())
    return clean or f"strategy_{int(time.time())}"


# ═══════════════════════════════════════════════════════════════
# REST API 엔드포인트
# ═══════════════════════════════════════════════════════════════

@app.get("/")
def serve_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index_path):
        return JSONResponse({"error": "static/index.html not found"}, status_code=404)
    return FileResponse(index_path, headers={"Cache-Control": "no-cache"})


def _is_local_request(req: Request) -> bool:
    if os.environ.get("RENDER"):
        return False
    if req.headers.get("cf-connecting-ip") or req.headers.get("x-forwarded-for") or req.headers.get("x-forwarded-host"):
        return False
    client_ip = (req.client.host if req.client else "").strip().lower()
    if client_ip not in ("127.0.0.1", "::1", "localhost", "testclient"):
        return False
    host_hdr = req.headers.get("host", "").split(":")[0].strip().lower()
    return host_hdr in ("localhost", "127.0.0.1", "::1", "[::1]", "testserver", "")


def _require_local_admin(req: Request):
    if not _is_local_request(req):
        raise HTTPException(
            status_code=403,
            detail="보안상 모바일·외부접속 및 클라우드 배포 설정은 서버가 실행 중인 내 PC(localhost)에서만 접근할 수 있습니다.",
        )


@app.get("/api/init")
def api_init(req: Request):
    universe = get_all_universe(include_full_krx=False)
    universe_fmt = [
        {
            "code": u["code"],
            "name": u["name"],
            "market": u.get("market", "KRX"),
            "label": format_universe_label(u),
        }
        for u in universe
    ]
    quick_picks_fmt = [
        {"label": label, "tickers": tickers, "desc": desc}
        for label, tickers, desc in QUICK_PICKS
    ]
    return JSONResponse(_clean_json({
        "universe": universe_fmt,
        "quick_picks": quick_picks_fmt,
        "strategies": _serialize_strategies(),
        "recipes": SCENARIO_RECIPES,
        "snippets": {
            "D": build_formula_snippets(""),
            "W": build_formula_snippets("W_"),
            "M": build_formula_snippets("M_"),
        },
        "indicator_reference": [
            {"category": c, "syntax": s, "desc": d} for c, s, d in INDICATOR_REFERENCE
        ],
        "autocomplete": AUTOCOMPLETE_ITEMS,
        "metric_help": METRIC_HELP,
        "category_order": CATEGORY_ORDER,
        "historical_events": HISTORICAL_EVENTS,
        "saved_strategies": _list_saved_strategies(),
        "today": datetime.date.today().strftime("%Y-%m-%d"),
        "is_local_admin": _is_local_request(req),
    }))


@app.post("/api/universe/refresh")
def api_universe_refresh():
    universe = get_all_universe(include_full_krx=True)
    return JSONResponse(_clean_json({
        "count": len(universe),
        "universe": [
            {
                "code": u["code"],
                "name": u["name"],
                "market": u.get("market", "KRX"),
                "label": format_universe_label(u),
            }
            for u in universe
        ],
    }))


@app.post("/api/formula/inspect")
async def api_formula_inspect(req: Request):
    body = await req.json()
    entry_expr = str(body.get("entry_expr", "") or "")
    exit_expr = str(body.get("exit_expr", "") or "")
    ticker = str(body.get("ticker", "") or "").strip()
    start_date = str(body.get("start_date", "2020-01-01") or "2020-01-01")
    end_date = str(body.get("end_date", datetime.date.today().strftime("%Y-%m-%d")) or datetime.date.today().strftime("%Y-%m-%d"))
    mtf_close_only = bool(body.get("mtf_close_only", True))
    load_data = bool(body.get("load_data", False))

    en_ok, en_msg = ConditionParser.validate_syntax(entry_expr) if entry_expr.strip() else (False, "매수 조건이 비어 있습니다.")
    ex_ok, ex_msg = ConditionParser.validate_syntax(exit_expr) if exit_expr.strip() else (True, "")

    en_ko = translate_formula_to_korean(entry_expr) if en_ok else f"⚠️ {en_msg}"
    ex_ko = translate_formula_to_korean(exit_expr) if (exit_expr.strip() and ex_ok) else ("매도 조건 없음 (보유 유지)" if not exit_expr.strip() else f"⚠️ {ex_msg}")

    en_nums = extract_formula_numbers(entry_expr) if en_ok else []
    ex_nums = extract_formula_numbers(exit_expr) if (exit_expr.strip() and ex_ok) else []
    linked_slots = build_custom_formula_slots(entry_expr, exit_expr, sync_linked=True) if (en_ok and ex_ok) else []

    live = None
    if ticker and en_ok and ex_ok:
        try:
            df = load_price_data(ticker, start_date, end_date) if load_data else get_cached_only(ticker, start_date, end_date)
            if df is not None and not df.empty:
                live = inspect_formula_live_values(
                    df, entry_expr, exit_expr, mtf_close_only=mtf_close_only,
                    start_date=start_date, end_date=end_date,
                )
                try:
                    en_sig = to_signal(df, entry_expr)
                    ex_sig = to_signal(df, exit_expr) if exit_expr.strip() else pd.Series(False, index=df.index)
                    tf = uses_timeframe(f"{entry_expr} {exit_expr}") if mtf_close_only else None
                    if tf:
                        from engine.simulator import _period_end_mask
                        mask = _period_end_mask(df.index, tf)
                        en_sig, ex_sig = en_sig & mask, ex_sig & mask
                    s_ts, e_ts = pd.Timestamp(start_date), pd.Timestamp(end_date)
                    en_p = en_sig[(en_sig.index >= s_ts) & (en_sig.index <= e_ts)]
                    ex_p = ex_sig[(ex_sig.index >= s_ts) & (ex_sig.index <= e_ts)]
                    if isinstance(live, dict) and live.get("valid"):
                        live["period_entry_count"] = int(en_p.sum())
                        live["period_exit_count"] = int(ex_p.sum())
                except Exception:
                    pass
        except Exception as e:
            live = {"valid": False, "error": str(e)}

    return JSONResponse(_clean_json({
        "entry_valid": en_ok,
        "entry_error": en_msg if not en_ok else "",
        "entry_ko": en_ko,
        "entry_numbers": en_nums,
        "exit_valid": ex_ok,
        "exit_error": ex_msg if not ex_ok else "",
        "exit_ko": ex_ko,
        "exit_numbers": ex_nums,
        "linked_slots": [
            {"index": s["index"], "label": s["label"], "value": s["value"], "targets": s["targets"]}
            for s in linked_slots
        ],
        "live": live,
    }))


@app.post("/api/formula/transform")
async def api_formula_transform(req: Request):
    body = await req.json()
    action = str(body.get("action", "") or "")
    entry_expr = str(body.get("entry_expr", "") or "")
    exit_expr = str(body.get("exit_expr", "") or "")

    if action == "timeframe":
        tf = str(body.get("tf", "D") or "D")
        target = str(body.get("target", "both") or "both")
        if target in ("entry", "both"):
            entry_expr = convert_formula_timeframe(entry_expr, tf)
        if target in ("exit", "both"):
            exit_expr = convert_formula_timeframe(exit_expr, tf)
    elif action == "replace_numbers":
        en_vals = body.get("entry_values")
        ex_vals = body.get("exit_values")
        if isinstance(en_vals, list):
            entry_expr = replace_formula_numbers(entry_expr, en_vals)
        if isinstance(ex_vals, list):
            exit_expr = replace_formula_numbers(exit_expr, ex_vals)
    elif action == "swap":
        entry_expr, exit_expr = exit_expr, entry_expr
    elif action == "append_block":
        target = str(body.get("target", "entry") or "entry")
        block = str(body.get("block", "") or "")
        join_op = str(body.get("join_op", "AND") or "AND")
        if target == "entry":
            entry_expr = append_formula_block(entry_expr, block, join_op)
        else:
            exit_expr = append_formula_block(exit_expr, block, join_op)

    en_ok, en_msg = ConditionParser.validate_syntax(entry_expr) if entry_expr.strip() else (False, "매수 조건이 비어 있습니다.")
    ex_ok, ex_msg = ConditionParser.validate_syntax(exit_expr) if exit_expr.strip() else (True, "")
    linked_slots = build_custom_formula_slots(entry_expr, exit_expr, sync_linked=True) if (en_ok and ex_ok) else []

    return JSONResponse(_clean_json({
        "entry_expr": entry_expr,
        "exit_expr": exit_expr,
        "entry_valid": en_ok,
        "entry_error": en_msg if not en_ok else "",
        "entry_ko": translate_formula_to_korean(entry_expr) if en_ok else f"⚠️ {en_msg}",
        "entry_numbers": extract_formula_numbers(entry_expr) if en_ok else [],
        "exit_valid": ex_ok,
        "exit_error": ex_msg if not ex_ok else "",
        "exit_ko": translate_formula_to_korean(exit_expr) if (exit_expr.strip() and ex_ok) else "매도 조건 없음",
        "exit_numbers": extract_formula_numbers(exit_expr) if (exit_expr.strip() and ex_ok) else [],
        "linked_slots": [
            {"index": s["index"], "label": s["label"], "value": s["value"], "targets": s["targets"]}
            for s in linked_slots
        ],
    }))


@app.post("/api/backtest")
async def api_backtest(req: Request):
    body = await req.json()
    mode = str(body.get("mode", "strategy") or "strategy")
    start_date = str(body.get("start_date", "2018-01-01") or "2018-01-01")
    end_date = str(body.get("end_date", datetime.date.today().strftime("%Y-%m-%d")) or datetime.date.today().strftime("%Y-%m-%d"))
    tickers = [str(t).strip() for t in (body.get("tickers") or []) if str(t).strip()]
    universe = get_all_universe(include_full_krx=False)
    cfg = _build_sim_config(body)
    oos_ratio = float(body.get("oos_ratio", 0) or 0)
    mc_horizon = int(body.get("mc_horizon_days", 252) or 252)
    market_filter = str(body.get("market_filter", "none") or "none")
    benchmark_ticker = str(body.get("benchmark_ticker", "auto") or "auto")
    candle_tf = str(body.get("candle_tf", "auto") or "auto")
    raw_data = {}

    try:
        # ─── 모드 1: 듀얼 레짐 스위칭 ───
        if mode == "regime":
            rcfg = body.get("regime") or {}
            sig_t = str(rcfg.get("signal_ticker", "QQQ") or "QQQ").strip()
            t1_t = str(rcfg.get("t1_ticker", "TQQQ") or "TQQQ").strip()
            t2_t = str(rcfg.get("t2_ticker", "") or "").strip()
            entry_expr = str(rcfg.get("entry_expr", "CLOSE > MA(200)") or "CLOSE > MA(200)").strip()
            eval_freq = str(rcfg.get("eval_freq", "daily") or "daily")

            ok, msg = ConditionParser.validate_syntax(entry_expr)
            if not ok:
                raise HTTPException(status_code=400, detail=f"레짐 조건 수식 오류: {msg}")

            need = list(dict.fromkeys([t for t in (sig_t, t1_t, t2_t) if t]))
            data = get_multiple_data(need, start_date, end_date)
            raw_data = data
            if sig_t not in data:
                raise HTTPException(status_code=400, detail=f"기준 종목({sig_t}) 데이터를 불러올 수 없습니다.")
            t1_df = data.get(t1_t)
            t2_df = data.get(t2_t) if t2_t else None
            atypes = _detect_asset_types(need, universe)
            res = run_regime_switch(
                signal_df=data[sig_t], t1_df=t1_df, t2_df=t2_df,
                signal_code=sig_t, t1_code=t1_t, t2_code=t2_t,
                entry_expr=entry_expr, cfg=cfg, start=start_date, end=end_date,
                eval_freq=eval_freq, asset_types=atypes,
            )
            chart_tickers = [t for t in (t1_t, sig_t) if t in res.price_data]

        # ─── 모드 2: 다자산 비중 배분 & 적립식 ───
        elif mode == "allocation":
            acfg = body.get("allocation") or {}
            weights_pct = acfg.get("weights") or {}
            rebal = str(acfg.get("rebalance_freq", "quarterly") or "quarterly")
            dca = float(acfg.get("monthly_contribution", 0) or 0)
            a_tickers = [str(k).strip() for k, v in weights_pct.items() if float(v or 0) > 0]
            if not a_tickers:
                raise HTTPException(status_code=400, detail="비중이 0%보다 큰 종목을 최소 1개 설정해주세요.")
            data = get_multiple_data(a_tickers, start_date, end_date)
            raw_data = data
            weights = {t: float(weights_pct[t]) / 100.0 for t in a_tickers if t in data}
            if not weights:
                raise HTTPException(status_code=400, detail="선택한 종목 데이터를 불러오지 못했습니다.")
            atypes = _detect_asset_types(list(weights.keys()), universe)
            res = run_allocation_backtest(
                data=data, weights=weights, cfg=cfg, start=start_date, end=end_date,
                rebalance_freq=rebal, monthly_contribution=dca, asset_types=atypes,
            )
            chart_tickers = list(weights.keys())

        # ─── 모드 3: 수식 & 전략 백테스트 ───
        else:
            if not tickers:
                raise HTTPException(status_code=400, detail="종목을 1개 이상 선택해주세요.")
            data = get_multiple_data(tickers, start_date, end_date)
            raw_data = data
            if not data:
                raise HTTPException(status_code=400, detail="종목 데이터를 불러오지 못했습니다. 티커와 기간을 확인해주세요.")
            atypes = _detect_asset_types(list(data.keys()), universe)
            market_ok = _load_market_filter(market_filter, start_date, end_date)

            strat_id = str(body.get("strategy_id", "custom") or "custom")
            strat = STRATEGY_MAP.get(strat_id)
            if strat and strat.kind == "vbo":
                sparams = body.get("strategy_params") or strat.defaults()
                res = run_vbo_backtest(
                    data=data,
                    k_value=float(sparams.get("k", 0.5)),
                    ma_filter=int(sparams.get("ma", 5)),
                    cfg=cfg, start=start_date, end=end_date,
                    asset_types=atypes, market_ok=market_ok,
                )
            else:
                entry_expr = str(body.get("entry_expr", "") or "").strip()
                exit_expr = str(body.get("exit_expr", "") or "").strip()
                ok1, msg1 = ConditionParser.validate_syntax(entry_expr) if entry_expr else (False, "매수 조건식을 입력해주세요.")
                if not ok1:
                    raise HTTPException(status_code=400, detail=f"매수 수식 오류: {msg1}")
                if exit_expr:
                    ok2, msg2 = ConditionParser.validate_syntax(exit_expr)
                    if not ok2:
                        raise HTTPException(status_code=400, detail=f"매도 수식 오류: {msg2}")
                res = run_backtest(
                    data=data, entry_expr=entry_expr, exit_expr=exit_expr,
                    cfg=cfg, start=start_date, end=end_date,
                    asset_types=atypes, market_ok=market_ok,
                )
            chart_tickers = list(data.keys())

        # 벤치마크 교체 옵션 처리
        if benchmark_ticker and benchmark_ticker != "auto":
            bdf = load_price_data(benchmark_ticker, start_date, end_date)
            if bdf is not None and not bdf.empty:
                from engine.regime_simulator import _build_single_benchmark
                bat = detect_asset_type(benchmark_ticker)
                res.benchmark = _build_single_benchmark(
                    bdf, res.equity.index, cfg.initial_capital, cfg.fee, bat, f"{benchmark_ticker} 보유"
                )
                res.benchmark_name = f"{benchmark_ticker} 보유"

        # 검증 기간 분할 날짜 계산
        oos_start = None
        if oos_ratio > 0 and len(res.equity) > 30:
            cut_idx = int(len(res.equity) * (1.0 - min(0.5, max(0.1, oos_ratio / 100.0))))
            if 10 < cut_idx < len(res.equity) - 10:
                oos_start = res.equity.index[cut_idx]

        metrics, drawdown, monthly, yearly, rolling_6m, is_oos = calculate_tearsheet(
            res, oos_start=oos_start
        )

        grade = metrics.pop("_grade", {})
        bench_dd = metrics.pop("_bench_dd", pd.Series(dtype=float))
        underwater_df = metrics.pop("_underwater", pd.DataFrame())

        live_signal = analyze_live_signal(res, cfg) if mode != "allocation" else {}
        if live_signal and chart_tickers and chart_tickers[0] in raw_data:
            live_diag = inspect_formula_live_values(
                raw_data[chart_tickers[0]], res.entry_expr, res.exit_expr or "",
                mtf_close_only=cfg.mtf_close_only, start_date=start_date, end_date=end_date,
            )
            if isinstance(live_diag, dict) and live_diag.get("valid"):
                live_signal["signal_diag"] = live_diag

        mc = run_monte_carlo(res, horizon_days=mc_horizon, n_sims=400)
        candle = _build_candle_payload(res, chart_tickers, raw_data=raw_data, candle_tf=candle_tf)

        # 시계열 직렬화
        eq_df = res.equity
        init_c = float(metrics.get("Initial Capital", cfg.initial_capital) or cfg.initial_capital)
        eq_s = eq_df["Equity"].astype(float)
        bench_s = res.benchmark.reindex(eq_df.index).ffill() if res.benchmark is not None else eq_s
        b0 = float(bench_s.iloc[0]) if len(bench_s) > 0 and bench_s.iloc[0] > 0 else init_c

        series_payload = {
            "dates": [d.strftime("%Y-%m-%d") for d in eq_df.index],
            "equity": eq_s.tolist(),
            "cash": eq_df["Cash"].astype(float).tolist(),
            "holdings": eq_df["Holdings"].astype(float).tolist(),
            "benchmark": bench_s.astype(float).tolist(),
            "benchmark_name": res.benchmark_name,
            "drawdown": drawdown.reindex(eq_df.index).fillna(0.0).tolist(),
            "bench_dd": bench_dd.reindex(eq_df.index).fillna(0.0).tolist() if not bench_dd.empty else [],
            "equity_pct": ((eq_s / init_c - 1.0) * 100.0).tolist(),
            "benchmark_pct": ((bench_s / b0 - 1.0) * 100.0).tolist(),
        }
        if mode == "regime" and res.extra and "regime_series" in res.extra:
            series_payload["regime"] = res.extra["regime_series"].reindex(eq_df.index).fillna("현금").tolist()

        monthly_payload = None
        if monthly is not None and not monthly.empty:
            monthly_payload = {
                "years": [int(y) for y in monthly.index],
                "columns": [str(c) for c in monthly.columns],
                "values": [[_clean_json(round(float(v) * 100.0, 2) if pd.notna(v) else None) for v in row] for row in monthly.to_numpy()],
            }

        mc_payload = None
        if mc and "curves" in mc:
            c_df = mc["curves"]
            mc_payload = {
                "start_capital": mc["start_capital"],
                "horizon_days": mc["horizon_days"],
                "n_sims": mc["n_sims"],
                "p10_final": mc["p10_final"],
                "p50_final": mc["p50_final"],
                "p90_final": mc["p90_final"],
                "p10_ret": mc["p10_ret"],
                "p50_ret": mc["p50_ret"],
                "p90_ret": mc["p90_ret"],
                "prob_profit": mc["prob_profit"],
                "prob_plus20": mc["prob_plus20"],
                "dates": [d.strftime("%Y-%m-%d") for d in c_df.index],
                "p10": c_df["p10"].tolist(),
                "p25": c_df["p25"].tolist(),
                "p50": c_df["p50"].tolist(),
                "p75": c_df["p75"].tolist(),
                "p90": c_df["p90"].tolist(),
            }

        rolling_payload = None
        if rolling_6m is not None and not rolling_6m.dropna().empty:
            r_clean = rolling_6m.dropna()
            rolling_payload = {
                "dates": [d.strftime("%Y-%m-%d") for d in r_clean.index],
                "values": r_clean.tolist(),
            }

        trades_list = []
        if res.trades is not None and not res.trades.empty:
            trades_list = res.trades.to_dict(orient="records")

        primary_t = chart_tickers[0] if chart_tickers else (tickers[0] if tickers else "")
        primary_df = raw_data.get(primary_t) if isinstance(raw_data, dict) else None
        external_intel = _build_external_intel(primary_t, primary_df)

        payload_out = {
            "mode": mode,
            "tickers": tickers,
            "entry_expr": res.entry_expr,
            "exit_expr": res.exit_expr,
            "entry_ko": translate_formula_to_korean(res.entry_expr) if mode == "strategy" and res.entry_expr else res.entry_expr,
            "exit_ko": translate_formula_to_korean(res.exit_expr) if mode == "strategy" and res.exit_expr else res.exit_expr,
            "metrics": metrics,
            "grade": grade,
            "is_oos": is_oos,
            "live_signal": live_signal,
            "external_intel": external_intel,
            "series": series_payload,
            "candle": candle,
            "monthly_table": monthly_payload,
            "yearly": yearly.to_dict(orient="records") if yearly is not None and not yearly.empty else [],
            "rolling_6m": rolling_payload,
            "underwater": underwater_df.to_dict(orient="records") if underwater_df is not None and not underwater_df.empty else [],
            "monte_carlo": mc_payload,
            "trades": trades_list,
            "diagnostics": res.diagnostics,
        }
        return JSONResponse(_clean_json(payload_out))

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"시뮬레이션 오류: {e}")


@app.get("/api/external/{ticker}")
async def api_external(ticker: str, days: int = 365):
    """특정 종목의 외부 수급·밸류에이션·매크로·시점별 뉴스 스냅샷을 반환한다."""
    t = str(ticker or "").strip()
    if not t:
        raise HTTPException(status_code=400, detail="티커를 입력해주세요.")
    end_d = datetime.date.today().strftime("%Y-%m-%d")
    start_d = (datetime.date.today() - datetime.timedelta(days=max(60, min(days, 3650)))).strftime("%Y-%m-%d")
    df = load_price_data(t, start_d, end_d)
    return JSONResponse(_clean_json(_build_external_intel(t, df)))


NATIONAL_INDICES = [
    {"symbol": "^KS11", "name": "코스피 (KOSPI)", "short": "코스피", "country": "KR", "flag": "🇰🇷", "fallback": "069500"},
    {"symbol": "^KQ11", "name": "코스닥 (KOSDAQ)", "short": "코스닥", "country": "KR", "flag": "🇰🇷", "fallback": "229200"},
    {"symbol": "^GSPC", "name": "S&P 500", "short": "S&P500", "country": "US", "flag": "🇺🇸", "fallback": "SPY"},
    {"symbol": "^IXIC", "name": "나스닥 종합 (NASDAQ)", "short": "나스닥", "country": "US", "flag": "🇺🇸", "fallback": "QQQ"},
    {"symbol": "^SOX", "name": "필라델피아 반도체 (SOX)", "short": "반도체(SOX)", "country": "US", "flag": "🇺🇸", "fallback": "SOXX"},
    {"symbol": "^N225", "name": "닛케이 225 (Nikkei)", "short": "닛케이225", "country": "JP", "flag": "🇯🇵", "fallback": "EWJ"},
]


def _compute_index_event_metrics(dates: list, close_s: pd.Series, pct_s: pd.Series, dd_s: pd.Series, target_date: str) -> dict:
    from bisect import bisect_left

    n = len(dates)
    if n == 0:
        return {}
    pos = bisect_left(dates, target_date)
    if pos >= n:
        pos = n - 1
    c_val = float(close_s.iloc[pos])
    p_val = float(pct_s.iloc[pos])
    d_val = float(dd_s.iloc[pos])
    ret_1m = round(float((close_s.iloc[min(pos + 20, n - 1)] / c_val - 1.0) * 100.0), 2) if pos + 5 < n and c_val > 0 else None
    ret_3m = round(float((close_s.iloc[min(pos + 60, n - 1)] / c_val - 1.0) * 100.0), 2) if pos + 15 < n and c_val > 0 else None
    return {
        "matched_date": dates[pos],
        "index_close": round(c_val, 2),
        "index_pct": round(p_val, 2),
        "dd_at_event": round(d_val, 2),
        "ret_1m": ret_1m,
        "ret_3m": ret_3m,
    }


@app.get("/api/index-events")
async def api_index_events(symbol: str = "^KS11", start_date: str = "2018-01-01", end_date: str = ""):
    """국가 대표 지수 시계열과 주요 역사적 사건 · 지수 급변동 뉴스를 매핑하여 반환한다."""
    sym = str(symbol or "^KS11").strip()
    start_d = str(start_date or "2018-01-01").strip()
    end_d = str(end_date or "").strip() or datetime.date.today().strftime("%Y-%m-%d")

    meta = next((item for item in NATIONAL_INDICES if item["symbol"].upper() == sym.upper()), None)
    if meta is None:
        meta = {"symbol": sym, "name": sym, "short": sym, "country": "GLOBAL", "flag": "🌍", "fallback": ""}

    used_symbol = sym
    df = load_price_data(sym, start_d, end_d)
    if (df is None or df.empty) and meta.get("fallback"):
        used_symbol = meta["fallback"]
        df = load_price_data(used_symbol, start_d, end_d)

    if df is None or df.empty:
        raise HTTPException(status_code=400, detail=f"지수 데이터를 불러올 수 없습니다: {sym}")

    s_ts = pd.to_datetime(start_d)
    e_ts = pd.to_datetime(end_d)
    df_view = df.loc[(df.index >= s_ts) & (df.index <= e_ts)].copy()
    if df_view.empty:
        df_view = df.tail(250).copy()

    close_s = pd.to_numeric(df_view["Close"], errors="coerce").dropna()
    if close_s.empty:
        raise HTTPException(status_code=400, detail=f"유효한 종가 시계열이 없습니다: {sym}")
    df_view = df_view.loc[close_s.index]
    open_s = pd.to_numeric(df_view["Open"], errors="coerce").fillna(close_s) if "Open" in df_view.columns else close_s
    high_s = pd.to_numeric(df_view["High"], errors="coerce").fillna(close_s) if "High" in df_view.columns else close_s
    low_s = pd.to_numeric(df_view["Low"], errors="coerce").fillna(close_s) if "Low" in df_view.columns else close_s

    first_close = float(close_s.iloc[0])
    pct_s = ((close_s / first_close) - 1.0) * 100.0 if first_close > 0 else close_s * 0.0
    dd_s = ((close_s / close_s.cummax()) - 1.0) * 100.0
    dates = [d.strftime("%Y-%m-%d") for d in df_view.index]

    historical_mapped = []
    min_d, max_d = dates[0], dates[-1]
    for ev in HISTORICAL_EVENTS:
        ev_date = str(ev.get("date", ""))
        if min_d <= ev_date <= max_d:
            m = _compute_index_event_metrics(dates, close_s, pct_s, dd_s, ev_date)
            historical_mapped.append({
                **ev,
                "category": "macro",
                **m,
            })

    auto_mapped = []
    try:
        from data.external import fetch_point_in_time_shock_news
        news_pack = fetch_point_in_time_shock_news(used_symbol, meta["short"], df_view, max_events=6)
        for aev in (news_pack or {}).get("auto_events", []):
            a_date = str(aev.get("date", ""))
            if not a_date:
                continue
            m = _compute_index_event_metrics(dates, close_s, pct_s, dd_s, a_date)
            chg = float(aev.get("chg_pct", 0.0) or 0.0)
            auto_mapped.append({
                "date": a_date,
                "label": str(aev.get("title") or f"{meta['short']} {chg:+.1f}%"),
                "full_title": str(aev.get("full_title") or aev.get("title") or ""),
                "url": str(aev.get("url") or ""),
                "source": str(aev.get("source") or ""),
                "chg_pct": round(chg, 2),
                "market": meta["country"],
                "type": "bull" if chg >= 0 else "shock",
                "category": "shock",
                **m,
            })
    except Exception:
        auto_mapped = []

    combined = sorted([*historical_mapped, *auto_mapped], key=lambda x: str(x.get("date", "")))
    end_close = float(close_s.iloc[-1])
    summary = {
        "period_return_pct": round(float(pct_s.iloc[-1]), 2),
        "mdd_pct": round(float(dd_s.min()), 2),
        "start_close": round(first_close, 2),
        "end_close": round(end_close, 2),
        "high_close": round(float(close_s.max()), 2),
        "low_close": round(float(close_s.min()), 2),
        "historical_count": len(historical_mapped),
        "shock_count": len(auto_mapped),
        "start_date": min_d,
        "end_date": max_d,
    }

    return JSONResponse(_clean_json({
        "symbol": sym,
        "used_symbol": used_symbol,
        "name": meta["name"],
        "short": meta["short"],
        "country": meta["country"],
        "flag": meta["flag"],
        "indices_catalog": NATIONAL_INDICES,
        "summary": summary,
        "series": {
            "dates": dates,
            "opens": [round(float(v), 2) for v in open_s.tolist()],
            "highs": [round(float(v), 2) for v in high_s.tolist()],
            "lows": [round(float(v), 2) for v in low_s.tolist()],
            "closes": [round(float(v), 2) for v in close_s.tolist()],
            "pct": [round(float(v), 2) for v in pct_s.tolist()],
            "drawdown": [round(float(v), 2) for v in dd_s.tolist()],
        },
        "historical_events": historical_mapped,
        "auto_events": auto_mapped,
        "combined_events": combined,
    }))


@app.post("/api/compare")
async def api_compare(req: Request):
    body = await req.json()
    tickers = [str(t).strip() for t in (body.get("tickers") or []) if str(t).strip()]
    if not tickers:
        raise HTTPException(status_code=400, detail="종목을 1개 이상 선택해주세요.")
    start_date = str(body.get("start_date", "2018-01-01") or "2018-01-01")
    end_date = str(body.get("end_date", datetime.date.today().strftime("%Y-%m-%d")) or datetime.date.today().strftime("%Y-%m-%d"))
    market_filter = str(body.get("market_filter", "none") or "none")

    data = get_multiple_data(tickers, start_date, end_date)
    if not data:
        raise HTTPException(status_code=400, detail="종목 데이터를 불러올 수 없습니다.")
    universe = get_all_universe(include_full_krx=False)
    atypes = _detect_asset_types(list(data.keys()), universe)
    cfg = _build_sim_config(body)
    market_ok = _load_market_filter(market_filter, start_date, end_date)

    df_rank, curves = compare_all_strategies(
        data=data, cfg=cfg, start=start_date, end=end_date,
        asset_types=atypes, market_ok=market_ok,
    )
    if df_rank.empty:
        raise HTTPException(status_code=400, detail="비교 실행에 실패했습니다.")

    curves_payload = {"dates": [], "series": []}
    if curves:
        first_s = next(iter(curves.values()))
        curves_payload["dates"] = [d.strftime("%Y-%m-%d") for d in first_s.index]
        for label in df_rank["전략명"].tolist():
            if label in curves:
                s = curves[label].reindex(first_s.index).ffill()
                curves_payload["series"].append({
                    "name": label,
                    "values": s.tolist(),
                    "pct": ((s / cfg.initial_capital - 1.0) * 100.0).tolist(),
                })

    return JSONResponse(_clean_json({
        "ranking": df_rank.to_dict(orient="records"),
        "curves": curves_payload,
    }))


@app.post("/api/optimize")
async def api_optimize(req: Request):
    body = await req.json()
    tickers = [str(t).strip() for t in (body.get("tickers") or []) if str(t).strip()]
    if not tickers:
        raise HTTPException(status_code=400, detail="종목을 1개 이상 선택해주세요.")
    start_date = str(body.get("start_date", "2018-01-01") or "2018-01-01")
    end_date = str(body.get("end_date", datetime.date.today().strftime("%Y-%m-%d")) or datetime.date.today().strftime("%Y-%m-%d"))
    market_filter = str(body.get("market_filter", "none") or "none")
    sort_by = str(body.get("sort_by", "샤프지수") or "샤프지수")
    opt_mode = str(body.get("opt_mode", "custom") or "custom")
    strat_id = str(body.get("strategy_id", "custom") or "custom")
    entry_expr = str(body.get("entry_expr", "") or "").strip()
    exit_expr = str(body.get("exit_expr", "") or "").strip()

    data = get_multiple_data(tickers, start_date, end_date)
    if not data:
        raise HTTPException(status_code=400, detail="종목 데이터를 불러올 수 없습니다.")
    universe = get_all_universe(include_full_krx=False)
    atypes = _detect_asset_types(list(data.keys()), universe)
    cfg = _build_sim_config(body)
    market_ok = _load_market_filter(market_filter, start_date, end_date)

    best_info = {}
    if opt_mode == "strategy" and strat_id in STRATEGY_MAP and STRATEGY_MAP[strat_id].params:
        strat = STRATEGY_MAP[strat_id]
        df_opt, best_params = optimize_strategy_params(
            strategy=strat, data=data, cfg=cfg, start=start_date, end=end_date,
            asset_types=atypes, market_ok=market_ok, sort_by=sort_by,
        )
        en_b, ex_b = strat.formulas(best_params)
        best_info = {
            "entry_expr": en_b,
            "exit_expr": ex_b,
            "params": best_params,
            "summary": " · ".join(f"{k}={v}" for k, v in best_params.items()),
        }
    else:
        ok1, m1 = ConditionParser.validate_syntax(entry_expr)
        if not ok1:
            raise HTTPException(status_code=400, detail=f"매수 수식 오류: {m1}")
        if exit_expr:
            ok2, m2 = ConditionParser.validate_syntax(exit_expr)
            if not ok2:
                raise HTTPException(status_code=400, detail=f"매도 수식 오류: {m2}")
        df_opt, best_info = optimize_custom_formula(
            entry_expr=entry_expr, exit_expr=exit_expr,
            data=data, cfg=cfg, start=start_date, end=end_date,
            asset_types=atypes, market_ok=market_ok, sort_by=sort_by,
            slot_indices=body.get("slot_indices"),
            sync_linked=bool(body.get("sync_linked", True)),
        )

    hm_raw = df_opt.attrs.pop("heatmap", None) if not df_opt.empty else None
    heatmap_payload = None
    if hm_raw and "pivot" in hm_raw:
        piv = hm_raw["pivot"]
        rpiv = hm_raw.get("ret_pivot")
        heatmap_payload = {
            "x_label": hm_raw.get("x_label", "변수 2"),
            "y_label": hm_raw.get("y_label", "변수 1"),
            "x": [str(c) for c in piv.columns.tolist()],
            "y": [str(r) for r in piv.index.tolist()],
            "z_sharpe": piv.where(pd.notna(piv), None).values.tolist(),
            "z_ret": rpiv.reindex_like(piv).where(pd.notna(rpiv.reindex_like(piv)), None).values.tolist() if rpiv is not None else None,
        }

    if not df_opt.empty:
        drop_cols = [c for c in ("_params", "_v0", "_v1") if c in df_opt.columns]
        rows_df = df_opt.drop(columns=drop_cols).head(30)
        rows = rows_df.to_dict(orient="records")
    else:
        rows = []

    return JSONResponse(_clean_json({
        "rows": rows,
        "best": best_info,
        "available_slots": best_info.get("available_slots", []),
        "heatmap": heatmap_payload,
    }))


@app.post("/api/scanner")
async def api_scanner(req: Request):
    """현재 수식을 대표/관심 여러 종목에 일괄 적용해 성과 및 오늘 매매 신호 스캔"""
    body = await req.json()
    basket = str(body.get("basket", "core10") or "core10")
    custom_tickers = [str(t).strip() for t in (body.get("tickers") or []) if str(t).strip()]
    scan_tickers = custom_tickers if (basket == "selected" and custom_tickers) else SCANNER_BASKETS.get(basket, SCANNER_BASKETS["core10"])

    start_date = str(body.get("start_date", "2018-01-01") or "2018-01-01")
    end_date = str(body.get("end_date", datetime.date.today().strftime("%Y-%m-%d")) or datetime.date.today().strftime("%Y-%m-%d"))
    entry_expr = str(body.get("entry_expr", "") or "").strip()
    exit_expr = str(body.get("exit_expr", "") or "").strip()
    market_filter = str(body.get("market_filter", "none") or "none")

    ok1, m1 = ConditionParser.validate_syntax(entry_expr) if entry_expr else (False, "매수 조건식이 비어 있습니다.")
    if not ok1:
        raise HTTPException(status_code=400, detail=f"매수 수식 오류: {m1}")
    if exit_expr:
        ok2, m2 = ConditionParser.validate_syntax(exit_expr)
        if not ok2:
            raise HTTPException(status_code=400, detail=f"매도 수식 오류: {m2}")

    universe = get_all_universe(include_full_krx=False)
    name_map = {u["code"]: u["name"] for u in universe}
    all_data = get_multiple_data(scan_tickers, start_date, end_date)
    if not all_data:
        raise HTTPException(status_code=400, detail="스캔 대상 종목 데이터를 불러올 수 없습니다.")

    cfg = _build_sim_config(body)
    cfg.max_positions = 1
    market_ok = _load_market_filter(market_filter, start_date, end_date)

    rows = []
    for t in scan_tickers:
        df = all_data.get(t)
        if df is None or df.empty:
            continue
        try:
            atypes = _detect_asset_types([t], universe)
            res = run_backtest({t: df}, entry_expr, exit_expr, cfg=cfg, start=start_date, end=end_date, asset_types=atypes, market_ok=market_ok)
            m, *_ = calculate_tearsheet(res)
            g = m.get("_grade", {})
            sig = analyze_live_signal(res, cfg)
            sd = inspect_formula_live_values(df, entry_expr, exit_expr, mtf_close_only=cfg.mtf_close_only, start_date=start_date, end_date=end_date)
            rows.append({
                "ticker": t,
                "name": name_map.get(t, t),
                "signal_status": sig.get("status", "WATCHING"),
                "signal_badge": sig.get("badge", "⚪ 관망"),
                "last_entry_date": sd.get("last_entry_date", "없음") if isinstance(sd, dict) else "없음",
                "grade": g.get("grade", "-"),
                "score": g.get("score", 0),
                "total_return": m.get("Total Return (%)", 0.0),
                "alpha": m.get("Alpha (%)", 0.0),
                "cagr": m.get("CAGR (%)", 0.0),
                "mdd": m.get("MDD (%)", 0.0),
                "sharpe": m.get("Sharpe Ratio", 0.0),
                "win_rate": m.get("Win Rate (%)", 0.0),
                "trades": m.get("Total Trades", 0),
            })
        except Exception:
            continue

    # 오늘 매수 신호 우선 + 종합점수 순 정렬
    rows.sort(key=lambda r: (1 if r["signal_status"] == "BUY_TODAY" else 0, r["score"], r["sharpe"]), reverse=True)
    return JSONResponse(_clean_json({"rows": rows, "scanned": len(rows)}))


CLOUD_CONFIG_PATH = os.path.join(BASE_DIR, "data", "cloud_config.json")
_CLOUD_LOCK = threading.RLock()


def _github_headers(gh_token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _load_cloud_config() -> Dict[str, Any]:
    with _CLOUD_LOCK:
        cfg: Dict[str, Any] = {
            "gh_token": os.environ.get("GH_TOKEN", os.environ.get("GITHUB_TOKEN", "")).strip(),
            "github_repo": os.environ.get("GITHUB_REPO", "backtest-pro").strip() or "backtest-pro",
            "github_branch": os.environ.get("GITHUB_BRANCH", "main").strip() or "main",
            "github_repo_url": "",
            "render_url": os.environ.get("RENDER_EXTERNAL_URL", "").strip().rstrip("/"),
            "cloud_url": "",
            "render_deploy_hook": os.environ.get("RENDER_DEPLOY_HOOK", "").strip(),
            "auto_sync_strategies": True,
            "last_sync_at": "",
            "last_deploy_at": "",
            "last_sync_note": "",
        }
        allowed_keys = set(cfg.keys())
        if os.path.exists(CLOUD_CONFIG_PATH):
            try:
                with open(CLOUD_CONFIG_PATH, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    for k, v in raw.items():
                        if k not in allowed_keys:
                            continue
                        if k == "cloud_url" and ".hf.space" in str(v or ""):
                            continue
                        if k == "last_sync_note" and ("HF " in str(v or "") or "Hugging Face" in str(v or "")):
                            continue
                        if v not in (None, "") or k in ("auto_sync_strategies",):
                            cfg[k] = v
            except Exception:
                pass
        if not cfg.get("gh_token"):
            cfg["gh_token"] = os.environ.get("GH_TOKEN", os.environ.get("GITHUB_TOKEN", "")).strip()
        r_url = str(cfg.get("render_url") or cfg.get("cloud_url") or "").strip().rstrip("/")
        cfg["render_url"] = r_url
        cfg["cloud_url"] = r_url
        return cfg


def _save_cloud_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    with _CLOUD_LOCK:
        cfg = _load_cloud_config()
        for k, v in updates.items():
            if k in ("gh_token", "render_deploy_hook") and not str(v or "").strip():
                continue
            cfg[k] = v
        if "render_url" in updates:
            r_url = str(updates.get("render_url") or "").strip().rstrip("/")
            cfg["render_url"] = r_url
            cfg["cloud_url"] = r_url
        elif "cloud_url" in updates:
            c_url = str(updates.get("cloud_url") or "").strip().rstrip("/")
            cfg["cloud_url"] = c_url
            cfg["render_url"] = c_url
        os.makedirs(os.path.dirname(CLOUD_CONFIG_PATH), exist_ok=True)
        with open(CLOUD_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return cfg


def _public_cloud_config(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    c = cfg or _load_cloud_config()
    tok = str(c.get("gh_token") or "").strip()
    preview = f"{tok[:6]}...{tok[-3:]}" if len(tok) > 10 else ("설정됨" if tok else "")
    r_url = str(c.get("render_url") or c.get("cloud_url") or "").strip().rstrip("/")
    return {
        "has_token": bool(tok),
        "token_preview": preview,
        "github_repo": str(c.get("github_repo") or "backtest-pro"),
        "github_repo_url": str(c.get("github_repo_url") or ""),
        "render_url": r_url,
        "cloud_url": r_url,
        "has_deploy_hook": bool(str(c.get("render_deploy_hook") or "").strip()),
        "auto_sync_strategies": bool(c.get("auto_sync_strategies", True)),
        "in_cloud": bool(os.environ.get("RENDER")),
        "last_sync_at": str(c.get("last_sync_at") or ""),
        "last_deploy_at": str(c.get("last_deploy_at") or ""),
        "last_sync_note": str(c.get("last_sync_note") or ""),
    }


def _active_sync_endpoints(cfg: Dict[str, Any]) -> List[str]:
    r_url = str(cfg.get("render_url") or cfg.get("cloud_url") or "").strip().rstrip("/")
    return [r_url] if r_url else []


def _read_strategy_files_map() -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    if not os.path.exists(STRATEGIES_DIR):
        return out
    for fname in sorted(os.listdir(STRATEGIES_DIR)):
        if not fname.endswith(".json"):
            continue
        p = os.path.join(STRATEGIES_DIR, fname)
        try:
            with open(p, "r", encoding="utf-8") as f:
                out[fname] = json.load(f)
        except Exception:
            pass
    return out


def _apply_strategy_sync_item(action: str, fname: str, payload: Optional[dict] = None) -> bool:
    safe = os.path.basename(str(fname or "").strip())
    if not safe.endswith(".json"):
        return False
    p = os.path.join(STRATEGIES_DIR, safe)
    if action == "delete":
        if os.path.exists(p):
            os.remove(p)
            return True
        return False
    if isinstance(payload, dict):
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    cur = json.load(f)
                if str(cur.get("saved_at", "")) >= str(payload.get("saved_at", "")):
                    return False
            except Exception:
                pass
        os.makedirs(STRATEGIES_DIR, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return True
    return False


def _upsert_github_strategy_file(
    gh_token: str, github_repo: str, branch: str, safe_name: str, raw_bytes: bytes, sha: Optional[str] = None
):
    import requests as _req

    hdrs = _github_headers(gh_token)
    api_url = f"https://api.github.com/repos/{github_repo}/contents/data/strategies/{safe_name}"
    if sha is None:
        r_get = _req.get(api_url, params={"ref": branch}, headers=hdrs, timeout=10)
        if r_get.status_code == 200:
            sha = (r_get.json() or {}).get("sha")
    body: Dict[str, Any] = {
        "message": f"Sync strategy: {safe_name}",
        "content": base64.b64encode(raw_bytes).decode("ascii"),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha
    _req.put(api_url, headers=hdrs, json=body, timeout=12)


def _delete_github_strategy_file(gh_token: str, github_repo: str, branch: str, safe_name: str):
    import requests as _req

    hdrs = _github_headers(gh_token)
    api_url = f"https://api.github.com/repos/{github_repo}/contents/data/strategies/{safe_name}"
    r_get = _req.get(api_url, params={"ref": branch}, headers=hdrs, timeout=10)
    if r_get.status_code == 200:
        sha = (r_get.json() or {}).get("sha")
        if sha:
            _req.delete(
                api_url,
                headers=hdrs,
                json={"message": f"Delete strategy: {safe_name}", "sha": sha, "branch": branch},
                timeout=12,
            )


def _auto_sync_strategy_bg(action: str, fname: str, payload: Optional[dict] = None):
    cfg = _load_cloud_config()
    if not cfg.get("auto_sync_strategies", True):
        return
    gh_token = str(cfg.get("gh_token") or "").strip()
    github_repo = str(cfg.get("github_repo") or "").strip()
    branch = str(cfg.get("github_branch") or "main").strip() or "main"
    safe = os.path.basename(fname)

    # 1) 실행 중인 Render 백엔드 URL로 즉시 전파
    if not os.environ.get("RENDER"):
        for ep in _active_sync_endpoints(cfg):
            try:
                import requests as _req
                headers = {"X-Cloud-Sync-Key": gh_token} if gh_token else {}
                _req.post(
                    f"{ep}/api/sync/strategies",
                    json={"action": action, "filename": safe, "payload": payload},
                    headers=headers,
                    timeout=8,
                )
            except Exception:
                pass

    # 2) GitHub 저장소 data/strategies 폴더에 영구 커밋 (Render 슬립·재배포 시 유실 방지)
    if gh_token and "/" in github_repo:
        try:
            if action == "delete":
                _delete_github_strategy_file(gh_token, github_repo, branch, safe)
            else:
                local_path = os.path.join(STRATEGIES_DIR, safe)
                if os.path.exists(local_path):
                    with open(local_path, "rb") as f:
                        _upsert_github_strategy_file(gh_token, github_repo, branch, safe, f.read())
            _save_cloud_config({
                "last_sync_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "last_sync_note": f"자동 동기화 완료 ({safe})",
            })
        except Exception:
            pass


def _sync_strategies_bidirectional() -> Dict[str, Any]:
    cfg = _load_cloud_config()
    gh_token = str(cfg.get("gh_token") or "").strip()
    github_repo = str(cfg.get("github_repo") or "").strip()
    branch = str(cfg.get("github_branch") or "main").strip() or "main"
    pulled = 0
    pushed = 0

    # A) 라이브 Render 서버와 양방향 동기화
    if not os.environ.get("RENDER"):
        for ep in _active_sync_endpoints(cfg):
            try:
                import requests as _req
                headers = {"X-Cloud-Sync-Key": gh_token} if gh_token else {}
                r = _req.get(f"{ep}/api/sync/strategies", headers=headers, timeout=10)
                if r.status_code == 200:
                    remote_map = (r.json() or {}).get("strategies") or {}
                    for fn, item in remote_map.items():
                        if _apply_strategy_sync_item("upsert", fn, item):
                            pulled += 1
                    to_push = {}
                    local_after = _read_strategy_files_map()
                    for fn, item in local_after.items():
                        r_item = remote_map.get(fn)
                        if r_item is None or str(item.get("saved_at", "")) > str(r_item.get("saved_at", "")):
                            to_push[fn] = item
                    if to_push:
                        pr = _req.post(
                            f"{ep}/api/sync/strategies",
                            json={"strategies": to_push},
                            headers=headers,
                            timeout=12,
                        )
                        if pr.status_code == 200:
                            pushed += len(to_push)
            except Exception:
                pass

    # B) GitHub 저장소 data/strategies 폴더와 양방향 동기화
    if gh_token and "/" in github_repo:
        try:
            import requests as _req
            hdrs = _github_headers(gh_token)
            dir_url = f"https://api.github.com/repos/{github_repo}/contents/data/strategies"
            r_dir = _req.get(dir_url, params={"ref": branch}, headers=hdrs, timeout=10)
            remote_entries = r_dir.json() if (r_dir.status_code == 200 and isinstance(r_dir.json(), list)) else []
            remote_shas: Dict[str, str] = {}
            for entry in remote_entries:
                name = str(entry.get("name") or "")
                if entry.get("type") != "file" or not name.endswith(".json"):
                    continue
                if entry.get("sha"):
                    remote_shas[name] = str(entry["sha"])
                try:
                    r_f = _req.get(str(entry["url"]), headers=hdrs, timeout=10)
                    if r_f.status_code == 200:
                        raw_b64 = (r_f.json() or {}).get("content", "")
                        item = json.loads(base64.b64decode(raw_b64).decode("utf-8"))
                        if _apply_strategy_sync_item("upsert", name, item):
                            pulled += 1
                except Exception:
                    pass
            local_after = _read_strategy_files_map()
            for base in local_after.keys():
                if base not in remote_shas:
                    lpath = os.path.join(STRATEGIES_DIR, base)
                    try:
                        with open(lpath, "rb") as f:
                            _upsert_github_strategy_file(gh_token, github_repo, branch, base, f.read(), sha="")
                        pushed += 1
                    except Exception:
                        pass
        except Exception:
            pass

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    note = f"동기화 완료 (가져옴 {pulled}건 · 올림 {pushed}건)"
    _save_cloud_config({"last_sync_at": now_str, "last_sync_note": note})
    return {"pulled": pulled, "pushed": pushed, "last_sync_at": now_str, "note": note}


def _upload_project_to_github(gh_token: str, repo_name: str = "backtest-pro") -> Dict[str, Any]:
    """Git CLI 없이 순수 GitHub REST API로 전체 앱 소스를 GitHub 저장소에 1클릭 푸시한다."""
    import requests as _req

    tok = str(gh_token or "").strip()
    if not tok:
        raise ValueError("GitHub Personal Access Token이 필요합니다.")
    clean_repo = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(repo_name or "backtest-pro")).strip("-") or "backtest-pro"
    hdrs = _github_headers(tok)

    r_me = _req.get("https://api.github.com/user", headers=hdrs, timeout=15)
    if r_me.status_code != 200:
        raise ValueError(f"GitHub 토큰 인증 실패 ({r_me.status_code}): {r_me.text[:160]}")
    username = r_me.json().get("login")
    if not username:
        raise ValueError("GitHub 사용자 이름을 확인할 수 없습니다.")

    full_repo = f"{username}/{clean_repo}"
    r_repo = _req.get(f"https://api.github.com/repos/{full_repo}", headers=hdrs, timeout=15)
    if r_repo.status_code == 404:
        r_cr = _req.post(
            "https://api.github.com/user/repos",
            headers=hdrs,
            json={"name": clean_repo, "private": False, "auto_init": True, "description": "Backtest Pro Quant Workbench"},
            timeout=20,
        )
        if r_cr.status_code not in (200, 201):
            raise ValueError(f"GitHub 저장소 생성 실패 ({r_cr.status_code}): {r_cr.text[:200]}")
        default_branch = r_cr.json().get("default_branch", "main")
        time.sleep(1.2)
    elif r_repo.status_code == 200:
        default_branch = r_repo.json().get("default_branch", "main")
    else:
        raise ValueError(f"GitHub 저장소 조회 실패 ({r_repo.status_code}): {r_repo.text[:160]}")

    # 비어있는 저장소면 초기 파일 1개 커밋
    r_ref = _req.get(f"https://api.github.com/repos/{full_repo}/git/ref/heads/{default_branch}", headers=hdrs, timeout=15)
    if r_ref.status_code != 200:
        init_b64 = base64.b64encode(b"# Backtest Pro\n").decode("ascii")
        _req.put(
            f"https://api.github.com/repos/{full_repo}/contents/README.md",
            headers=hdrs,
            json={"message": "Initialize repo", "content": init_b64, "branch": default_branch},
            timeout=15,
        )
        r_ref = _req.get(f"https://api.github.com/repos/{full_repo}/git/ref/heads/{default_branch}", headers=hdrs, timeout=15)
    if r_ref.status_code != 200:
        raise ValueError(f"GitHub 브랜치 참조 실패 ({r_ref.status_code}): {r_ref.text[:160]}")

    base_commit_sha = r_ref.json()["object"]["sha"]
    r_commit = _req.get(f"https://api.github.com/repos/{full_repo}/git/commits/{base_commit_sha}", headers=hdrs, timeout=15)
    base_tree_sha = r_commit.json()["tree"]["sha"]

    skip_dirs = {".bin", "__pycache__", ".git", ".pytest_cache", "cache"}
    tree_items = []
    file_count = 0
    for root, dirs, files in os.walk(BASE_DIR):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        rel_dir = os.path.relpath(root, BASE_DIR).replace("\\", "/")
        if rel_dir.startswith("data/cache"):
            continue
        for fn in files:
            if fn.endswith((".pyc", ".log", ".bat")) or fn == "cloud_config.json":
                continue
            full_path = os.path.join(root, fn)
            rel_path = fn if rel_dir == "." else f"{rel_dir}/{fn}"
            if rel_path == "data/cloud_config.json":
                continue
            with open(full_path, "rb") as f:
                raw_bytes = f.read()
            b64 = base64.b64encode(raw_bytes).decode("ascii")
            r_blob = _req.post(
                f"https://api.github.com/repos/{full_repo}/git/blobs",
                headers=hdrs,
                json={"content": b64, "encoding": "base64"},
                timeout=20,
            )
            if r_blob.status_code not in (200, 201):
                raise ValueError(f"GitHub 파일 업로드 실패 ({rel_path}): {r_blob.status_code}")
            tree_items.append({
                "path": rel_path,
                "mode": "100644",
                "type": "blob",
                "sha": r_blob.json()["sha"],
            })
            file_count += 1

    r_tree = _req.post(
        f"https://api.github.com/repos/{full_repo}/git/trees",
        headers=hdrs,
        json={"base_tree": base_tree_sha, "tree": tree_items},
        timeout=25,
    )
    if r_tree.status_code not in (200, 201):
        raise ValueError(f"GitHub 트리 생성 실패 ({r_tree.status_code}): {r_tree.text[:160]}")

    r_new_commit = _req.post(
        f"https://api.github.com/repos/{full_repo}/git/commits",
        headers=hdrs,
        json={
            "message": f"Sync Backtest Pro ({datetime.datetime.now().strftime('%Y-%m-%d %H:%M')})",
            "tree": r_tree.json()["sha"],
            "parents": [base_commit_sha],
        },
        timeout=20,
    )
    if r_new_commit.status_code not in (200, 201):
        raise ValueError(f"GitHub 커밋 실패 ({r_new_commit.status_code}): {r_new_commit.text[:160]}")

    r_upd = _req.patch(
        f"https://api.github.com/repos/{full_repo}/git/refs/heads/{default_branch}",
        headers=hdrs,
        json={"sha": r_new_commit.json()["sha"], "force": True},
        timeout=15,
    )
    if r_upd.status_code not in (200, 201):
        raise ValueError(f"GitHub 브랜치 업데이트 실패 ({r_upd.status_code}): {r_upd.text[:160]}")

    repo_url = f"https://github.com/{full_repo}"
    render_deploy_url = f"https://render.com/deploy?repo={repo_url}"
    return {
        "repo": full_repo,
        "default_branch": default_branch,
        "repo_url": repo_url,
        "render_deploy_url": render_deploy_url,
        "files_uploaded": file_count,
    }


@app.get("/api/strategies")
def api_get_strategies():
    return JSONResponse(_clean_json({"strategies": _list_saved_strategies()}))


@app.post("/api/strategies")
async def api_save_strategy(req: Request):
    body = await req.json()
    name = str(body.get("name", "") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="전략 이름을 입력해주세요.")
    fname = _safe_filename(name) + ".json"
    path = os.path.join(STRATEGIES_DIR, fname)
    payload = {
        "name": name,
        "mode": body.get("mode", "strategy"),
        "tickers": body.get("tickers", []),
        "entry_expr": body.get("entry_expr", ""),
        "exit_expr": body.get("exit_expr", ""),
        "strategy_id": body.get("strategy_id", "custom"),
        "start_date": body.get("start_date", ""),
        "stop_loss_pct": body.get("stop_loss_pct", 0),
        "take_profit_pct": body.get("take_profit_pct", 0),
        "trailing_stop_pct": body.get("trailing_stop_pct", 0),
        "saved_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    threading.Thread(target=_auto_sync_strategy_bg, args=("upsert", fname, payload), daemon=True).start()
    return JSONResponse(_clean_json({"ok": True, "filename": fname, "strategies": _list_saved_strategies()}))


@app.delete("/api/strategies/{filename}")
def api_delete_strategy(filename: str):
    safe = os.path.basename(filename)
    path = os.path.join(STRATEGIES_DIR, safe)
    if os.path.exists(path):
        os.remove(path)
    threading.Thread(target=_auto_sync_strategy_bg, args=("delete", safe, None), daemon=True).start()
    return JSONResponse(_clean_json({"ok": True, "strategies": _list_saved_strategies()}))


_SERVER_PORT = int(os.environ.get("PORT", 8000))
_ACCESS_PIN = os.environ.get("BACKTEST_PIN", "").strip()
_TUNNEL_LOCK = threading.RLock()
_TUNNEL_STATE: Dict[str, Any] = {
    "status": "stopped",
    "public_url": None,
    "error": "",
    "proc": None,
    "provider": None,
    "registered": False,
}


@app.middleware("http")
async def _pin_auth_middleware(req: Request, call_next):
    if _ACCESS_PIN and req.url.path != "/manifest.json":
        sync_hdr = req.headers.get("x-cloud-sync-key", "").strip()
        saved_tok = str(_load_cloud_config().get("gh_token") or "").strip()
        if req.url.path == "/api/sync/strategies" and sync_hdr and saved_tok and hmac.compare_digest(sync_hdr, saved_tok):
            return await call_next(req)
        auth = req.headers.get("authorization", "")
        ok = False
        if auth.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8", errors="ignore")
                pw = decoded.split(":", 1)[1] if ":" in decoded else decoded
                ok = hmac.compare_digest(pw, _ACCESS_PIN)
            except Exception:
                ok = False
        if not ok:
            return JSONResponse(
                {"detail": "PIN 인증이 필요합니다."},
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Backtest Pro Access"'},
            )
    res = await call_next(req)
    if req.url.path.startswith("/static/"):
        res.headers["Cache-Control"] = "no-cache"
    return res


def _get_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _ensure_cloudflared() -> str:
    sys_bin = shutil.which("cloudflared")
    if sys_bin:
        return sys_bin
    bin_dir = os.path.join(BASE_DIR, ".bin")
    os.makedirs(bin_dir, exist_ok=True)
    exe_name = "cloudflared.exe" if sys.platform.startswith("win") else "cloudflared"
    local_bin = os.path.join(bin_dir, exe_name)
    if os.path.exists(local_bin) and os.path.getsize(local_bin) > 1_000_000:
        return local_bin
    if sys.platform.startswith("win"):
        url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    else:
        url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
    tmp_bin = local_bin + ".part"
    urllib.request.urlretrieve(url, tmp_bin)
    os.replace(tmp_bin, local_bin)
    if not sys.platform.startswith("win"):
        os.chmod(local_bin, 0o755)
    return local_bin


def _drain_tunnel_pipe(proc: subprocess.Popen, pattern: str):
    try:
        if not proc.stdout:
            return
        for line in proc.stdout:
            m = re.search(pattern, line)
            if m and not _TUNNEL_STATE.get("public_url"):
                _TUNNEL_STATE["public_url"] = m.group(0)
                if _TUNNEL_STATE.get("provider") != "cloudflared":
                    _TUNNEL_STATE["registered"] = True
                    _TUNNEL_STATE["status"] = "running"
            if "Registered tunnel connection" in line:
                _TUNNEL_STATE["registered"] = True
                if _TUNNEL_STATE.get("public_url"):
                    _TUNNEL_STATE["status"] = "running"
    except Exception:
        pass


def _stop_tunnel():
    with _TUNNEL_LOCK:
        proc = _TUNNEL_STATE.get("proc")
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        _TUNNEL_STATE["proc"] = None
        _TUNNEL_STATE["public_url"] = None
        _TUNNEL_STATE["status"] = "stopped"
        _TUNNEL_STATE["error"] = ""
        _TUNNEL_STATE["provider"] = None
        _TUNNEL_STATE["registered"] = False


atexit.register(_stop_tunnel)


def _start_public_tunnel(port: int) -> Dict[str, Any]:
    with _TUNNEL_LOCK:
        existing = _TUNNEL_STATE.get("proc")
        if existing is not None and existing.poll() is None and _TUNNEL_STATE.get("public_url"):
            return dict(_TUNNEL_STATE)

        _TUNNEL_STATE["status"] = "starting"
        _TUNNEL_STATE["public_url"] = None
        _TUNNEL_STATE["error"] = ""
        _TUNNEL_STATE["registered"] = False

        # 1차: Cloudflare Quick Tunnel
        cf_err = ""
        try:
            cf_bin = _ensure_cloudflared()
            proc = subprocess.Popen(
                [cf_bin, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            _TUNNEL_STATE["proc"] = proc
            _TUNNEL_STATE["provider"] = "cloudflared"
            t = threading.Thread(
                target=_drain_tunnel_pipe,
                args=(proc, r"https://[a-zA-Z0-9-]+\.trycloudflare\.com"),
                daemon=True,
            )
            t.start()
            for _ in range(45):
                if _TUNNEL_STATE.get("public_url") and _TUNNEL_STATE.get("registered"):
                    _TUNNEL_STATE["status"] = "running"
                    time.sleep(2.5)
                    return dict(_TUNNEL_STATE)
                if proc.poll() is not None:
                    break
                time.sleep(0.35)
            cf_err = "Cloudflare 터널 응답 대기 시간 초과"
        except Exception as exc:
            cf_err = str(exc)

        # 실패 시 정리 후 2차 OpenSSH(localhost.run) fallback
        _stop_tunnel()
        ssh_bin = shutil.which("ssh")
        if ssh_bin:
            try:
                _TUNNEL_STATE["status"] = "starting"
                proc = subprocess.Popen(
                    [ssh_bin, "-o", "StrictHostKeyChecking=no", "-o", "ServerAliveInterval=30", "-R", f"80:127.0.0.1:{port}", "nokey@localhost.run"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                )
                _TUNNEL_STATE["proc"] = proc
                _TUNNEL_STATE["provider"] = "localhost.run"
                t = threading.Thread(
                    target=_drain_tunnel_pipe,
                    args=(proc, r"https://[a-zA-Z0-9-]+\.(?:lhr\.life|localhost\.run)"),
                    daemon=True,
                )
                t.start()
                for _ in range(30):
                    if _TUNNEL_STATE.get("public_url"):
                        return dict(_TUNNEL_STATE)
                    if proc.poll() is not None:
                        break
                    time.sleep(0.35)
            except Exception as ssh_exc:
                cf_err = f"{cf_err} | SSH fallback 실패: {ssh_exc}"

        _stop_tunnel()
        _TUNNEL_STATE["status"] = "error"
        _TUNNEL_STATE["error"] = cf_err or "외부 터널을 시작할 수 없습니다."
        return dict(_TUNNEL_STATE)


def _access_payload() -> Dict[str, Any]:
    lan_ip = _get_lan_ip()
    port = _SERVER_PORT
    proc = _TUNNEL_STATE.get("proc")
    if proc is not None and proc.poll() is not None and _TUNNEL_STATE.get("status") == "running":
        _TUNNEL_STATE["status"] = "stopped"
        _TUNNEL_STATE["public_url"] = None
    return {
        "local_url": f"http://127.0.0.1:{port}",
        "lan_ip": lan_ip,
        "lan_url": f"http://{lan_ip}:{port}",
        "tunnel_status": _TUNNEL_STATE["status"],
        "public_url": _TUNNEL_STATE["public_url"],
        "tunnel_error": _TUNNEL_STATE["error"],
        "provider": _TUNNEL_STATE.get("provider"),
        "pin_required": bool(_ACCESS_PIN),
    }


@app.get("/manifest.json")
def serve_manifest():
    manifest_path = os.path.join(STATIC_DIR, "manifest.json")
    if not os.path.exists(manifest_path):
        raise HTTPException(status_code=404, detail="manifest.json not found")
    return FileResponse(manifest_path, media_type="application/manifest+json")


@app.get("/api/access/info")
def api_access_info(req: Request):
    if not _is_local_request(req):
        return JSONResponse({
            "is_local_admin": False,
            "tunnel_status": "hidden",
            "pin_required": bool(_ACCESS_PIN),
        })
    return JSONResponse({**_access_payload(), "is_local_admin": True})


@app.post("/api/access/tunnel")
async def api_access_tunnel(req: Request):
    _require_local_admin(req)
    body = await req.json()
    action = str(body.get("action", "start") or "start").lower()
    if action == "stop":
        _stop_tunnel()
    else:
        _start_public_tunnel(_SERVER_PORT)
    return JSONResponse({**_access_payload(), "is_local_admin": True})


@app.get("/api/cloud/config")
def api_get_cloud_config(req: Request):
    _require_local_admin(req)
    return JSONResponse(_clean_json(_public_cloud_config()))


@app.post("/api/cloud/config")
async def api_save_cloud_config(req: Request):
    _require_local_admin(req)
    body = await req.json()
    updates: Dict[str, Any] = {}
    if "gh_token" in body and str(body.get("gh_token") or "").strip():
        updates["gh_token"] = str(body["gh_token"]).strip()
    if "github_repo" in body:
        updates["github_repo"] = str(body.get("github_repo") or "").strip() or "backtest-pro"
    if "render_url" in body:
        r_url = str(body.get("render_url") or "").strip().rstrip("/")
        updates["render_url"] = r_url
        updates["cloud_url"] = r_url
        if r_url:
            updates["last_sync_note"] = f"Render 24시간 서버 연결됨 ({r_url})"
    elif "cloud_url" in body:
        c_url = str(body.get("cloud_url") or "").strip().rstrip("/")
        updates["cloud_url"] = c_url
        updates["render_url"] = c_url
        if c_url:
            updates["last_sync_note"] = f"Render 24시간 서버 연결됨 ({c_url})"
    if "render_deploy_hook" in body and str(body.get("render_deploy_hook") or "").strip():
        updates["render_deploy_hook"] = str(body["render_deploy_hook"]).strip()
    if "auto_sync_strategies" in body:
        updates["auto_sync_strategies"] = bool(body.get("auto_sync_strategies"))
    cfg = _save_cloud_config(updates)
    return JSONResponse(_clean_json({"ok": True, **_public_cloud_config(cfg)}))


@app.get("/api/sync/strategies")
def api_sync_get_strategies():
    return JSONResponse(_clean_json({"strategies": _read_strategy_files_map()}))


@app.post("/api/sync/strategies")
async def api_sync_post_strategies(req: Request):
    body = await req.json()
    changed = 0
    if isinstance(body.get("strategies"), dict):
        for fn, item in body["strategies"].items():
            if _apply_strategy_sync_item("upsert", fn, item):
                changed += 1
    elif body.get("filename"):
        act = str(body.get("action", "upsert") or "upsert")
        if _apply_strategy_sync_item(act, str(body["filename"]), body.get("payload")):
            changed += 1
    if isinstance(body.get("deleted"), list):
        for fn in body["deleted"]:
            if _apply_strategy_sync_item("delete", str(fn), None):
                changed += 1
    return JSONResponse(_clean_json({"ok": True, "changed": changed, "strategies": _list_saved_strategies()}))


@app.post("/api/cloud/sync-now")
def api_cloud_sync_now(req: Request):
    _require_local_admin(req)
    res = _sync_strategies_bidirectional()
    return JSONResponse(_clean_json({
        "ok": True,
        **res,
        "strategies": _list_saved_strategies(),
        "cloud": _public_cloud_config(),
    }))


@app.post("/api/cloud/github-deploy")
async def api_cloud_github_deploy(req: Request):
    _require_local_admin(req)
    body = await req.json()
    saved = _load_cloud_config()
    gh_token = str(body.get("gh_token") or saved.get("gh_token") or "").strip()
    repo_name = str(body.get("repo_name") or saved.get("github_repo") or "backtest-pro").strip() or "backtest-pro"
    if "/" in repo_name:
        repo_name = repo_name.split("/", 1)[1].strip() or "backtest-pro"
    if not gh_token:
        raise HTTPException(
            status_code=400,
            detail="GitHub Personal Access Token(ghp_... 또는 github_pat_...)을 입력해주세요.",
        )
    try:
        res = _upload_project_to_github(gh_token, repo_name)
        deploy_hook = str(body.get("render_deploy_hook") or saved.get("render_deploy_hook") or "").strip()
        hook_triggered = False
        if deploy_hook.startswith("https://api.render.com/deploy/"):
            try:
                with urllib.request.urlopen(deploy_hook, timeout=15) as r_hook:
                    hook_triggered = 200 <= getattr(r_hook, "status", 200) < 300
            except Exception:
                hook_triggered = False
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        note = (
            f"GitHub 업로드 및 Render 자동 재배포 호출 완료 ({res['repo']}, {res['files_uploaded']}개 파일)"
            if hook_triggered
            else f"GitHub 업로드 완료 ({res['repo']}, {res['files_uploaded']}개 파일)"
        )
        updates_cfg: Dict[str, Any] = {
            "gh_token": gh_token,
            "github_repo": res["repo"],
            "github_branch": res["default_branch"],
            "github_repo_url": res["repo_url"],
            "last_deploy_at": now_str,
            "last_sync_at": now_str,
            "last_sync_note": note,
        }
        if deploy_hook:
            updates_cfg["render_deploy_hook"] = deploy_hook
        cfg = _save_cloud_config(updates_cfg)
        return JSONResponse(_clean_json({
            "ok": True,
            "render_deploy_triggered": hook_triggered,
            **res,
            "cloud": _public_cloud_config(cfg),
        }))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"GitHub 업로드 실패: {exc}")


def _startup_cloud_sync_bg():
    time.sleep(2.5)
    try:
        cfg = _load_cloud_config()
        has_gh = bool(cfg.get("gh_token") and "/" in str(cfg.get("github_repo") or ""))
        if not os.environ.get("RENDER") and cfg.get("auto_sync_strategies", True) and (_active_sync_endpoints(cfg) or has_gh):
            _sync_strategies_bidirectional()
    except Exception:
        pass


def _open_browser_later(url: str):
    time.sleep(1.1)
    try:
        webbrowser.open(url)
    except Exception:
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest Pro Quant Workbench Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--share", action="store_true", help="Cloudflare 무료 HTTPS 외부 인터넷 접속 터널 자동 시작")
    parser.add_argument("--pin", default="", help="외부 접속 시 비밀번호(PIN) 잠금 설정")
    args = parser.parse_args()

    _SERVER_PORT = args.port
    if args.pin.strip():
        _ACCESS_PIN = args.pin.strip()

    local_url = f"http://127.0.0.1:{args.port}"
    lan_url = f"http://{_get_lan_ip()}:{args.port}"
    public_url = None
    if args.share:
        print("🌐 외부 HTTPS 접속 터널 생성 중 (Cloudflare Tunnel)...")
        st = _start_public_tunnel(args.port)
        public_url = st.get("public_url")
        if not public_url:
            print(f"⚠️ 터널 자동 시작 실패: {st.get('error')}")

    print("=======================================================")
    print("  BACKTEST PRO - QUANT WORKBENCH SERVER")
    print(f"  내 PC 접속      : {local_url}")
    print(f"  같은 Wi-Fi 접속 : {lan_url}")
    if public_url:
        print(f"  외부(5G/PC) HTTPS: {public_url}")
    if _ACCESS_PIN:
        print(f"  보안 PIN 잠금    : 활성화됨 (아이디 무관 / 비밀번호 입력)")
    print("=======================================================")
    threading.Thread(target=_startup_cloud_sync_bg, daemon=True).start()
    if not args.no_browser:
        threading.Thread(target=_open_browser_later, args=(local_url,), daemon=True).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
