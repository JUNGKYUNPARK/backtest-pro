"""
백테스트 엔진 (현실적인 체결 모델)

핵심 원칙
- 미래 참조 금지: 오늘 종가로 만든 신호는 '다음 날 시가'에 체결 (기본값)
- 손절/익절/추적손절은 장중 고가·저가로 판정, 갭 발생 시 시가 체결
- 연도별 실제 거래세 + 수수료 + 슬리피지 반영
- 정수 주식 수, 슬롯(최대 보유 종목 수) 기반 균등 비중
"""
from dataclasses import dataclass, field
import math
import numpy as np
import pandas as pd

from .parser import to_signal, evaluate_condition, uses_timeframe
from .fees import FeeModel
from .indicators import ATR, MA


@dataclass
class SimConfig:
    initial_capital: float = 10_000_000
    max_positions: int = 1
    execution: str = 'close'          # 'close' | 'next_open'
    stop_loss: float = 0.0            # 0.1 = -10%에서 손절
    take_profit: float = 0.0          # 0.2 = +20%에서 익절
    trailing_stop: float = 0.0        # 0.1 = 고점 대비 -10%
    atr_stop: float = 0.0             # ATR(14) × N 손절
    max_hold_days: int = 0            # N 거래일 후 강제 매도 (0=미사용)
    mtf_close_only: bool = True       # 주/월봉 수식은 주말/월말에만 매매
    rank_expr: str = ''               # 후보가 많을 때 우선순위 수식
    rank_desc: bool = True            # True: 값이 큰 종목 우선
    fee: FeeModel = field(default_factory=FeeModel)


@dataclass
class BacktestResult:
    equity: pd.DataFrame              # Equity, Cash, Holdings, Positions
    trades: pd.DataFrame
    benchmark: pd.Series
    benchmark_name: str
    diagnostics: dict
    entry_expr: str = ''
    exit_expr: str = ''
    price_data: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)


TRADE_COLUMNS = ['ticker', 'entry_date', 'exit_date', 'entry_price', 'exit_price', 'shares',
                 'invested', 'pnl', 'return_pct', 'hold_days', 'reason', 'is_open', 'fees', 'mae_pct', 'mfe_pct']


def _period_end_mask(index, tf):
    if tf is None or len(index) == 0:
        return pd.Series(True, index=index)
    key = index.to_period('M' if tf == 'M' else 'W-FRI')
    s = pd.Series(index, index=index)
    last = s.groupby(key).transform('last')
    return pd.Series(index == last.values, index=index)


class _Engine:
    def __init__(self, data, cfg: SimConfig, start, end, asset_types=None):
        self.cfg = cfg
        self.tickers = list(data.keys())
        self.data = data
        self.asset_types = asset_types or {}
        start = pd.Timestamp(start) if start is not None else None
        end = pd.Timestamp(end) if end is not None else None
        idx = pd.DatetimeIndex([])
        for df in data.values():
            idx = idx.union(df.index)
        if start is not None:
            idx = idx[idx >= start]
        if end is not None:
            idx = idx[idx <= end]
        self.dates = idx
        if len(idx) < 2:
            raise ValueError("선택한 기간에 거래일 데이터가 부족합니다. 기간을 넓혀주세요.")
        n, k = len(idx), len(self.tickers)
        self.n, self.k = n, k

        def arr(col):
            return np.column_stack([data[t][col].reindex(idx).to_numpy(dtype=float) for t in self.tickers])

        self.O, self.H, self.L, self.C = arr('Open'), arr('High'), arr('Low'), arr('Close')
        # 시가 누락(0/NaN) 데이터는 종가로 대체
        bad_o = ~np.isfinite(self.O) | (self.O <= 0)
        self.O = np.where(bad_o, self.C, self.O)
        self.H = np.where(~np.isfinite(self.H) | (self.H <= 0), np.fmax(self.O, self.C), self.H)
        self.L = np.where(~np.isfinite(self.L) | (self.L <= 0), np.fmin(self.O, self.C), self.L)
        self.Cff = pd.DataFrame(self.C).ffill().to_numpy()

        self.cash = float(cfg.initial_capital)
        self.pos = {}
        self.trades = []
        self.total_fees = 0.0

    # ───────────── 공통 매매 함수 ─────────────
    def valid(self, x):
        return x == x and x > 0

    def equity_at(self, i, use_open=False):
        v = self.cash
        for j, p in self.pos.items():
            px = self.O[i, j] if use_open else self.C[i, j]
            if not self.valid(px):
                px = self.Cff[i - 1, j] if i > 0 and self.valid(self.Cff[i - 1, j]) else p['ref']
            v += p['shares'] * px
        return v

    def open_pos(self, j, px, i, alloc, atr_val=np.nan):
        asset = self.asset_types.get(self.tickers[j], 'KRX')
        fill, comm = self.cfg.fee.buy_fill(px, asset)
        shares = math.floor(alloc / (fill * (1 + comm))) if fill > 0 else 0
        if shares <= 0:
            return False
        cost = shares * fill
        fee_amt = cost * comm
        self.cash -= cost + fee_amt
        self.total_fees += fee_amt + shares * (fill - px)
        atr_stop = px - self.cfg.atr_stop * atr_val if self.cfg.atr_stop and atr_val == atr_val else None
        self.pos[j] = dict(shares=shares, fill=fill, ref=px, i=i, invested=cost + fee_amt,
                           peak=px, atr_stop=atr_stop, lo=px, hi=px, fees=fee_amt + shares * (fill - px))
        return True

    def close_pos(self, j, px, i, reason, is_open=False):
        p = self.pos[j]
        asset = self.asset_types.get(self.tickers[j], 'KRX')
        fill, cost = self.cfg.fee.sell(px, p['shares'], asset, self.dates[i].year)
        proceeds = fill * p['shares'] - cost
        pnl = proceeds - p['invested']
        fees = p['fees'] + cost + p['shares'] * (px - fill)
        if not is_open:
            self.cash += proceeds
            self.total_fees += cost + p['shares'] * (px - fill)
            del self.pos[j]
        self.trades.append(dict(
            ticker=self.tickers[j], entry_date=self.dates[p['i']], exit_date=self.dates[i],
            entry_price=p['fill'], exit_price=fill, shares=p['shares'], invested=p['invested'],
            pnl=pnl, return_pct=pnl / p['invested'] * 100 if p['invested'] else 0.0,
            hold_days=int((self.dates[i] - self.dates[p['i']]).days), reason=reason, is_open=is_open,
            fees=fees, mae_pct=(p['lo'] / p['ref'] - 1) * 100, mfe_pct=(p['hi'] / p['ref'] - 1) * 100,
        ))

    def finalize(self, eq, cash_arr, hold_arr, npos_arr):
        last = self.n - 1
        for j in list(self.pos):
            px = self.C[last, j] if self.valid(self.C[last, j]) else self.Cff[last, j]
            self.close_pos(j, px, last, '보유 중', is_open=True)
        equity = pd.DataFrame({'Equity': eq, 'Cash': cash_arr, 'Holdings': hold_arr, 'Positions': npos_arr},
                              index=self.dates)
        trades = pd.DataFrame(self.trades, columns=TRADE_COLUMNS)
        return equity, trades

    def benchmark(self):
        """선택 종목 균등 매수 후 보유 (비용 반영)"""
        cap = self.cfg.initial_capital
        total = np.zeros(self.n)
        for j, t in enumerate(self.tickers):
            col = self.Cff[:, j]
            first = np.argmax(np.isfinite(self.C[:, j]) & (self.C[:, j] > 0))
            base = self.C[first, j]
            asset = self.asset_types.get(t, 'KRX')
            buy_cost = self.cfg.fee.slippage + self.cfg.fee.comm_rate(asset)
            part = np.full(self.n, cap / self.k)
            part[first:] = cap / self.k * (1 - buy_cost) * col[first:] / base
            total += part
        name = '그냥 보유' if self.k == 1 else f'{self.k}종목 균등 보유'
        return pd.Series(total, index=self.dates, name=name), name

    def price_slices(self):
        s, e = self.dates[0], self.dates[-1]
        return {t: df[(df.index >= s) & (df.index <= e)] for t, df in self.data.items()}


# ═══════════════════════════════════════════════════════════════
# 1) 신호 기반 백테스트
# ═══════════════════════════════════════════════════════════════

def run_backtest(data, entry_expr, exit_expr, cfg: SimConfig, start=None, end=None,
                 asset_types=None, market_ok: pd.Series = None, progress=None) -> BacktestResult:
    eng = _Engine(data, cfg, start, end, asset_types)
    n, k, dates = eng.n, eng.k, eng.dates
    tf = uses_timeframe(f"{entry_expr} {exit_expr}") if cfg.mtf_close_only else None

    EN = np.zeros((n, k), dtype=bool)
    EX = np.zeros((n, k), dtype=bool)
    RK = np.full((n, k), np.nan)
    ATRv = np.full((n, k), np.nan)
    diag = {}
    for j, t in enumerate(eng.tickers):
        df = data[t]
        en = to_signal(df, entry_expr)
        ex = to_signal(df, exit_expr) if exit_expr and str(exit_expr).strip() else pd.Series(False, index=df.index)
        if tf:
            m = _period_end_mask(df.index, tf)
            en, ex = en & m, ex & m
        EN[:, j] = en.reindex(dates).fillna(False).to_numpy(dtype=bool)
        EX[:, j] = ex.reindex(dates).fillna(False).to_numpy(dtype=bool)
        if cfg.rank_expr:
            try:
                rv = evaluate_condition(df, cfg.rank_expr)
                if isinstance(rv, pd.Series):
                    RK[:, j] = rv.reindex(dates).to_numpy(dtype=float)
            except Exception:
                pass
        if cfg.atr_stop:
            ATRv[:, j] = ATR(df, 14).reindex(dates).to_numpy(dtype=float)
        diag[t] = dict(entry_signals=int(EN[:, j].sum()), exit_signals=int(EX[:, j].sum()),
                       days=int(np.isfinite(eng.C[:, j]).sum()))

    mkt = np.ones(n, dtype=bool)
    if market_ok is not None:
        mkt = market_ok.reindex(dates).ffill().fillna(False).to_numpy(dtype=bool)

    next_open = cfg.execution == 'next_open'
    slots = max(1, int(cfg.max_positions))
    eq = np.zeros(n); cash_arr = np.zeros(n); hold_arr = np.zeros(n); npos = np.zeros(n, dtype=int)
    pending_buy, pending_sell = [], {}
    O, H, L, C = eng.O, eng.H, eng.L, eng.C

    for i in range(n):
        if progress and i % 250 == 0:
            progress(i / n)
        # ── 1. 시가 체결 (전날 신호) ──
        if next_open:
            if pending_sell:
                carry = {}
                for j, reason in pending_sell.items():
                    if j in eng.pos and eng.valid(O[i, j]):
                        eng.close_pos(j, O[i, j], i, reason)
                    elif j in eng.pos:
                        carry[j] = reason
                pending_sell = carry
            if pending_buy:
                slot_val = eng.equity_at(i, use_open=True) / slots
                for j in pending_buy:
                    if len(eng.pos) >= slots:
                        break
                    if j in eng.pos or not eng.valid(O[i, j]):
                        continue
                    atr_v = ATRv[i - 1, j] if i > 0 else np.nan
                    eng.open_pos(j, O[i, j], i, min(eng.cash, slot_val), atr_v)
                pending_buy = []

        # ── 2. 장중 손절/익절 ──
        if eng.pos and (cfg.stop_loss or cfg.take_profit or cfg.trailing_stop or cfg.atr_stop):
            for j in list(eng.pos):
                lo, hi, op = L[i, j], H[i, j], O[i, j]
                if not eng.valid(lo):
                    continue
                p = eng.pos[j]
                stops = []
                if cfg.stop_loss:
                    stops.append((p['ref'] * (1 - cfg.stop_loss), f"손절 -{cfg.stop_loss * 100:g}%"))
                if cfg.trailing_stop:
                    stops.append((p['peak'] * (1 - cfg.trailing_stop), f"추적손절 (고점 -{cfg.trailing_stop * 100:g}%)"))
                if p['atr_stop']:
                    stops.append((p['atr_stop'], f"ATR×{cfg.atr_stop:g} 손절"))
                exit_px, reason = None, None
                if stops:
                    lvl, reason = max(stops, key=lambda x: x[0])
                    if lo <= lvl:
                        exit_px = min(op, lvl)
                if exit_px is None and cfg.take_profit:
                    tp = p['ref'] * (1 + cfg.take_profit)
                    if hi >= tp:
                        exit_px, reason = max(op, tp), f"익절 +{cfg.take_profit * 100:g}%"
                if exit_px is not None:
                    p['lo'] = min(p['lo'], lo); p['hi'] = max(p['hi'], hi)
                    eng.close_pos(j, exit_px, i, reason)
                    pending_sell.pop(j, None)

        # MAE/MFE·고점 갱신
        for j, p in eng.pos.items():
            if eng.valid(H[i, j]):
                p['peak'] = max(p['peak'], H[i, j]); p['hi'] = max(p['hi'], H[i, j])
            if eng.valid(L[i, j]):
                p['lo'] = min(p['lo'], L[i, j])

        # ── 3. 종가 신호 판정 ──
        for j in list(eng.pos):
            if not eng.valid(C[i, j]):
                continue
            p = eng.pos[j]
            reason = None
            if EX[i, j]:
                reason = '매도 신호'
            elif cfg.max_hold_days and (i - p['i']) >= cfg.max_hold_days:
                reason = f'보유 {cfg.max_hold_days}일 경과'
            if reason:
                if next_open:
                    pending_sell[j] = reason
                else:
                    eng.close_pos(j, C[i, j], i, reason)

        if mkt[i]:
            cands = [j for j in range(k) if EN[i, j] and not EX[i, j] and j not in eng.pos
                     and j not in pending_sell and eng.valid(C[i, j])]
            if cands and cfg.rank_expr and len(cands) > 1:
                cands.sort(key=lambda j: (np.isnan(RK[i, j]), -RK[i, j] if cfg.rank_desc else RK[i, j]))
            if next_open:
                pending_buy = cands
            elif cands:
                slot_val = eng.equity_at(i) / slots
                for j in cands:
                    if len(eng.pos) >= slots:
                        break
                    eng.open_pos(j, C[i, j], i, min(eng.cash, slot_val), ATRv[i, j])
        else:
            pending_buy = []

        # ── 4. 평가 ──
        hv = 0.0
        for j, p in eng.pos.items():
            px = eng.Cff[i, j]
            hv += p['shares'] * (px if px == px else p['ref'])
        eq[i] = eng.cash + hv; cash_arr[i] = eng.cash; hold_arr[i] = hv; npos[i] = len(eng.pos)

    equity, trades = eng.finalize(eq, cash_arr, hold_arr, npos)
    bench, bname = eng.benchmark()
    return BacktestResult(equity=equity, trades=trades, benchmark=bench, benchmark_name=bname,
                          diagnostics=diag, entry_expr=entry_expr, exit_expr=exit_expr,
                          price_data=eng.price_slices(),
                          extra=dict(total_fees=eng.total_fees, timeframe=tf, kind='signal'))


# ═══════════════════════════════════════════════════════════════
# 2) 변동성 돌파 (당일 목표가 돌파 매수 → 다음 날 시가 매도)
# ═══════════════════════════════════════════════════════════════

def run_vbo_backtest(data, k_value, ma_filter, cfg: SimConfig, start=None, end=None,
                     asset_types=None, market_ok: pd.Series = None, progress=None) -> BacktestResult:
    eng = _Engine(data, cfg, start, end, asset_types)
    n, kk, dates = eng.n, eng.k, eng.dates
    TGT_RANGE = np.full((n, kk), np.nan)
    FILT = np.ones((n, kk), dtype=bool)
    diag = {}
    for j, t in enumerate(eng.tickers):
        df = data[t]
        rng = (df['High'] - df['Low']).shift(1)
        TGT_RANGE[:, j] = rng.reindex(dates).to_numpy(dtype=float)
        if ma_filter and int(ma_filter) > 0:
            f = (df['Close'].shift(1) > MA(df, int(ma_filter)).shift(1))
            FILT[:, j] = f.reindex(dates).fillna(False).to_numpy(dtype=bool)
        diag[t] = dict(entry_signals=0, exit_signals=0, days=int(np.isfinite(eng.C[:, j]).sum()))

    mkt = np.ones(n, dtype=bool)
    if market_ok is not None:
        # 전일까지 확정된 시장 상태 사용
        mkt = market_ok.reindex(dates).ffill().shift(1).fillna(False).to_numpy(dtype=bool)

    slots = max(1, int(cfg.max_positions))
    eq = np.zeros(n); cash_arr = np.zeros(n); hold_arr = np.zeros(n); npos = np.zeros(n, dtype=int)
    O, H, L, C = eng.O, eng.H, eng.L, eng.C
    for i in range(n):
        if progress and i % 250 == 0:
            progress(i / n)
        # 보유분 시가 매도
        for j in list(eng.pos):
            if eng.valid(O[i, j]):
                eng.close_pos(j, O[i, j], i, '다음 날 시가 매도')
        if mkt[i]:
            slot_val = eng.equity_at(i, use_open=True) / slots
            cands = []
            for j in range(kk):
                if j in eng.pos or not (eng.valid(O[i, j]) and eng.valid(H[i, j])) or not FILT[i, j]:
                    continue
                r = TGT_RANGE[i, j]
                if not (r == r) or r <= 0:
                    continue
                tgt = O[i, j] + k_value * r
                if H[i, j] >= tgt:
                    cands.append((tgt / O[i, j], j, tgt))
            cands.sort()
            for _, j, tgt in cands:
                if len(eng.pos) >= slots:
                    break
                diag[eng.tickers[j]]['entry_signals'] += 1
                if eng.open_pos(j, max(O[i, j], tgt), i, min(eng.cash, slot_val)):
                    p = eng.pos[j]
                    p['hi'] = max(p['hi'], H[i, j]); p['lo'] = min(p['lo'], L[i, j])
        hv = sum(p['shares'] * (eng.Cff[i, j] if eng.Cff[i, j] == eng.Cff[i, j] else p['ref'])
                 for j, p in eng.pos.items())
        eq[i] = eng.cash + hv; cash_arr[i] = eng.cash; hold_arr[i] = hv; npos[i] = len(eng.pos)

    equity, trades = eng.finalize(eq, cash_arr, hold_arr, npos)
    bench, bname = eng.benchmark()
    ma_txt = f" · 전일 종가 > MA({int(ma_filter)})" if ma_filter and int(ma_filter) > 0 else ""
    return BacktestResult(equity=equity, trades=trades, benchmark=bench, benchmark_name=bname,
                          diagnostics=diag, entry_expr=f"HIGH ≥ 시가 + 전일변동폭 × {k_value:g}{ma_txt}",
                          exit_expr="다음 날 시가", price_data=eng.price_slices(),
                          extra=dict(total_fees=eng.total_fees, kind='vbo'))
