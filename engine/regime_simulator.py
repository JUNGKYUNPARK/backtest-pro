"""
레짐 스위칭 및 포트폴리오 자산배분 시뮬레이터
- run_regime_switch: 기준 종목(시그널) 조건에 따라 T1(상승장) / T2(하락장·현금) 자동 전환
- run_allocation_backtest: 다자산 비중 리밸런싱 + 매월 적립식(DCA) 지원
- RegimeSwitchSimulator: 기존 인터페이스 하위 호환 래퍼
"""
import math
import numpy as np
import pandas as pd

from .parser import to_signal, uses_timeframe
from .fees import FeeModel
from .simulator import SimConfig, BacktestResult, TRADE_COLUMNS, _Engine, _period_end_mask


def _build_single_benchmark(bench_df, dates, initial_capital, fee: FeeModel, asset_type='KRX', name='그냥 보유'):
    s = bench_df['Close'].reindex(dates).ffill()
    valid = s.dropna()
    if valid.empty:
        return pd.Series(initial_capital, index=dates, name=name)
    first_idx = valid.index[0]
    base = float(valid.iloc[0])
    buy_cost = fee.slippage + fee.comm_rate(asset_type)
    out = pd.Series(initial_capital, index=dates, dtype=float)
    mask = dates >= first_idx
    out.loc[mask] = initial_capital * (1 - buy_cost) * (s.loc[mask] / base)
    out.name = name
    return out


def run_regime_switch(
    signal_df: pd.DataFrame,
    t1_df: pd.DataFrame,
    t2_df: pd.DataFrame,
    signal_code: str,
    t1_code: str,
    t2_code: str,
    entry_expr: str,
    cfg: SimConfig,
    start=None,
    end=None,
    eval_freq: str = 'daily',
    asset_types: dict = None,
) -> BacktestResult:
    """
    듀얼 레짐 스위칭 백테스트.
    - signal_df에서 entry_expr이 True면 T1 보유, False면 T2(없으면 현금) 보유
    - eval_freq: 'daily' | 'weekly' | 'monthly'
    """
    tradable = {}
    if t1_code and t1_df is not None and not t1_df.empty:
        tradable[t1_code] = t1_df
    if t2_code and t2_df is not None and not t2_df.empty and t2_code not in tradable:
        tradable[t2_code] = t2_df
    if not tradable:
        raise ValueError("상승장(T1) 또는 하락장(T2) 종목을 최소 1개 선택해주세요.")

    eng = _Engine(tradable, cfg, start, end, asset_types)
    n, dates = eng.n, eng.dates
    t1_idx = eng.tickers.index(t1_code) if (t1_code and t1_code in eng.tickers) else None
    t2_idx = eng.tickers.index(t2_code) if (t2_code and t2_code in eng.tickers) else None

    raw_sig = to_signal(signal_df, entry_expr)
    tf = None
    if eval_freq == 'monthly':
        tf = 'M'
    elif eval_freq == 'weekly':
        tf = 'W'
    elif cfg.mtf_close_only:
        tf = uses_timeframe(entry_expr)

    eval_mask_sig = _period_end_mask(signal_df.index, tf) if tf else pd.Series(True, index=signal_df.index)
    sig_arr = raw_sig.reindex(dates).ffill().fillna(False).to_numpy(dtype=bool)
    eval_arr = eval_mask_sig.reindex(dates).fillna(False).to_numpy(dtype=bool)

    next_open = cfg.execution == 'next_open'
    eq = np.zeros(n)
    cash_arr = np.zeros(n)
    hold_arr = np.zeros(n)
    npos = np.zeros(n, dtype=int)
    regime_arr = []

    current_regime = None   # None('CASH') | 'T1' | 'T2'
    pending_regime = None
    has_pending = False
    O, H, L, C = eng.O, eng.H, eng.L, eng.C

    def _target_idx(reg):
        if reg == 'T1':
            return t1_idx
        if reg == 'T2':
            return t2_idx
        return None

    def _switch_to(target_reg, i, use_open):
        nonlocal current_regime
        if target_reg == current_regime:
            return
        cur_j = _target_idx(current_regime)
        tgt_j = _target_idx(target_reg)
        if cur_j is not None and cur_j in eng.pos:
            px = O[i, cur_j] if use_open else C[i, cur_j]
            if not eng.valid(px):
                return
            eng.close_pos(cur_j, px, i, f"레짐 전환 → {target_reg or '현금'}")
        if tgt_j is not None:
            px = O[i, tgt_j] if use_open else C[i, tgt_j]
            if not eng.valid(px):
                current_regime = None
                return
            eng.open_pos(tgt_j, px, i, eng.cash)
        current_regime = target_reg

    for i in range(n):
        if next_open and has_pending:
            _switch_to(pending_regime, i, use_open=True)
            has_pending = False

        for j, p in eng.pos.items():
            if eng.valid(H[i, j]):
                p['peak'] = max(p['peak'], H[i, j])
                p['hi'] = max(p['hi'], H[i, j])
            if eng.valid(L[i, j]):
                p['lo'] = min(p['lo'], L[i, j])

        if eval_arr[i] or (i == 0 and tf is None):
            desired = ('T1' if t1_idx is not None else None) if sig_arr[i] else ('T2' if t2_idx is not None else None)
            if next_open:
                if desired != current_regime:
                    pending_regime = desired
                    has_pending = True
            else:
                _switch_to(desired, i, use_open=False)

        hv = 0.0
        for j, p in eng.pos.items():
            px = eng.Cff[i, j]
            hv += p['shares'] * (px if px == px else p['ref'])
        eq[i] = eng.cash + hv
        cash_arr[i] = eng.cash
        hold_arr[i] = hv
        npos[i] = len(eng.pos)
        regime_arr.append(current_regime or '현금')

    equity, trades = eng.finalize(eq, cash_arr, hold_arr, npos)
    bench_df = t1_df if (t1_df is not None and not t1_df.empty) else signal_df
    bench_code = t1_code if (t1_df is not None and not t1_df.empty) else signal_code
    bench_at = (asset_types or {}).get(bench_code, 'KRX')
    bench_name = f"{t1_code} 그냥 보유" if t1_code else "기준지수 보유"
    bench = _build_single_benchmark(bench_df, dates, cfg.initial_capital, cfg.fee, bench_at, bench_name)

    diag = {
        signal_code: dict(
            entry_signals=int(sig_arr.sum()),
            exit_signals=int((~sig_arr).sum()),
            days=len(dates),
        )
    }
    return BacktestResult(
        equity=equity,
        trades=trades,
        benchmark=bench,
        benchmark_name=bench_name,
        diagnostics=diag,
        entry_expr=entry_expr,
        exit_expr='레짐 조건 미충족 시 T2/현금 전환',
        price_data=eng.price_slices(),
        extra=dict(
            regime_series=pd.Series(regime_arr, index=dates, name='Regime'),
            signal_series=pd.Series(sig_arr, index=dates, name='Signal'),
            total_fees=eng.total_fees,
            kind='regime',
            signal_code=signal_code,
            t1_code=t1_code,
            t2_code=t2_code,
        ),
    )


def run_allocation_backtest(
    data: dict,
    weights: dict,
    cfg: SimConfig,
    start=None,
    end=None,
    rebalance_freq: str = 'monthly',
    monthly_contribution: float = 0.0,
    asset_types: dict = None,
) -> BacktestResult:
    """
    다자산 비중 리밸런싱 + 적립식(DCA) 백테스트.
    - weights: {ticker: 0.0~1.0} (합이 1보다 작으면 나머지는 현금 비중)
    - rebalance_freq: 'monthly' | 'quarterly' | 'yearly' | 'none'
    - monthly_contribution: 매월 첫 거래일에 추가 투입할 원금 (원)
    """
    valid_data = {t: df for t, df in data.items() if t in weights and weights[t] > 0}
    if not valid_data:
        raise ValueError("비중이 0%보다 큰 종목을 최소 1개 선택해주세요.")

    w_clean = {t: float(weights[t]) for t in valid_data}
    total_w = sum(w_clean.values())
    if total_w > 1.0:
        w_clean = {t: v / total_w for t, v in w_clean.items()}

    eng = _Engine(valid_data, cfg, start, end, asset_types)
    n, k, dates = eng.n, eng.k, eng.dates
    target_w = np.array([w_clean.get(t, 0.0) for t in eng.tickers], dtype=float)
    use_open = cfg.execution == 'next_open'
    O, H, L, C = eng.O, eng.H, eng.L, eng.C

    eq = np.zeros(n)
    cash_arr = np.zeros(n)
    hold_arr = np.zeros(n)
    npos = np.zeros(n, dtype=int)
    contrib_arr = np.zeros(n)
    contrib_arr[0] = cfg.initial_capital
    total_invested = float(cfg.initial_capital)

    def _is_rebal_day(i):
        if i == 0:
            return True
        prev, cur = dates[i - 1], dates[i]
        if rebalance_freq == 'monthly':
            return (cur.year, cur.month) != (prev.year, prev.month)
        if rebalance_freq == 'quarterly':
            return (cur.year, (cur.month - 1) // 3) != (prev.year, (prev.month - 1) // 3)
        if rebalance_freq == 'yearly':
            return cur.year != prev.year
        return False

    def _rebalance(i, px_row, reason):
        total_val = eng.cash + sum(
            eng.pos[j]['shares'] * (px_row[j] if eng.valid(px_row[j]) else eng.pos[j]['ref'])
            for j in eng.pos
        )
        # 1) 비중 초과분 매도
        for j in range(k):
            if j not in eng.pos or not eng.valid(px_row[j]):
                continue
            p = eng.pos[j]
            cur_val = p['shares'] * px_row[j]
            tgt_val = total_val * target_w[j]
            diff = tgt_val - cur_val
            if diff < -px_row[j]:
                sell_shares = min(p['shares'], int(abs(diff) / px_row[j]))
                if sell_shares <= 0:
                    continue
                if sell_shares >= p['shares']:
                    eng.close_pos(j, px_row[j], i, reason)
                else:
                    asset = eng.asset_types.get(eng.tickers[j], 'KRX')
                    fill, cost = eng.cfg.fee.sell(px_row[j], sell_shares, asset, dates[i].year)
                    proceeds = fill * sell_shares - cost
                    inv_part = p['invested'] * (sell_shares / p['shares'])
                    fee_part = p['fees'] * (sell_shares / p['shares'])
                    pnl = proceeds - inv_part
                    eng.cash += proceeds
                    slip_cost = sell_shares * (px_row[j] - fill)
                    eng.total_fees += cost + slip_cost
                    p['shares'] -= sell_shares
                    p['invested'] -= inv_part
                    p['fees'] -= fee_part
                    eng.trades.append(dict(
                        ticker=eng.tickers[j], entry_date=dates[p['i']], exit_date=dates[i],
                        entry_price=p['fill'], exit_price=fill, shares=sell_shares, invested=inv_part,
                        pnl=pnl, return_pct=pnl / inv_part * 100 if inv_part else 0.0,
                        hold_days=int((dates[i] - dates[p['i']]).days), reason=reason, is_open=False,
                        fees=fee_part + cost + slip_cost,
                        mae_pct=(p['lo'] / p['ref'] - 1) * 100, mfe_pct=(p['hi'] / p['ref'] - 1) * 100,
                    ))

        # 2) 부족분 매수
        for j in range(k):
            if target_w[j] <= 0 or not eng.valid(px_row[j]):
                continue
            cur_val = eng.pos[j]['shares'] * px_row[j] if j in eng.pos else 0.0
            tgt_val = total_val * target_w[j]
            needed = min(eng.cash, tgt_val - cur_val)
            if needed <= px_row[j]:
                continue
            asset = eng.asset_types.get(eng.tickers[j], 'KRX')
            fill, comm = eng.cfg.fee.buy_fill(px_row[j], asset)
            add_shares = math.floor(needed / (fill * (1 + comm))) if fill > 0 else 0
            if add_shares <= 0:
                continue
            cost = add_shares * fill
            fee_amt = cost * comm
            slip_cost = add_shares * (fill - px_row[j])
            eng.cash -= (cost + fee_amt)
            eng.total_fees += fee_amt + slip_cost
            if j in eng.pos:
                p = eng.pos[j]
                tot_sh = p['shares'] + add_shares
                p['fill'] = (p['fill'] * p['shares'] + fill * add_shares) / tot_sh
                p['shares'] = tot_sh
                p['invested'] += cost + fee_amt
                p['fees'] += fee_amt + slip_cost
            else:
                eng.pos[j] = dict(
                    shares=add_shares, fill=fill, ref=px_row[j], i=i, invested=cost + fee_amt,
                    peak=px_row[j], atr_stop=None, lo=px_row[j], hi=px_row[j],
                    fees=fee_amt + slip_cost,
                )

    for i in range(n):
        new_month = i > 0 and (dates[i].year, dates[i].month) != (dates[i - 1].year, dates[i - 1].month)
        if new_month and monthly_contribution > 0:
            eng.cash += monthly_contribution
            total_invested += monthly_contribution
            contrib_arr[i] = monthly_contribution

        px_row = O[i] if (use_open and i > 0) else C[i]
        if _is_rebal_day(i):
            _rebalance(i, px_row, '초기 매수' if i == 0 else '정기 리밸런싱')
        elif new_month and monthly_contribution > 0 and rebalance_freq == 'none':
            # 리밸런싱 미사용 적립식: 새로 들어온 적립금만 목표 비중대로 추가 매수
            for j in range(k):
                if target_w[j] <= 0 or not eng.valid(px_row[j]):
                    continue
                alloc = monthly_contribution * target_w[j]
                alloc = min(alloc, eng.cash)
                asset = eng.asset_types.get(eng.tickers[j], 'KRX')
                fill, comm = eng.cfg.fee.buy_fill(px_row[j], asset)
                add_shares = math.floor(alloc / (fill * (1 + comm))) if fill > 0 else 0
                if add_shares > 0:
                    cost = add_shares * fill
                    fee_amt = cost * comm
                    slip_cost = add_shares * (fill - px_row[j])
                    eng.cash -= (cost + fee_amt)
                    eng.total_fees += fee_amt + slip_cost
                    if j in eng.pos:
                        p = eng.pos[j]
                        tot_sh = p['shares'] + add_shares
                        p['fill'] = (p['fill'] * p['shares'] + fill * add_shares) / tot_sh
                        p['shares'] = tot_sh
                        p['invested'] += cost + fee_amt
                        p['fees'] += fee_amt + slip_cost
                    else:
                        eng.pos[j] = dict(
                            shares=add_shares, fill=fill, ref=px_row[j], i=i, invested=cost + fee_amt,
                            peak=px_row[j], atr_stop=None, lo=px_row[j], hi=px_row[j],
                            fees=fee_amt + slip_cost,
                        )

        for j, p in eng.pos.items():
            if eng.valid(H[i, j]):
                p['peak'] = max(p['peak'], H[i, j])
                p['hi'] = max(p['hi'], H[i, j])
            if eng.valid(L[i, j]):
                p['lo'] = min(p['lo'], L[i, j])

        hv = sum(
            p['shares'] * (eng.Cff[i, j] if eng.Cff[i, j] == eng.Cff[i, j] else p['ref'])
            for j, p in eng.pos.items()
        )
        eq[i] = eng.cash + hv
        cash_arr[i] = eng.cash
        hold_arr[i] = hv
        npos[i] = len(eng.pos)

    equity, trades = eng.finalize(eq, cash_arr, hold_arr, npos)
    bench, bname = eng.benchmark()
    contrib_series = pd.Series(contrib_arr, index=dates, name='Contribution')
    w_desc = " / ".join(f"{t} {w*100:.0f}%" for t, w in w_clean.items())
    return BacktestResult(
        equity=equity,
        trades=trades,
        benchmark=bench,
        benchmark_name=bname,
        diagnostics={t: dict(entry_signals=1, exit_signals=0, days=n) for t in eng.tickers},
        entry_expr=f"목표 비중: {w_desc}",
        exit_expr=f"리밸런싱: {rebalance_freq} · 월 적립: {int(monthly_contribution):,}원",
        price_data=eng.price_slices(),
        extra=dict(
            total_invested=total_invested,
            contributions=contrib_series,
            weights=w_clean,
            rebalance_freq=rebalance_freq,
            total_fees=eng.total_fees,
            kind='allocation',
        ),
    )


class RegimeSwitchSimulator:
    """기존 코드 호환용 래퍼"""

    def __init__(self, signal_df, t1_df, t2_df, signal_code, t1_code, t2_code,
                 entry_expr, initial_capital=10000000, fee_calculator=None,
                 t1_asset_type='KRX', t2_asset_type='KRX', sim_start_date=None,
                 period_close_only=None):
        self.signal_df = signal_df
        self.t1_df = t1_df
        self.t2_df = t2_df
        self.signal_code = signal_code
        self.t1_code = t1_code
        self.t2_code = t2_code
        self.entry_expr = entry_expr
        self.initial_capital = initial_capital
        self.fc = fee_calculator if isinstance(fee_calculator, FeeModel) else FeeModel()
        self.t1_type = t1_asset_type
        self.t2_type = t2_asset_type
        self.sim_start_date = sim_start_date
        self.period_close_only = period_close_only

    def run(self):
        from .tearsheet import calculate_tearsheet
        freq = 'monthly' if self.period_close_only == '1mo' else ('weekly' if self.period_close_only == '1wk' else 'daily')
        cfg = SimConfig(initial_capital=self.initial_capital, fee=self.fc)
        res = run_regime_switch(
            self.signal_df, self.t1_df, self.t2_df,
            self.signal_code, self.t1_code, self.t2_code,
            self.entry_expr, cfg, start=self.sim_start_date, eval_freq=freq,
            asset_types={self.t1_code: self.t1_type, self.t2_code: self.t2_type},
        )
        metrics, *_ = calculate_tearsheet(res)
        res.equity['Benchmark'] = res.benchmark
        res.equity['Regime'] = res.extra['regime_series']
        return res.equity, res.trades, metrics, res.extra['signal_series']
