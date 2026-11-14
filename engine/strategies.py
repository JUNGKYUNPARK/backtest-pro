"""
전략 라이브러리 — 초보자도 이해할 수 있는 설명 + 조절 가능한 파라미터
"""
from dataclasses import dataclass, field
from typing import Callable, List, Tuple


@dataclass
class Param:
    key: str
    label: str
    min: float
    max: float
    default: float
    step: float = 1
    help: str = ""
    grid: Tuple = ()   # 자동 최적화 탐색 후보


@dataclass
class Strategy:
    id: str
    name: str
    emoji: str
    category: str
    level: str
    summary: str
    how: str
    good_when: str
    bad_when: str
    params: List[Param]
    build: Callable[[dict], Tuple[str, str]]
    kind: str = 'signal'          # 'signal' | 'vbo'(변동성 돌파, 당일 매수→익일 시가 매도)

    @property
    def label(self):
        return f"{self.emoji} {self.name}"

    def defaults(self):
        return {p.key: p.default for p in self.params}

    def formulas(self, params=None):
        p = self.defaults()
        if params:
            p.update({k: v for k, v in params.items() if k in p})
        return self.build(p)


def _i(v):
    v = float(v)
    return int(v) if v == int(v) else v


STRATEGIES: List[Strategy] = [
    Strategy(
        id='buy_hold', name='그냥 사서 보유', emoji='🧺', category='장기투자', level='입문',
        summary='처음에 사서 끝까지 들고 있기. 모든 전략의 비교 기준이에요.',
        how='시작일에 전액 매수하고 아무것도 하지 않습니다.',
        good_when='꾸준히 오르는 우량 자산', bad_when='긴 하락장 (손실을 그대로 견뎌야 함)',
        params=[], build=lambda p: ('TRUE', 'FALSE'),
    ),
    Strategy(
        id='golden_cross', name='골든크로스', emoji='✨', category='추세추종', level='입문',
        summary='짧은 이동평균선이 긴 선을 뚫고 올라가면 사고, 내려가면 팔아요.',
        how='단기선이 장기선을 위로 돌파(골든크로스) → 매수, 아래로 돌파(데드크로스) → 매도.',
        good_when='한 방향으로 길게 가는 추세장', bad_when='오르락내리락 박스권 (잦은 손절)',
        params=[Param('short', '단기선 (일)', 3, 60, 20, 1, '최근 며칠 평균을 볼까요?', (5, 10, 20, 30, 40)),
                Param('long', '장기선 (일)', 20, 250, 60, 5, '단기선보다 길게', (60, 90, 120, 200))],
        build=lambda p: (f"CROSSUP(MA({_i(p['short'])}), MA({_i(p['long'])}))",
                         f"CROSSDOWN(MA({_i(p['short'])}), MA({_i(p['long'])}))"),
    ),
    Strategy(
        id='trend_ma', name='이동평균선 위에서만 보유', emoji='🛡️', category='추세추종', level='입문',
        summary='가격이 N일 평균보다 위면 보유, 아래로 내려가면 현금으로 피신.',
        how='종가 > N일 이동평균 → 매수, 종가 < N일 이동평균 → 매도. 큰 폭락을 피하는 대표 방법.',
        good_when='큰 하락장 회피 (2008, 2020, 2022)', bad_when='짧은 급락 후 V자 반등',
        params=[Param('n', '이동평균 기간 (일)', 10, 300, 200, 10, '200일선이 가장 유명해요', (50, 100, 120, 150, 200, 250))],
        build=lambda p: (f"CLOSE > MA({_i(p['n'])})", f"CLOSE < MA({_i(p['n'])})"),
    ),
    Strategy(
        id='monthly_trend', name='월말 10개월선 전략', emoji='📅', category='추세추종', level='입문',
        summary='한 달에 한 번만 확인! 월봉 종가가 10개월 평균 위면 보유.',
        how='매월 마지막 거래일에 월봉 종가 > 월봉 N개월 이동평균이면 보유, 아니면 매도. (메브 파버 전략)',
        good_when='바쁜 직장인, 장기 하락장 회피', bad_when='한 달 안에 급락 후 회복하는 경우',
        params=[Param('n', '월 이동평균 (개월)', 3, 24, 10, 1, '', (6, 8, 10, 12))],
        build=lambda p: (f"M_CLOSE > M_MA({_i(p['n'])})", f"M_CLOSE < M_MA({_i(p['n'])})"),
    ),
    Strategy(
        id='momentum', name='12개월 모멘텀', emoji='🚀', category='추세추종', level='입문',
        summary='지난 1년간 올랐으면 보유, 떨어졌으면 쉬기 (절대 모멘텀).',
        how='N일 수익률 > 0 → 매수, N일 수익률 < 0 → 매도. 오르는 자산은 계속 오르는 경향(모멘텀)을 이용해요.',
        good_when='강한 상승 추세', bad_when='추세가 자주 뒤집히는 장',
        params=[Param('n', '확인 기간 (거래일)', 20, 300, 252, 21, '252일 ≈ 1년, 126일 ≈ 6개월', (63, 126, 189, 252))],
        build=lambda p: (f"MOMENTUM({_i(p['n'])}) > 0", f"MOMENTUM({_i(p['n'])}) < 0"),
    ),
    Strategy(
        id='rsi', name='RSI 과매도 반등', emoji='🎯', category='역추세', level='입문',
        summary='너무 많이 떨어졌을 때(RSI 낮음) 사고, 많이 올랐을 때 팔아요.',
        how='RSI가 하한선 아래 → 매수, 상한선 위 → 매도. "공포에 사고 환희에 판다".',
        good_when='일정 범위를 오가는 박스권', bad_when='계속 떨어지는 하락 추세 (물타기 위험)',
        params=[Param('n', 'RSI 기간', 2, 30, 14, 1, '', (7, 10, 14, 21)),
                Param('lo', '매수 기준 (이하)', 5, 50, 30, 1, '낮을수록 더 많이 떨어졌을 때만 매수', (20, 25, 30, 35)),
                Param('hi', '매도 기준 (이상)', 50, 95, 70, 1, '', (60, 65, 70, 75, 80))],
        build=lambda p: (f"RSI({_i(p['n'])}) < {_i(p['lo'])}", f"RSI({_i(p['n'])}) > {_i(p['hi'])}"),
    ),
    Strategy(
        id='bollinger', name='볼린저밴드 반등', emoji='🎈', category='역추세', level='중급',
        summary='가격이 밴드 아래로 벗어나면 사고, 가운데선을 회복하면 팔아요.',
        how='종가 < 볼린저 하단 → 매수, 종가 > 볼린저 중심선 → 매도.',
        good_when='평균으로 되돌아오는 장', bad_when='밴드를 타고 계속 내려가는 폭락장',
        params=[Param('n', '기간 (일)', 10, 60, 20, 1, '', (10, 15, 20, 30)),
                Param('k', '밴드 폭 (표준편차)', 1.0, 3.5, 2.0, 0.1, '', (1.5, 2.0, 2.5))],
        build=lambda p: (f"CLOSE < BB_LOWER({_i(p['n'])}, {_i(p['k'])})", f"CLOSE > BB_MID({_i(p['n'])})"),
    ),
    Strategy(
        id='macd', name='MACD 교차', emoji='📊', category='추세추종', level='중급',
        summary='MACD선이 시그널선을 위로 뚫으면 사고, 아래로 뚫으면 팔아요.',
        how='MACD(빠른선-느린선)가 시그널선 상향 돌파 → 매수, 하향 돌파 → 매도.',
        good_when='추세 전환 포착', bad_when='횡보장 (신호가 자주 뒤집힘)',
        params=[Param('fast', '빠른선', 5, 20, 12, 1, '', (8, 12, 16)),
                Param('slow', '느린선', 15, 60, 26, 1, '', (21, 26, 34)),
                Param('sig', '시그널', 3, 20, 9, 1, '', (5, 9, 12))],
        build=lambda p: (
            f"CROSSUP(MACD({_i(p['fast'])},{_i(p['slow'])},{_i(p['sig'])}), MACD_SIGNAL({_i(p['fast'])},{_i(p['slow'])},{_i(p['sig'])}))",
            f"CROSSDOWN(MACD({_i(p['fast'])},{_i(p['slow'])},{_i(p['sig'])}), MACD_SIGNAL({_i(p['fast'])},{_i(p['slow'])},{_i(p['sig'])}))"),
    ),
    Strategy(
        id='donchian', name='신고가 돌파 (터틀)', emoji='🐢', category='돌파', level='중급',
        summary='최근 N일 최고가를 넘으면 사고, M일 최저가를 깨면 팔아요.',
        how='종가 > 직전 N일 최고가 → 매수, 종가 < 직전 M일 최저가 → 매도. 전설의 터틀 트레이딩.',
        good_when='큰 추세가 시작될 때', bad_when='돌파 후 바로 꺾이는 가짜 돌파가 많은 장',
        params=[Param('n', '매수: N일 신고가', 10, 120, 20, 5, '', (20, 40, 55, 80)),
                Param('m', '매도: M일 신저가', 5, 60, 10, 5, '', (10, 20, 30))],
        build=lambda p: (f"CLOSE > HIGHEST({_i(p['n'])})", f"CLOSE < LOWEST({_i(p['m'])})"),
    ),
    Strategy(
        id='vbo', name='변동성 돌파 (단타)', emoji='⚡', category='돌파', level='중급',
        summary='오늘 시가 + 어제 변동폭×K 를 넘으면 사고, 다음 날 아침에 팔아요.',
        how='목표가 = 시가 + (전일 고가-저가)×K. 장중 목표가 돌파 시 매수, 다음 날 시가 매도. 이동평균 필터로 하락장 회피.',
        good_when='변동성이 크고 상승하는 장', bad_when='갭 하락이 잦은 장, 거래비용이 큰 종목',
        params=[Param('k', 'K 값', 0.1, 1.0, 0.5, 0.05, '작을수록 자주 매수', (0.3, 0.4, 0.5, 0.6, 0.7)),
                Param('ma', '상승장 필터 (N일선 위에서만, 0=끄기)', 0, 120, 5, 1, '', (0, 3, 5, 10, 20))],
        build=lambda p: ('TRUE', 'TRUE'), kind='vbo',
    ),
    Strategy(
        id='dip_in_uptrend', name='상승 추세 속 눌림목', emoji='🪜', category='역추세', level='중급',
        summary='큰 흐름은 상승(장기선 위)인데 잠깐 많이 떨어졌을 때 사요.',
        how='종가 > N일선 그리고 RSI(2) < 하한 → 매수, 종가 > 5일선 → 매도. (래리 코너스 RSI-2)',
        good_when='꾸준한 상승장의 일시 조정', bad_when='추세가 꺾이는 시점',
        params=[Param('n', '추세 기준선 (일)', 50, 250, 200, 10, '', (100, 150, 200)),
                Param('lo', 'RSI(2) 매수 기준', 2, 30, 10, 1, '', (5, 10, 15, 20))],
        build=lambda p: (f"CLOSE > MA({_i(p['n'])}) AND RSI(2) < {_i(p['lo'])}", "CLOSE > MA(5)"),
    ),
    Strategy(
        id='disparity', name='이격도 매매', emoji='📏', category='역추세', level='입문',
        summary='평균선보다 많이 떨어지면 사고, 많이 오르면 팔아요.',
        how='종가가 N일선 대비 X% 이상 아래 → 매수, Y% 이상 위 → 매도.',
        good_when='평균 회귀가 잘 되는 대형주·지수', bad_when='추세적 하락',
        params=[Param('n', '기준선 (일)', 5, 120, 20, 5, '', (10, 20, 40, 60)),
                Param('down', '매수: 평균보다 X% 아래', 1, 30, 5, 1, '', (3, 5, 8, 10)),
                Param('up', '매도: 평균보다 Y% 위', 1, 30, 5, 1, '', (3, 5, 8, 10))],
        build=lambda p: (f"DISPARITY({_i(p['n'])}) < {_i(100 - p['down'])}", f"DISPARITY({_i(p['n'])}) > {_i(100 + p['up'])}"),
    ),
    Strategy(
        id='weekly_trend_pullback', name='주봉 추세 + 일봉 과매도', emoji='🔭', category='역추세', level='고급',
        summary='주봉으로 보면 상승 추세인데, 일봉 RSI가 과매도일 때 사요.',
        how='종가 > 주봉 N주선 그리고 RSI(14) < 하한 → 매수, RSI(14) > 상한 또는 종가 < 20일선 → 매도.',
        good_when='중장기 상승 속 단기 조정', bad_when='주봉 추세 붕괴',
        params=[Param('n', '주봉 이동평균 (주)', 5, 60, 20, 1, '', (10, 20, 30, 40)),
                Param('lo', 'RSI 매수 기준', 10, 50, 35, 1, '', (25, 30, 35, 40)),
                Param('hi', 'RSI 매도 기준', 50, 90, 70, 1, '', (60, 70, 80))],
        build=lambda p: (f"CLOSE > W_MA({_i(p['n'])}) AND RSI(14) < {_i(p['lo'])}",
                         f"RSI(14) > {_i(p['hi'])} OR CLOSE < MA(20)"),
    ),
]

STRATEGY_MAP = {s.id: s for s in STRATEGIES}

CATEGORY_ORDER = ['장기투자', '추세추종', '역추세', '돌파']
