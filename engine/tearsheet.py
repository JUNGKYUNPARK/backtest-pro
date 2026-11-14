"""
성과 분석(Tear Sheet) & AI 성적표 · 초보자 해설 엔진
- 적립식(DCA) 현금 유입을 보정한 시간가중수익률(TWR) 기반 샤프/소르티노/CAGR/드로다운 지원
- BacktestResult 및 기존 df_equity 호출 방식 모두 호환
"""
import numpy as np
import pandas as pd


METRIC_HELP = {
    'Total Return (%)': ('누적 수익률', '총 투입 원금 대비 최종 자산이 몇 % 불어났는지 보여줍니다.'),
    'CAGR (%)': ('연평균 복리 수익률', '매년 꾸준히 몇 %씩 복리로 자랐는지 나타냅니다. (예: 12%면 약 6년마다 2배)'),
    'MDD (%)': ('최대 낙폭 (MDD)', '가장 잘 나가던 고점 대비 최대로 떨어졌던 폭입니다. 숫자가 작을수록(-10% > -40%) 버티기 쉽습니다.'),
    'MDD Duration (days)': ('최장 하락 기간', '고점을 찍고 다시 그 고점을 회복할 때까지 걸린 최대 일수입니다.'),
    'Sharpe Ratio': ('샤프 지수', '감수한 변동성(위험) 대비 수익 효율입니다. 1.0 이상이면 우수, 1.5 이상이면 최상급입니다.'),
    'Sortino Ratio': ('소르티노 지수', '오를 때의 변동성은 빼고, 떨어지는 위험만 따져서 계산한 효율 점수입니다.'),
    'Calmar Ratio': ('칼마 비율', '연평균 수익률(CAGR) ÷ 최대 낙폭(|MDD|). 1.0 이상이면 낙폭 대비 수익이 좋습니다.'),
    'Volatility (%)': ('연간 변동성', '주가가 하루하루 위아래로 흔들리는 정도를 1년 단위로 환산한 값입니다.'),
    'Alpha (%)': ('초과 수익 (Alpha)', '그냥 사서 보유(벤치마크)했을 때보다 전략이 얼마나 더 벌었는지 차이입니다.'),
    'Beta': ('시장 민감도 (Beta)', '벤치마크가 1% 움직일 때 전략 자산이 몇 % 움직이는지 나타냅니다.'),
    'Win Rate (%)': ('승률', '전체 매매 중 수익으로 끝난 거래의 비율입니다. 추세추종은 40%대라도 크게 먹으면 성공합니다.'),
    'Profit Factor': ('손익비 (PF)', '총 벌어들인 금액 ÷ 총 잃은 금액. 1.5 이상이면 탄탄한 전략입니다.'),
    'Payoff Ratio': ('평균 익절/손절 배수', '평균 수익 거래(%) ÷ 평균 손실 거래(%). 승률이 낮아도 이 값이 크면 돈을 법니다.'),
    'Expectancy (%)': ('1회 매매 기대수익률', '한 번 사고팔 때마다 평균적으로 기대할 수 있는 수익률(%)입니다.'),
    'Exposure (%)': ('평균 주식 보유 비중', '전체 기간 중 현금이 아닌 주식에 투자된 비율입니다. 낮을수록 하락장 노출이 적습니다.'),
    'Time in Market (%)': ('시장 참여 비율', '전체 거래일 중 실제로 종목을 1주라도 보유하고 있던 날의 비율입니다.'),
    'Ulcer Index': ('궤양 지수', '하락의 깊이와 기간을 함께 반영한 스트레스 지표입니다. 0에 가까울수록 마음 편한 전략입니다.'),
    'Total Fees': ('누적 거래비용', '수수료·증권거래세·슬리피지로 빠져나간 총 금액입니다.'),
    'Avg MAE (%)': ('평균 장중 최대역행(MAE)', '매수한 뒤 보유하는 동안 겪은 평균 최대 손실폭입니다. 손절선 설정에 참고하세요.'),
    'Avg MFE (%)': ('평균 장중 최대순행(MFE)', '매수한 뒤 보유하는 동안 기록한 평균 최대 수익폭입니다. 익절선 설정에 참고하세요.'),
}


def _interp(val, xp, fp):
    return float(np.interp(val, xp, fp))


def grade_strategy(metrics: dict) -> dict:
    """5대 영역 점수(0~100) 및 S~D 종합 등급 + 쉬운 한국어 해설"""
    if not metrics:
        return dict(score=0, grade='D', color='#FF4D4F', headline='결과 데이터가 없습니다.',
                    radar={'수익성': 0, '안정성': 0, '효율성': 0, '시장대비': 0, '거래품질': 0},
                    strengths=[], warnings=[], tips=[])

    cagr = float(metrics.get('CAGR (%)', 0) or 0)
    mdd = abs(float(metrics.get('MDD (%)', 0) or 0))
    sharpe = float(metrics.get('Sharpe Ratio', 0) or 0)
    alpha = float(metrics.get('Alpha (%)', 0) or 0)
    bench_mdd = abs(float(metrics.get('Benchmark MDD (%)', mdd) or mdd))
    win_rate = float(metrics.get('Win Rate (%)', 0) or 0)
    pf_raw = metrics.get('Profit Factor', 0)
    pf = 4.0 if pf_raw == '∞' else float(pf_raw or 0)
    trades = int(metrics.get('Total Trades', 0) or 0)
    exp = float(metrics.get('Exposure (%)', 100) or 100)
    tot_ret = float(metrics.get('Total Return (%)', 0) or 0)

    # 1. 수익성 (CAGR)
    s_ret = _interp(cagr, [-15, 0, 6, 12, 20, 35], [10, 40, 60, 76, 90, 98])
    # 2. 안정성 (MDD)
    s_safe = _interp(mdd, [0, 8, 15, 25, 35, 55], [98, 88, 76, 58, 40, 12])
    # 3. 효율성 (Sharpe)
    s_eff = _interp(sharpe, [-0.5, 0, 0.5, 1.0, 1.5, 2.2], [15, 32, 54, 75, 90, 98])
    # 4. 시장대비 (Alpha + MDD 방어)
    mdd_saved = bench_mdd - mdd
    s_mkt = _interp(alpha, [-30, 0, 10, 30, 80], [20, 55, 72, 86, 96])
    s_mkt = min(98.0, max(10.0, s_mkt + _interp(mdd_saved, [-20, 0, 15, 30], [-12, 0, 10, 18])))
    # 5. 거래품질 (장기보유/자산배분처럼 매매 횟수가 극소수면 중립 75점)
    if trades <= 2:
        s_qual = 75.0 if tot_ret >= 0 else 45.0
    else:
        s_pf = _interp(pf, [0.5, 1.0, 1.5, 2.2, 3.5], [15, 45, 70, 88, 96])
        s_wr = _interp(win_rate, [20, 40, 55, 70, 85], [35, 60, 76, 88, 95])
        sample_bonus = 0 if trades >= 15 else -8
        s_qual = max(15.0, min(98.0, 0.65 * s_pf + 0.35 * s_wr + sample_bonus))

    total_score = round(0.28 * s_ret + 0.25 * s_safe + 0.22 * s_eff + 0.13 * s_mkt + 0.12 * s_qual)
    if total_score >= 85:
        grade, color, tag = 'S', '#F5B041', '최상급 전략 — 수익과 방어력이 모두 뛰어납니다'
    elif total_score >= 73:
        grade, color, tag = 'A', '#00D084', '우수한 전략 — 실전 검토 가치가 높습니다'
    elif total_score >= 60:
        grade, color, tag = 'B', '#3B82F6', '무난한 전략 — 리스크 관리나 조건을 조금 다듬어 보세요'
    elif total_score >= 45:
        grade, color, tag = 'C', '#F59E0B', '보통 이하 — 그냥 보유보다 나은지 다시 점검하세요'
    else:
        grade, color, tag = 'D', '#FF4D4F', '개선 필요 — 손실 위험이 크거나 수익성이 낮습니다'

    strengths, warnings, tips = [], [], []
    if cagr >= 12:
        strengths.append(f"연평균 {cagr:.1f}% 성장 — 장기 복리 효과가 강력합니다.")
    if mdd <= 15:
        strengths.append(f"최대 낙폭이 -{mdd:.1f}%로 작아 하락장에서도 멘탈 관리가 쉽습니다.")
    elif bench_mdd - mdd >= 10:
        strengths.append(f"그냥 보유(-{bench_mdd:.1f}%) 대비 최대 낙폭을 {bench_mdd - mdd:.1f}%p 줄였습니다.")
    if sharpe >= 1.2:
        strengths.append(f"샤프 지수 {sharpe:.2f} — 변동성 대비 수익 효율이 매우 좋습니다.")
    if alpha > 5:
        strengths.append(f"그냥 보유 대비 +{alpha:.1f}%p 초과 수익을 냈습니다.")
    if pf >= 1.8 and trades >= 5:
        strengths.append(f"손익비(PF) {pf:.2f} — 이길 때 크게 먹고 질 때 적게 잃었습니다.")

    if tot_ret < 0:
        warnings.append(f"최종 누적 수익률이 {tot_ret:+.1f}%로 원금 손실 구간입니다.")
    if alpha < -5:
        warnings.append(f"같은 기간 그냥 보유만 한 것보다 {-alpha:.1f}%p 덜 벌었습니다.")
    if mdd > 25:
        warnings.append(f"최대 낙폭 -{mdd:.1f}%는 실전에서 버티기 힘든 큰 하락입니다.")
    if 0 < trades < 10:
        warnings.append(f"완료된 거래가 {trades}번뿐이라 우연일 가능성을 배제할 수 없습니다.")
    if sharpe > 3.0 or (win_rate > 85 and trades >= 10):
        warnings.append("수치가 비정상적으로 높습니다. 특정 기간에만 맞춰진 과적합이 아닌지 검증 기간 분할로 확인하세요.")

    if mdd > 20:
        tips.append("🛡️ 세부 설정에서 '손절(-8%~-12%)' 또는 '고점 대비 추적손절(-12%)'을 켜면 낙폭을 줄일 수 있습니다.")
    if trades > 80 and tot_ret < metrics.get('Benchmark Return (%)', 0):
        tips.append("💸 매매가 잦아 수수료·세금 부담이 큽니다. 이동평균 기간을 늘리거나 주봉/월봉 전략을 써보세요.")
    if exp < 35 and alpha < 0:
        tips.append("⏱️ 현금으로 쉬는 기간이 너무 깁니다(보유 비중 " f"{exp:.0f}%). 진입 조건을 조금 완화해 보세요.")
    if not tips:
        tips.append("🔬 '전략 대결 & 최적화' 탭에서 다른 전략·숫자 조합과도 한 번 비교해 보세요.")

    return dict(
        score=total_score, grade=grade, color=color, headline=tag,
        radar={
            '수익성': round(s_ret),
            '안정성': round(s_safe),
            '효율성': round(s_eff),
            '시장대비': round(s_mkt),
            '거래품질': round(s_qual),
        },
        strengths=strengths, warnings=warnings, tips=tips,
    )


def _normalize_trades(df_trades):
    if df_trades is None or df_trades.empty:
        return pd.DataFrame()
    df = df_trades.copy()
    rename_map = {
        '종목코드': 'ticker', '매수일': 'entry_date', '매도일': 'exit_date',
        '매수가': 'entry_price', '매도가': 'exit_price', '수량': 'shares',
        '수익률(%)': 'return_pct', '수익금': 'pnl', '청산사유': 'reason',
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns and v not in df.columns})
    if 'is_open' not in df.columns:
        df['is_open'] = df.get('exit_date', pd.Series(dtype=str)).astype(str).isin(['보유 중', '미청산'])
    return df


def _calc_underwater(equity_series: pd.Series, top_n: int = 5) -> pd.DataFrame:
    if len(equity_series) < 2:
        return pd.DataFrame()
    dd = equity_series / equity_series.cummax() - 1.0
    under = dd < 0
    if not under.any():
        return pd.DataFrame()
    groups = (~under).cumsum()
    rows = []
    for _, grp in dd[under].groupby(groups[under]):
        trough_dt = grp.idxmin()
        min_dd = float(grp.min()) * 100
        start_pos = max(0, equity_series.index.get_loc(grp.index[0]) - 1)
        peak_dt = equity_series.index[start_pos]
        end_pos = equity_series.index.get_loc(grp.index[-1])
        recovered = end_pos + 1 < len(equity_series)
        rec_dt = equity_series.index[end_pos + 1] if recovered else None
        rows.append({
            '고점일': peak_dt.strftime('%Y-%m-%d'),
            '저점일': trough_dt.strftime('%Y-%m-%d'),
            '회복일': rec_dt.strftime('%Y-%m-%d') if rec_dt is not None else '미회복',
            '최대낙폭(%)': round(min_dd, 2),
            '하락기간(일)': int((trough_dt - peak_dt).days),
            '회복까지(일)': int((rec_dt - peak_dt).days) if rec_dt is not None else int((equity_series.index[-1] - peak_dt).days),
        })
    out = pd.DataFrame(rows).sort_values('최대낙폭(%)').head(top_n).reset_index(drop=True)
    out.insert(0, '순위', range(1, len(out) + 1))
    return out


def calculate_tearsheet(result_or_equity, initial_capital=None, df_trades=None,
                        benchmark_series=None, oos_start=None, risk_free=0.025):
    """
    종합 성과지표 + 월별/연도별/롤링/위기구간/AI등급 계산.
    첫 인자는 BacktestResult 또는 df_equity DataFrame 모두 허용.
    """
    contributions = None
    total_invested = None
    total_fees = 0.0

    if hasattr(result_or_equity, 'equity') and hasattr(result_or_equity, 'trades'):
        res = result_or_equity
        df_equity = res.equity
        df_trades = res.trades
        benchmark_series = res.benchmark
        extra = res.extra or {}
        contributions = extra.get('contributions')
        total_invested = extra.get('total_invested')
        total_fees = float(extra.get('total_fees', 0.0) or 0.0)
        if initial_capital is None:
            initial_capital = float(df_equity['Equity'].iloc[0]) if not df_equity.empty else 10_000_000
    else:
        df_equity = result_or_equity

    if df_equity is None or df_equity.empty:
        return {}, pd.Series(dtype=float), pd.DataFrame(), pd.DataFrame(), pd.Series(dtype=float), {}

    equity = df_equity['Equity'].astype(float)
    init_cap = float(initial_capital or equity.iloc[0])
    invested_cap = float(total_invested or init_cap)
    final_cap = float(equity.iloc[-1])

    # 적립식(DCA) 현금 유입을 제거한 일간 시간가중수익률(TWR)
    if contributions is not None:
        c_aligned = contributions.reindex(equity.index).fillna(0.0).astype(float)
        c_ext = c_aligned.copy()
        c_ext.iloc[0] = 0.0
        prev_eq = equity.shift(1)
        daily_returns = ((equity - c_ext) / prev_eq - 1.0).dropna()
        twr_curve = (1.0 + daily_returns).cumprod() * init_cap
        twr_curve = pd.concat([pd.Series([init_cap], index=[equity.index[0]]), twr_curve])
    else:
        daily_returns = equity.pct_change().dropna()
        twr_curve = equity

    n_days = len(equity)
    n_years = max((equity.index[-1] - equity.index[0]).days / 365.25, n_days / 252.0, 1 / 252.0)

    if contributions is not None and invested_cap > init_cap:
        total_return = (final_cap / invested_cap) - 1.0
    else:
        total_return = (final_cap / init_cap) - 1.0
    cagr = (twr_curve.iloc[-1] / twr_curve.iloc[0]) ** (1.0 / n_years) - 1.0 if twr_curve.iloc[0] > 0 else 0.0
    volatility = float(daily_returns.std() * np.sqrt(252)) if len(daily_returns) > 1 else 0.0

    # 드로다운 (TWR 기준)
    drawdown_frac = (twr_curve / twr_curve.cummax()) - 1.0
    drawdown = drawdown_frac * 100.0
    mdd = float(drawdown_frac.min())
    is_dd = drawdown_frac < 0
    mdd_duration = int(is_dd.groupby((~is_dd).cumsum()).sum().max()) if is_dd.any() else 0

    # Sharpe / Sortino / Calmar / Ulcer / Tail
    daily_rf = (1.0 + risk_free) ** (1.0 / 252.0) - 1.0
    excess = daily_returns - daily_rf
    std = float(daily_returns.std()) if len(daily_returns) > 1 else 0.0
    sharpe = float((excess.mean() / std) * np.sqrt(252)) if std > 0 else 0.0
    downside = excess[excess < 0]
    d_std = float(downside.std()) if len(downside) > 1 else 0.0
    sortino = float((excess.mean() / d_std) * np.sqrt(252)) if d_std > 0 else 0.0
    calmar = float(cagr / abs(mdd)) if mdd != 0 else 0.0
    ulcer = float(np.sqrt((drawdown ** 2).mean())) if len(drawdown) > 0 else 0.0
    if len(daily_returns) >= 20:
        q95, q05 = np.percentile(daily_returns, 95), abs(np.percentile(daily_returns, 5))
        tail_ratio = float(q95 / q05) if q05 > 0 else 0.0
    else:
        tail_ratio = 0.0

    # 노출 비중
    if 'Holdings' in df_equity.columns:
        exposure = float((df_equity['Holdings'] / df_equity['Equity'].replace(0, np.nan)).fillna(0).mean() * 100)
        time_in_mkt = float((df_equity['Positions'] > 0).mean() * 100) if 'Positions' in df_equity.columns else exposure
    else:
        exposure, time_in_mkt = 100.0, 100.0

    # 벤치마크
    bench_return = bench_cagr = bench_mdd = beta = 0.0
    bench_dd = pd.Series(dtype=float)
    if benchmark_series is not None and len(benchmark_series) > 1:
        b = benchmark_series.reindex(equity.index).ffill().dropna()
        if len(b) > 1 and b.iloc[0] > 0:
            bench_return = float((b.iloc[-1] / b.iloc[0]) - 1.0)
            bench_cagr = float((b.iloc[-1] / b.iloc[0]) ** (1.0 / n_years) - 1.0)
            bench_dd = ((b / b.cummax()) - 1.0) * 100.0
            bench_mdd = float(bench_dd.min() / 100.0)
            b_ret = b.pct_change().dropna()
            aligned = pd.concat([daily_returns, b_ret], axis=1).dropna()
            if len(aligned) > 5 and aligned.iloc[:, 1].var() > 0:
                beta = float(aligned.iloc[:, 0].cov(aligned.iloc[:, 1]) / aligned.iloc[:, 1].var())
    alpha = (twr_curve.iloc[-1] / twr_curve.iloc[0] - 1.0) - bench_return

    # 거래 통계
    trades_norm = _normalize_trades(df_trades)
    total_trades = open_trades = max_wins = max_losses = 0
    win_rate = avg_win = avg_loss = avg_hold = best_tr = worst_tr = expectancy = payoff = avg_mae = avg_mfe = 0.0
    profit_factor = 0.0

    if not trades_norm.empty:
        open_trades = int(trades_norm['is_open'].sum())
        completed = trades_norm[~trades_norm['is_open']].copy()
        total_trades = len(completed)
        if total_trades > 0:
            r = completed['return_pct'].astype(float)
            wins, losses = r[r > 0], r[r <= 0]
            win_rate = float(len(wins) / total_trades * 100)
            pnl_s = completed['pnl'].astype(float) if 'pnl' in completed.columns else r
            w_sum = float(pnl_s[pnl_s > 0].sum())
            l_sum = float(abs(pnl_s[pnl_s <= 0].sum()))
            profit_factor = (w_sum / l_sum) if l_sum > 0 else (float('inf') if w_sum > 0 else 0.0)
            avg_win = float(wins.mean()) if len(wins) > 0 else 0.0
            avg_loss = float(losses.mean()) if len(losses) > 0 else 0.0
            payoff = (avg_win / abs(avg_loss)) if avg_loss < 0 else (float('inf') if avg_win > 0 else 0.0)
            expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)
            best_tr, worst_tr = float(r.max()), float(r.min())
            if 'hold_days' in completed.columns:
                avg_hold = float(completed['hold_days'].mean())
            else:
                try:
                    avg_hold = float((pd.to_datetime(completed['exit_date']) - pd.to_datetime(completed['entry_date'])).dt.days.mean())
                except Exception:
                    avg_hold = 0.0
            if 'mae_pct' in completed.columns:
                avg_mae = float(completed['mae_pct'].mean())
                avg_mfe = float(completed['mfe_pct'].mean())
            is_w = (r > 0).astype(int)
            for _, g in is_w.groupby((is_w != is_w.shift()).cumsum()):
                if g.iloc[0] == 1:
                    max_wins = max(max_wins, len(g))
                else:
                    max_losses = max(max_losses, len(g))

    metrics = {
        'Initial Capital': round(init_cap, 0),
        'Total Invested': round(invested_cap, 0),
        'Final Capital': round(final_cap, 0),
        'Net Profit': round(final_cap - invested_cap, 0),
        'Total Return (%)': round(total_return * 100, 2),
        'CAGR (%)': round(cagr * 100, 2),
        'Benchmark Return (%)': round(bench_return * 100, 2),
        'Benchmark CAGR (%)': round(bench_cagr * 100, 2),
        'Alpha (%)': round(alpha * 100, 2),
        'Beta': round(beta, 2),
        'MDD (%)': round(mdd * 100, 2),
        'Benchmark MDD (%)': round(bench_mdd * 100, 2),
        'MDD Duration (days)': mdd_duration,
        'Volatility (%)': round(volatility * 100, 2),
        'Sharpe Ratio': round(sharpe, 2),
        'Sortino Ratio': round(sortino, 2),
        'Calmar Ratio': round(calmar, 2),
        'Ulcer Index': round(ulcer, 2),
        'Tail Ratio': round(tail_ratio, 2),
        'Exposure (%)': round(exposure, 1),
        'Time in Market (%)': round(time_in_mkt, 1),
        'Total Fees': round(total_fees, 0),
        'Total Trades': total_trades,
        'Open Trades': open_trades,
        'Win Rate (%)': round(win_rate, 1),
        'Profit Factor': '∞' if profit_factor == float('inf') else round(profit_factor, 2),
        'Payoff Ratio': '∞' if payoff == float('inf') else round(payoff, 2),
        'Expectancy (%)': round(expectancy, 2),
        'Avg Win (%)': round(avg_win, 2),
        'Avg Loss (%)': round(avg_loss, 2),
        'Best Trade (%)': round(best_tr, 2),
        'Worst Trade (%)': round(worst_tr, 2),
        'Avg Holding Days': round(avg_hold, 1),
        'Avg MAE (%)': round(avg_mae, 2),
        'Avg MFE (%)': round(avg_mfe, 2),
        'Max Consecutive Wins': max_wins,
        'Max Consecutive Losses': max_losses,
    }

    # 월별 수익률 (첫 달도 정확히 계산)
    monthly_returns = pd.DataFrame()
    if len(daily_returns) >= 5:
        m_ret = (1.0 + daily_returns).resample('ME').prod() - 1.0
        mr_df = pd.DataFrame({'Year': m_ret.index.year, 'Month': m_ret.index.month, 'Return': m_ret.values})
        monthly_returns = mr_df.pivot_table(index='Year', columns='Month', values='Return', aggfunc='first')
        month_names = {i: f'{i}월' for i in range(1, 13)}
        monthly_returns = monthly_returns.rename(columns=month_names)
        y_ret = (1.0 + daily_returns).resample('YE').prod() - 1.0
        ymap = dict(zip(y_ret.index.year, y_ret.values))
        monthly_returns['연간'] = monthly_returns.index.map(lambda y: ymap.get(y, np.nan))

    # 연도별 수익률 (첫 해도 포함!)
    yearly_returns = pd.DataFrame()
    if len(daily_returns) >= 5:
        y_ret = (1.0 + daily_returns).resample('YE').prod() - 1.0
        yearly_returns = pd.DataFrame({
            '연도': y_ret.index.year,
            '전략 수익률(%)': (y_ret.values * 100).round(2),
        })
        if benchmark_series is not None and len(benchmark_series) > 5:
            b_dr = benchmark_series.reindex(equity.index).ffill().pct_change().dropna()
            if len(b_dr) >= 5:
                b_yr = (1.0 + b_dr).resample('YE').prod() - 1.0
                b_map = dict(zip(b_yr.index.year, (b_yr.values * 100).round(2)))
                yearly_returns['벤치마크(%)'] = yearly_returns['연도'].map(lambda y: b_map.get(y, 0.0))
                yearly_returns['초과수익(%)'] = (yearly_returns['전략 수익률(%)'] - yearly_returns['벤치마크(%)']).round(2)

    # 6개월(126일) 롤링 연환산 수익률
    rolling_returns = pd.Series(dtype=float)
    if len(daily_returns) > 126:
        rolling_returns = ((1.0 + daily_returns).rolling(126).apply(np.prod, raw=True) ** 2 - 1.0) * 100.0

    # 최악의 하락 구간 TOP 5
    metrics['_underwater'] = _calc_underwater(twr_curve, top_n=5)
    metrics['_bench_dd'] = bench_dd
    metrics['_grade'] = grade_strategy(metrics)

    # IS / OOS 비교
    is_oos = {}
    if oos_start is not None:
        oos_ts = pd.Timestamp(oos_start)
        pos = twr_curve.index.searchsorted(oos_ts)
        if 10 < pos < len(twr_curve) - 10:
            eq_is, eq_oos = twr_curve.iloc[:pos + 1], twr_curve.iloc[pos:]
            is_d = eq_is.pct_change().dropna()
            oos_d = eq_oos.pct_change().dropna()
            is_oos = {
                'IS Return (%)': round((eq_is.iloc[-1] / eq_is.iloc[0] - 1) * 100, 2),
                'OOS Return (%)': round((eq_oos.iloc[-1] / eq_oos.iloc[0] - 1) * 100, 2),
                'IS MDD (%)': round(((eq_is / eq_is.cummax()) - 1).min() * 100, 2),
                'OOS MDD (%)': round(((eq_oos / eq_oos.cummax()) - 1).min() * 100, 2),
                'IS Sharpe': round(float((is_d.mean() / is_d.std()) * np.sqrt(252)) if is_d.std() > 0 else 0.0, 2),
                'OOS Sharpe': round(float((oos_d.mean() / oos_d.std()) * np.sqrt(252)) if oos_d.std() > 0 else 0.0, 2),
                'OOS Start': oos_ts.strftime('%Y-%m-%d'),
            }

    return metrics, drawdown, monthly_returns, yearly_returns, rolling_returns, is_oos


def analyze_live_signal(result, cfg=None) -> dict:
    """
    가장 최근 거래일 기준 오늘의 실전 매매 신호 및 포지션 상태 브리핑 생성
    """
    from .formula_tools import inspect_formula_live_values

    if result is None or result.equity is None or result.equity.empty:
        return {}

    last_dt = result.equity.index[-1]
    last_date_str = last_dt.strftime('%Y-%m-%d')
    tickers = list((result.price_data or {}).keys())
    primary_ticker = tickers[0] if tickers else ''
    primary_df = result.price_data.get(primary_ticker) if primary_ticker else None

    trades = _normalize_trades(result.trades)
    open_pos = trades[trades['is_open']] if not trades.empty and 'is_open' in trades.columns else pd.DataFrame()

    sig_diag = {}
    if primary_df is not None and not primary_df.empty and result.entry_expr:
        mtf_flag = getattr(cfg, 'mtf_close_only', True) if cfg is not None else True
        sig_diag = inspect_formula_live_values(primary_df, result.entry_expr, result.exit_expr or '', mtf_close_only=mtf_flag)

    holding_info = None
    if not open_pos.empty:
        row = open_pos.iloc[-1]
        t_code = row.get('ticker', primary_ticker)
        t_df = (result.price_data or {}).get(t_code, primary_df)
        cur_px = float(t_df['Close'].iloc[-1]) if t_df is not None and not t_df.empty else float(row.get('exit_price', 0))
        entry_px = float(row.get('entry_price', cur_px))
        ret_pct = (cur_px / entry_px - 1.0) * 100.0 if entry_px > 0 else 0.0
        holding_info = {
            'ticker': t_code,
            'entry_date': pd.to_datetime(row['entry_date']).strftime('%Y-%m-%d'),
            'entry_price': entry_px,
            'current_price': cur_px,
            'shares': int(row.get('shares', 0)),
            'return_pct': round(ret_pct, 2),
            'hold_days': int(row.get('hold_days', 0)),
        }
        if cfg is not None:
            if getattr(cfg, 'stop_loss', 0) > 0:
                sl_px = entry_px * (1.0 - cfg.stop_loss)
                holding_info['stop_loss_price'] = sl_px
                holding_info['stop_loss_gap_pct'] = round((sl_px / cur_px - 1.0) * 100.0, 2)
            if getattr(cfg, 'take_profit', 0) > 0:
                tp_px = entry_px * (1.0 + cfg.take_profit)
                holding_info['take_profit_price'] = tp_px
                holding_info['take_profit_gap_pct'] = round((tp_px / cur_px - 1.0) * 100.0, 2)

    entry_today = bool(sig_diag.get('entry_today', False))
    exit_today = bool(sig_diag.get('exit_today', False))

    if holding_info and exit_today:
        status, badge, color = 'SELL_TODAY', '🔴 오늘 매도 신호 발생!', '#FF4D4F'
        summary = f"보유 중인 {holding_info['ticker']}에 매도 조건이 충족되었습니다. (평가손익 {holding_info['return_pct']:+.2f}%)"
    elif not holding_info and entry_today:
        status, badge, color = 'BUY_TODAY', '🟢 오늘 매수 신호 발생!', '#00D084'
        summary = f"{primary_ticker}의 매수 조건이 최근 거래일({last_date_str})에 충족되었습니다."
    elif holding_info:
        status, badge, color = 'HOLDING', '🔵 현재 보유 유지 중', '#3B82F6'
        summary = f"{holding_info['ticker']} 보유 중 ({holding_info['entry_date']} 진입 · {holding_info['return_pct']:+.2f}%)"
    else:
        status, badge, color = 'WATCHING', '⚪ 현금 관망 중 (신호 대기)', '#94A3B8'
        summary = f"최근 매수 조건 충족일: {sig_diag.get('last_entry_date', '없음')} · 현재는 진입 신호 대기 중입니다."

    return {
        'status': status,
        'badge': badge,
        'color': color,
        'summary': summary,
        'last_date': last_date_str,
        'primary_ticker': primary_ticker,
        'holding': holding_info,
        'signal_diag': sig_diag,
    }


def run_monte_carlo(result_or_equity, horizon_days: int = 252, n_sims: int = 400, seed: int = 42) -> dict:
    """
    과거 일간 수익률 분포를 복원추출(Bootstrap)하여 미래 자산 경로 시뮬레이션
    """
    if hasattr(result_or_equity, 'equity'):
        df_eq = result_or_equity.equity
        extra = result_or_equity.extra or {}
        contributions = extra.get('contributions')
    else:
        df_eq = result_or_equity
        contributions = None

    if df_eq is None or df_eq.empty or len(df_eq) < 20:
        return {}

    eq = df_eq['Equity'].astype(float)
    start_cap = float(eq.iloc[-1])

    if contributions is not None:
        c_ext = contributions.reindex(eq.index).fillna(0.0).astype(float)
        c_ext.iloc[0] = 0.0
        dr = ((eq - c_ext) / eq.shift(1) - 1.0).dropna().to_numpy(dtype=float)
    else:
        dr = eq.pct_change().dropna().to_numpy(dtype=float)

    dr = dr[np.isfinite(dr)]
    if len(dr) < 10:
        return {}

    rng = np.random.default_rng(seed)
    sampled = rng.choice(dr, size=(int(n_sims), int(horizon_days)), replace=True)
    paths = start_cap * np.cumprod(1.0 + sampled, axis=1)
    paths = np.hstack([np.full((int(n_sims), 1), start_cap), paths])

    future_dates = pd.bdate_range(start=eq.index[-1], periods=int(horizon_days) + 1)
    q10, q25, q50, q75, q90 = np.percentile(paths, [10, 25, 50, 75, 90], axis=0)
    curves = pd.DataFrame({'p10': q10, 'p25': q25, 'p50': q50, 'p75': q75, 'p90': q90}, index=future_dates)
    finals = paths[:, -1]

    return {
        'start_capital': start_cap,
        'horizon_days': int(horizon_days),
        'n_sims': int(n_sims),
        'curves': curves,
        'p10_final': float(q10[-1]),
        'p50_final': float(q50[-1]),
        'p90_final': float(q90[-1]),
        'p10_ret': float((q10[-1] / start_cap - 1.0) * 100.0),
        'p50_ret': float((q50[-1] / start_cap - 1.0) * 100.0),
        'p90_ret': float((q90[-1] / start_cap - 1.0) * 100.0),
        'prob_profit': float((finals > start_cap).mean() * 100.0),
        'prob_plus20': float((finals >= start_cap * 1.2).mean() * 100.0),
    }
