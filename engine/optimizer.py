"""
전략 비교 (13종 한 번에 대결) & 파라미터 자동 최적화 엔진
"""
import itertools
import pandas as pd

from .strategies import STRATEGIES, Strategy
from .simulator import SimConfig, run_backtest, run_vbo_backtest
from .tearsheet import calculate_tearsheet


def _run_one(strategy: Strategy, params: dict, data: dict, cfg: SimConfig,
             start, end, asset_types=None, market_ok=None):
    if strategy.kind == 'vbo':
        return run_vbo_backtest(
            data,
            k_value=float(params.get('k', 0.5)),
            ma_filter=int(params.get('ma', 5)),
            cfg=cfg, start=start, end=end,
            asset_types=asset_types, market_ok=market_ok,
        )
    en, ex = strategy.formulas(params)
    return run_backtest(
        data, en, ex, cfg=cfg, start=start, end=end,
        asset_types=asset_types, market_ok=market_ok,
    )


def compare_all_strategies(
    data: dict,
    cfg: SimConfig,
    start=None,
    end=None,
    asset_types=None,
    market_ok=None,
    strategies=None,
    progress=None,
):
    """
    등록된 모든 전략을 동일 조건으로 실행해 순위표와 수익곡선 반환.
    Returns: (df_ranking, curves_dict)
    """
    strat_list = strategies or STRATEGIES
    rows = []
    curves = {}
    total = len(strat_list)

    for idx, s in enumerate(strat_list):
        if progress:
            progress((idx + 1) / total, f"{s.emoji} {s.name} 시뮬레이션 중... ({idx + 1}/{total})")
        try:
            res = _run_one(s, s.defaults(), data, cfg, start, end, asset_types, market_ok)
            metrics, *_ = calculate_tearsheet(res)
            g = metrics.get('_grade', {})
            rows.append({
                '등급': g.get('grade', '-'),
                '종합점수': g.get('score', 0),
                '전략명': s.label,
                '카테고리': s.category,
                '누적수익률(%)': metrics.get('Total Return (%)', 0.0),
                '연평균CAGR(%)': metrics.get('CAGR (%)', 0.0),
                '최대낙폭MDD(%)': metrics.get('MDD (%)', 0.0),
                '샤프지수': metrics.get('Sharpe Ratio', 0.0),
                '승률(%)': metrics.get('Win Rate (%)', 0.0),
                '손익비(PF)': str(metrics.get('Profit Factor', 0)),
                '거래횟수': metrics.get('Total Trades', 0),
                '보유비중(%)': metrics.get('Exposure (%)', 0.0),
                '최종자산(원)': int(metrics.get('Final Capital', cfg.initial_capital)),
                'strategy_id': s.id,
            })
            curves[s.label] = res.equity['Equity']
        except Exception:
            continue

    if not rows:
        return pd.DataFrame(), {}

    df = pd.DataFrame(rows).sort_values(['종합점수', '샤프지수'], ascending=False).reset_index(drop=True)
    df.insert(0, '순위', range(1, len(df) + 1))
    return df, curves


def _valid_combo(params: dict) -> bool:
    if 'short' in params and 'long' in params and params['short'] >= params['long']:
        return False
    if 'fast' in params and 'slow' in params and params['fast'] >= params['slow']:
        return False
    if 'lo' in params and 'hi' in params and params['lo'] >= params['hi']:
        return False
    return True


def optimize_strategy_params(
    strategy: Strategy,
    data: dict,
    cfg: SimConfig,
    start=None,
    end=None,
    asset_types=None,
    market_ok=None,
    sort_by: str = '샤프지수',
    progress=None,
):
    """
    단일 전략의 파라미터 그리드 탐색.
    Returns: (df_results, best_params_dict)
    """
    if not strategy.params:
        res = _run_one(strategy, {}, data, cfg, start, end, asset_types, market_ok)
        m, *_ = calculate_tearsheet(res)
        g = m.get('_grade', {})
        df = pd.DataFrame([{
            '순위': 1, '파라미터': '기본 설정 (조절 변수 없음)',
            '누적수익률(%)': m.get('Total Return (%)', 0.0),
            '연평균CAGR(%)': m.get('CAGR (%)', 0.0),
            '최대낙폭MDD(%)': m.get('MDD (%)', 0.0),
            '샤프지수': m.get('Sharpe Ratio', 0.0),
            '칼마비율': m.get('Calmar Ratio', 0.0),
            '승률(%)': m.get('Win Rate (%)', 0.0),
            '거래횟수': m.get('Total Trades', 0),
            '종합점수': g.get('score', 0),
            '_params': {},
        }])
        return df, {}

    keys = [p.key for p in strategy.params]
    grids = [p.grid if p.grid else (p.default,) for p in strategy.params]
    combos = [dict(zip(keys, vals)) for vals in itertools.product(*grids)]
    combos = [c for c in combos if _valid_combo(c)]
    if len(combos) > 120:
        combos = combos[:120]

    rows = []
    total = len(combos)
    for idx, p in enumerate(combos):
        if progress and (idx % 5 == 0 or idx == total - 1):
            progress((idx + 1) / total, f"🔬 조합 탐색 중... ({idx + 1}/{total})")
        try:
            res = _run_one(strategy, p, data, cfg, start, end, asset_types, market_ok)
            m, *_ = calculate_tearsheet(res)
            g = m.get('_grade', {})
            summary = " · ".join(f"{k}={v}" for k, v in p.items())
            rows.append({
                '파라미터': summary,
                '누적수익률(%)': m.get('Total Return (%)', 0.0),
                '연평균CAGR(%)': m.get('CAGR (%)', 0.0),
                '최대낙폭MDD(%)': m.get('MDD (%)', 0.0),
                '샤프지수': m.get('Sharpe Ratio', 0.0),
                '칼마비율': m.get('Calmar Ratio', 0.0),
                '승률(%)': m.get('Win Rate (%)', 0.0),
                '거래횟수': m.get('Total Trades', 0),
                '종합점수': g.get('score', 0),
                '_params': p,
            })
        except Exception:
            continue

    if not rows:
        return pd.DataFrame(), strategy.defaults()

    sort_col = sort_by if sort_by in rows[0] else '샤프지수'
    df = pd.DataFrame(rows).sort_values(sort_col, ascending=False).reset_index(drop=True)
    df.insert(0, '순위', range(1, len(df) + 1))
    best_params = dict(df.iloc[0]['_params'])

    # 2개 이상 파라미터가 있으면 2D 히트맵 피벗 저장
    if len(keys) >= 2:
        k1, k2 = keys[0], keys[1]
        hm_rows = []
        for _, r in df.iterrows():
            pr = r['_params']
            if k1 in pr and k2 in pr:
                hm_rows.append({k1: pr[k1], k2: pr[k2], 'sharpe': r['샤프지수'], 'ret': r['누적수익률(%)']})
        if hm_rows:
            hdf = pd.DataFrame(hm_rows)
            piv = hdf.pivot_table(index=k1, columns=k2, values='sharpe', aggfunc='mean')
            if piv.shape[0] >= 2 and piv.shape[1] >= 2:
                df.attrs['heatmap'] = {
                    'pivot': piv,
                    'ret_pivot': hdf.pivot_table(index=k1, columns=k2, values='ret', aggfunc='mean'),
                    'y_label': strategy.params[0].label,
                    'x_label': strategy.params[1].label,
                }
    return df, best_params


def build_custom_formula_slots(entry_expr: str, exit_expr: str, sync_linked: bool = True) -> list:
    """수식 내 최적화 가능한 변수 슬롯 목록 추출 (같은 sync_key 숫자 묶음 지원)"""
    from .formula_tools import extract_formula_numbers

    en_toks = extract_formula_numbers(entry_expr)
    ex_toks = extract_formula_numbers(exit_expr)

    if not sync_linked:
        slots = []
        for t in en_toks:
            slots.append({
                'label': f"매수: {t['label']} ({t['raw']})",
                'value': t['value'],
                'grid': t['grid'],
                'targets': [('entry', t['index'])],
            })
        for t in ex_toks:
            slots.append({
                'label': f"매도: {t['label']} ({t['raw']})",
                'value': t['value'],
                'grid': t['grid'],
                'targets': [('exit', t['index'])],
            })
        for i, s in enumerate(slots):
            s['index'] = i
        return slots

    grouped = {}
    order = []
    for side, toks in (('entry', en_toks), ('exit', ex_toks)):
        for t in toks:
            sk = t.get('sync_key') or f"{side}:{t['index']}"
            if sk not in grouped:
                grouped[sk] = {
                    'sides': {side},
                    'tok': t,
                    'targets': [(side, t['index'])],
                }
                order.append(sk)
            else:
                grouped[sk]['sides'].add(side)
                grouped[sk]['targets'].append((side, t['index']))

    slots = []
    for i, sk in enumerate(order):
        g = grouped[sk]
        t = g['tok']
        if len(g['targets']) > 1:
            prefix = '🔗 공통' if len(g['sides']) > 1 else ('🔗 매수' if 'entry' in g['sides'] else '🔗 매도')
            lbl = f"{prefix} {t['label']} ({t['raw']})"
        else:
            side_txt = '매수' if 'entry' in g['sides'] else '매도'
            lbl = f"{side_txt}: {t['label']} ({t['raw']})"
        slots.append({
            'index': i,
            'label': lbl,
            'value': t['value'],
            'grid': t['grid'],
            'targets': g['targets'],
        })
    return slots


def optimize_custom_formula(
    entry_expr: str,
    exit_expr: str,
    data: dict,
    cfg: SimConfig,
    start=None,
    end=None,
    asset_types=None,
    market_ok=None,
    sort_by: str = '샤프지수',
    progress=None,
    slot_indices=None,
    sync_linked: bool = True,
):
    """
    사용자가 입력/조립한 임의 수식에서 핵심 숫자 1~2개를 선택(또는 자동 선정)하여 그리드 최적화 & 2D 히트맵 생성
    Returns: (df_results, best_info)
    """
    from .formula_tools import extract_formula_numbers, replace_formula_numbers

    en_toks = extract_formula_numbers(entry_expr)
    ex_toks = extract_formula_numbers(exit_expr)
    all_slots = build_custom_formula_slots(entry_expr, exit_expr, sync_linked=sync_linked)
    avail_info = [{'index': s['index'], 'label': s['label'], 'value': s['value']} for s in all_slots]

    if not all_slots:
        res = run_backtest(data, entry_expr, exit_expr, cfg, start, end, asset_types, market_ok)
        m, *_ = calculate_tearsheet(res)
        g = m.get('_grade', {})
        df = pd.DataFrame([{
            '순위': 1, '파라미터': '조정 가능한 숫자 없음',
            '누적수익률(%)': m.get('Total Return (%)', 0.0),
            '연평균CAGR(%)': m.get('CAGR (%)', 0.0),
            '최대낙폭MDD(%)': m.get('MDD (%)', 0.0),
            '샤프지수': m.get('Sharpe Ratio', 0.0),
            '승률(%)': m.get('Win Rate (%)', 0.0),
            '거래횟수': m.get('Total Trades', 0),
            '종합점수': g.get('score', 0),
            '_entry': entry_expr, '_exit': exit_expr,
        }])
        return df, {'entry_expr': entry_expr, 'exit_expr': exit_expr, 'available_slots': avail_info}

    if slot_indices:
        chosen_idx = [int(i) for i in slot_indices if 0 <= int(i) < len(all_slots)]
        # 중복 제거
        chosen_idx = list(dict.fromkeys(chosen_idx))[:2]
        slots = [all_slots[i] for i in chosen_idx] if chosen_idx else all_slots[:2]
    else:
        slots = all_slots[:2]

    base_en_vals = [t['value'] for t in en_toks]
    base_ex_vals = [t['value'] for t in ex_toks]
    grids = [slot['grid'] for slot in slots]
    combos = list(itertools.product(*grids))[:64]

    rows = []
    total = len(combos)
    for idx, vals in enumerate(combos):
        if progress and (idx % 4 == 0 or idx == total - 1):
            progress((idx + 1) / total, f"🔬 수식 숫자 조합 테스트 중... ({idx + 1}/{total})")
        en_v = list(base_en_vals)
        ex_v = list(base_ex_vals)
        desc_parts = []
        for slot, new_v in zip(slots, vals):
            for side, t_idx in slot['targets']:
                if side == 'entry' and t_idx < len(en_v):
                    en_v[t_idx] = new_v
                elif side == 'exit' and t_idx < len(ex_v):
                    ex_v[t_idx] = new_v
            desc_parts.append(f"{slot['label'].split('(')[0].strip()}={new_v}")
        new_en = replace_formula_numbers(entry_expr, en_v)
        new_ex = replace_formula_numbers(exit_expr, ex_v)
        try:
            res = run_backtest(data, new_en, new_ex, cfg, start, end, asset_types, market_ok)
            m, *_ = calculate_tearsheet(res)
            g = m.get('_grade', {})
            rows.append({
                '파라미터': ' · '.join(desc_parts),
                '누적수익률(%)': m.get('Total Return (%)', 0.0),
                '연평균CAGR(%)': m.get('CAGR (%)', 0.0),
                '최대낙폭MDD(%)': m.get('MDD (%)', 0.0),
                '샤프지수': m.get('Sharpe Ratio', 0.0),
                '승률(%)': m.get('Win Rate (%)', 0.0),
                '거래횟수': m.get('Total Trades', 0),
                '종합점수': g.get('score', 0),
                '_v0': vals[0],
                '_v1': vals[1] if len(vals) > 1 else None,
                '_entry': new_en,
                '_exit': new_ex,
            })
        except Exception:
            continue

    if not rows:
        return pd.DataFrame(), {'entry_expr': entry_expr, 'exit_expr': exit_expr, 'available_slots': avail_info}

    sort_col = sort_by if sort_by in rows[0] else '샤프지수'
    df = pd.DataFrame(rows).sort_values(sort_col, ascending=False).reset_index(drop=True)
    df.insert(0, '순위', range(1, len(df) + 1))

    if len(slots) >= 2:
        piv = df.pivot_table(index='_v0', columns='_v1', values='샤프지수', aggfunc='mean')
        if piv.shape[0] >= 2 and piv.shape[1] >= 2:
            df.attrs['heatmap'] = {
                'pivot': piv,
                'ret_pivot': df.pivot_table(index='_v0', columns='_v1', values='누적수익률(%)', aggfunc='mean'),
                'y_label': slots[0]['label'],
                'x_label': slots[1]['label'],
            }

    best = df.iloc[0]
    return df, {
        'entry_expr': best['_entry'],
        'exit_expr': best['_exit'],
        'summary': best['파라미터'],
        'available_slots': avail_info,
    }
