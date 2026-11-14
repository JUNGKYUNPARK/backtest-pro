"""
종목 유니버스 & 원클릭 추천 바스켓
- 기본 내장 핵심 종목 ~90개(한국·미국·지수)는 네트워크 호출 없이 0.001초 로딩
- 필요 시 전체 상장종목(KRX + S&P500 + NASDAQ100)을 불러와 로컬 캐시에 저장
"""
import json
import os
import pandas as pd

CACHE_PATH = os.path.join(os.path.dirname(__file__), 'universe_cache.json')
_UNIVERSE_MEM_CACHE = None

CURATED_UNIVERSE = [
    # ─── 주요 지수 ───
    {'code': '^KS11', 'name': '코스피 지수 (KOSPI)', 'market': 'INDEX'},
    {'code': '^KQ11', 'name': '코스닥 지수 (KOSDAQ)', 'market': 'INDEX'},
    {'code': '^GSPC', 'name': 'S&P 500 지수', 'market': 'INDEX'},
    {'code': '^IXIC', 'name': '나스닥 종합 지수', 'market': 'INDEX'},
    {'code': '^DJI',  'name': '다우 존스 지수', 'market': 'INDEX'},
    {'code': '^SOX',  'name': '필라델피아 반도체', 'market': 'INDEX'},
    {'code': '^VIX',  'name': 'VIX 공포지수', 'market': 'INDEX'},

    # ─── 한국 대표 ETF ───
    {'code': '069500', 'name': 'KODEX 200', 'market': 'ETF'},
    {'code': '122630', 'name': 'KODEX 레버리지', 'market': 'ETF'},
    {'code': '114800', 'name': 'KODEX 인버스', 'market': 'ETF'},
    {'code': '252670', 'name': 'KODEX 200선물인버스2X', 'market': 'ETF'},
    {'code': '229200', 'name': 'KODEX 코스닥150', 'market': 'ETF'},
    {'code': '233740', 'name': 'KODEX 코스닥150레버리지', 'market': 'ETF'},
    {'code': '379800', 'name': 'KODEX 미국S&P500TR', 'market': 'ETF'},
    {'code': '379810', 'name': 'KODEX 미국나스닥100TR', 'market': 'ETF'},
    {'code': '360750', 'name': 'TIGER 미국S&P500', 'market': 'ETF'},
    {'code': '133690', 'name': 'TIGER 미국나스닥100', 'market': 'ETF'},
    {'code': '458730', 'name': 'TIGER 미국배당다우존스', 'market': 'ETF'},
    {'code': '091160', 'name': 'KODEX 반도체', 'market': 'ETF'},
    {'code': '305720', 'name': 'KODEX 2차전지산업', 'market': 'ETF'},
    {'code': '148070', 'name': 'KOSEF 국고채10년', 'market': 'ETF'},
    {'code': '305080', 'name': 'TIGER 미국채10년선물', 'market': 'ETF'},
    {'code': '261240', 'name': 'KODEX 미국달러선물', 'market': 'ETF'},
    {'code': '132030', 'name': 'KODEX 골드선물(H)', 'market': 'ETF'},
    {'code': '453810', 'name': 'KODEX 인도Nifty50', 'market': 'ETF'},

    # ─── 한국 대표 주식 ───
    {'code': '005930', 'name': '삼성전자', 'market': 'KOSPI'},
    {'code': '000660', 'name': 'SK하이닉스', 'market': 'KOSPI'},
    {'code': '373220', 'name': 'LG에너지솔루션', 'market': 'KOSPI'},
    {'code': '207940', 'name': '삼성바이오로직스', 'market': 'KOSPI'},
    {'code': '005380', 'name': '현대차', 'market': 'KOSPI'},
    {'code': '000270', 'name': '기아', 'market': 'KOSPI'},
    {'code': '068270', 'name': '셀트리온', 'market': 'KOSPI'},
    {'code': '105560', 'name': 'KB금융', 'market': 'KOSPI'},
    {'code': '055550', 'name': '신한지주', 'market': 'KOSPI'},
    {'code': '035420', 'name': 'NAVER', 'market': 'KOSPI'},
    {'code': '035720', 'name': '카카오', 'market': 'KOSPI'},
    {'code': '005490', 'name': 'POSCO홀딩스', 'market': 'KOSPI'},
    {'code': '012450', 'name': '한화에어로스페이스', 'market': 'KOSPI'},
    {'code': '009540', 'name': 'HD한국조선해양', 'market': 'KOSPI'},
    {'code': '329180', 'name': 'HD현대중공업', 'market': 'KOSPI'},
    {'code': '028260', 'name': '삼성물산', 'market': 'KOSPI'},
    {'code': '086790', 'name': '하나금융지주', 'market': 'KOSPI'},
    {'code': '138040', 'name': '메리츠금융지주', 'market': 'KOSPI'},
    {'code': '006400', 'name': '삼성SDI', 'market': 'KOSPI'},
    {'code': '051910', 'name': 'LG화학', 'market': 'KOSPI'},
    {'code': '032830', 'name': '삼성생명', 'market': 'KOSPI'},
    {'code': '003670', 'name': '포스코퓨처엠', 'market': 'KOSPI'},
    {'code': '015760', 'name': '한국전력', 'market': 'KOSPI'},
    {'code': '034020', 'name': '두산에너빌리티', 'market': 'KOSPI'},
    {'code': '042700', 'name': '한미반도체', 'market': 'KOSPI'},
    {'code': '247540', 'name': '에코프로비엠', 'market': 'KOSDAQ'},
    {'code': '086520', 'name': '에코프로', 'market': 'KOSDAQ'},
    {'code': '196170', 'name': '알테오젠', 'market': 'KOSDAQ'},

    # ─── 미국 대표 ETF ───
    {'code': 'SPY',  'name': 'SPDR S&P 500 ETF', 'market': 'US_ETF'},
    {'code': 'QQQ',  'name': 'Invesco QQQ Trust (나스닥100)', 'market': 'US_ETF'},
    {'code': 'VOO',  'name': 'Vanguard S&P 500 ETF', 'market': 'US_ETF'},
    {'code': 'VTI',  'name': 'Vanguard Total Stock Market', 'market': 'US_ETF'},
    {'code': 'DIA',  'name': 'SPDR Dow Jones Industrial', 'market': 'US_ETF'},
    {'code': 'IWM',  'name': 'iShares Russell 2000 (중소형주)', 'market': 'US_ETF'},
    {'code': 'SCHD', 'name': 'Schwab US Dividend Equity', 'market': 'US_ETF'},
    {'code': 'TLT',  'name': 'iShares 20+ Year Treasury (장기채)', 'market': 'US_ETF'},
    {'code': 'IEF',  'name': 'iShares 7-10 Year Treasury (중기채)', 'market': 'US_ETF'},
    {'code': 'SHY',  'name': 'iShares 1-3 Year Treasury (단기채)', 'market': 'US_ETF'},
    {'code': 'GLD',  'name': 'SPDR Gold Shares (금)', 'market': 'US_ETF'},
    {'code': 'SLV',  'name': 'iShares Silver Trust (은)', 'market': 'US_ETF'},
    {'code': 'DBC',  'name': 'Invesco DB Commodity (원자재)', 'market': 'US_ETF'},
    {'code': 'VNQ',  'name': 'Vanguard Real Estate (리츠)', 'market': 'US_ETF'},
    {'code': 'TQQQ', 'name': 'ProShares UltraPro QQQ (3배)', 'market': 'US_ETF'},
    {'code': 'SQQQ', 'name': 'ProShares UltraPro Short QQQ (-3배)', 'market': 'US_ETF'},
    {'code': 'SOXL', 'name': 'Direxion 반도체 Bull 3X', 'market': 'US_ETF'},
    {'code': 'SOXS', 'name': 'Direxion 반도체 Bear -3X', 'market': 'US_ETF'},
    {'code': 'UPRO', 'name': 'ProShares UltraPro S&P500 (3배)', 'market': 'US_ETF'},
    {'code': 'XLK',  'name': 'Technology Select Sector', 'market': 'US_ETF'},
    {'code': 'XLF',  'name': 'Financial Select Sector', 'market': 'US_ETF'},
    {'code': 'XLE',  'name': 'Energy Select Sector', 'market': 'US_ETF'},
    {'code': 'XLV',  'name': 'Health Care Select Sector', 'market': 'US_ETF'},

    # ─── 미국 대표 주식 ───
    {'code': 'AAPL',  'name': 'Apple (애플)', 'market': 'US_STOCK'},
    {'code': 'MSFT',  'name': 'Microsoft (마이크로소프트)', 'market': 'US_STOCK'},
    {'code': 'NVDA',  'name': 'NVIDIA (엔비디아)', 'market': 'US_STOCK'},
    {'code': 'GOOGL', 'name': 'Alphabet (구글)', 'market': 'US_STOCK'},
    {'code': 'AMZN',  'name': 'Amazon (아마존)', 'market': 'US_STOCK'},
    {'code': 'META',  'name': 'Meta Platforms (메타)', 'market': 'US_STOCK'},
    {'code': 'TSLA',  'name': 'Tesla (테슬라)', 'market': 'US_STOCK'},
    {'code': 'BRK-B', 'name': 'Berkshire Hathaway (버크셔)', 'market': 'US_STOCK'},
    {'code': 'AVGO',  'name': 'Broadcom (브로드컴)', 'market': 'US_STOCK'},
    {'code': 'LLY',   'name': 'Eli Lilly (일라이릴리)', 'market': 'US_STOCK'},
    {'code': 'JPM',   'name': 'JPMorgan Chase', 'market': 'US_STOCK'},
    {'code': 'AMD',   'name': 'AMD', 'market': 'US_STOCK'},
    {'code': 'NFLX',  'name': 'Netflix (넷플릭스)', 'market': 'US_STOCK'},
    {'code': 'COST',  'name': 'Costco (코스트코)', 'market': 'US_STOCK'},
]

QUICK_PICKS = [
    ("🇰🇷 삼성전자", ["005930"], "한국 대표 국민주"),
    ("📦 KODEX 200", ["069500"], "코스피 200 대표 ETF"),
    ("🇺🇸 S&P 500 (SPY)", ["SPY"], "미국 500대 우량기업 ETF"),
    ("🚀 나스닥 100 (QQQ)", ["QQQ"], "미국 기술주 대표 ETF"),
    ("🤖 미국 빅테크 4대장", ["AAPL", "MSFT", "NVDA", "GOOGL"], "애플·MS·엔비디아·구글 균등"),
    ("💾 한국 반도체 투톱", ["005930", "000660"], "삼성전자 + SK하이닉스"),
    ("🔥 미국 3배 레버리지", ["TQQQ", "SOXL"], "나스닥3배 + 반도체3배"),
    ("🛡️ 올웨더 4자산", ["SPY", "TLT", "GLD", "IEF"], "주식·장기채·금·중기채 분산"),
]


def _merge_items(*lists):
    seen, out = set(), []
    for lst in lists:
        for it in lst:
            c = str(it.get('code', '')).strip()
            if c and c not in seen:
                seen.add(c)
                out.append({'code': c, 'name': str(it.get('name', c)), 'market': str(it.get('market', 'KRX'))})
    return out


def load_full_universe():
    """전체 상장종목 (KRX + 한국 ETF + 미국 S&P500/NASDAQ100) 로드 후 캐시 저장"""
    import FinanceDataReader as fdr
    extra = []
    try:
        df_krx = fdr.StockListing('KRX')
        for _, row in df_krx.iterrows():
            code = row.get('Code')
            if pd.notnull(code):
                mkt = str(row.get('Market', 'KOSPI')).upper()
                extra.append({'code': str(code).zfill(6), 'name': str(row.get('Name', code)), 'market': mkt})
    except Exception:
        pass

    try:
        df_etf = fdr.StockListing('ETF/KR')
        for _, row in df_etf.iterrows():
            code = row.get('Symbol') or row.get('Code')
            if pd.notnull(code):
                extra.append({'code': str(code).zfill(6), 'name': str(row.get('Name', code)), 'market': 'ETF'})
    except Exception:
        pass

    for market_name in ('S&P500', 'NASDAQ'):
        try:
            df_us = fdr.StockListing(market_name)
            for _, row in df_us.head(550).iterrows():
                sym = row.get('Symbol')
                if pd.notnull(sym):
                    extra.append({'code': str(sym).replace('.', '-'), 'name': str(row.get('Name', sym)), 'market': 'US_STOCK'})
        except Exception:
            pass

    merged = _merge_items(CURATED_UNIVERSE, extra)
    try:
        with open(CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(merged, f, ensure_ascii=False)
    except Exception:
        pass
    return merged


def get_all_universe(include_full_krx: bool = False):
    """빠른 유니버스 반환 (캐시가 있으면 병합, 없으면 기본 내장 ~90종목 즉시 반환)"""
    global _UNIVERSE_MEM_CACHE
    if include_full_krx:
        _UNIVERSE_MEM_CACHE = load_full_universe()
        return _UNIVERSE_MEM_CACHE
    if _UNIVERSE_MEM_CACHE is not None:
        return _UNIVERSE_MEM_CACHE
    cached = []
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, 'r', encoding='utf-8') as f:
                cached = json.load(f)
        except Exception:
            cached = []
    _UNIVERSE_MEM_CACHE = _merge_items(CURATED_UNIVERSE, cached)
    return _UNIVERSE_MEM_CACHE


def format_universe_label(item):
    market_icons = {
        'KOSPI': '🇰🇷', 'KOSDAQ': '🇰🇷', 'KONEX': '🇰🇷', 'KRX': '🇰🇷',
        'ETF': '📦', 'ETN': '📋',
        'US_STOCK': '🇺🇸', 'US_ETF': '🇺🇸📦',
        'INDEX': '🌎',
    }
    icon = market_icons.get(item.get('market', ''), '📈')
    return f"{icon} {item['name']} ({item['code']})"
