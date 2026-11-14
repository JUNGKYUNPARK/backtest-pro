"""
수식 지능화 엔진 (초간편 수식 워크벤치 지원)
- 수식 → 한국어 자연어 번역 (AST 기반)
- 일봉 ↔ 주봉(W_) ↔ 월봉(M_) 원클릭 변환
- 수식 내 숫자 자동 추출 및 치환
- 실시간 신호 횟수 · 최근 거래일 지표값 미리보기
- 원클릭 수식 조각 팔레트
"""
import ast
import re
import numpy as np
import pandas as pd

from .parser import ConditionParser, normalize, to_signal, uses_timeframe
from .simulator import _period_end_mask

MTF_SUPPORTED = (
    'CLOSE', 'OPEN', 'HIGH', 'LOW', 'VOLUME',
    'MA', 'EMA', 'RSI', 'MACD', 'MACD_SIGNAL',
    'BULLISH', 'BEARISH', 'ROC', 'STOCH_K',
    'BB_UPPER', 'BB_LOWER', 'HIGHEST', 'LOWEST',
)
_MTF_RE = re.compile(r'\b(?:W_|M_)?(' + '|'.join(sorted(MTF_SUPPORTED, key=len, reverse=True)) + r')\b', re.I)
_NUM_RE = re.compile(r'(?<![A-Za-z0-9_.])\d+(?:\.\d+)?(?![A-Za-z0-9_])')

_KO_NAMES = {
    'CLOSE': '종가', 'OPEN': '시가', 'HIGH': '고가', 'LOW': '저가', 'VOLUME': '거래량',
    'MA': '이평', 'SMA': '이평', 'EMA': '지수이평', 'WMA': '가중이평',
    'RSI': 'RSI', 'MACD': 'MACD', 'MACD_SIGNAL': 'MACD시그널', 'MACD_HIST': 'MACD히스토그램',
    'BB_UPPER': '볼린저상단', 'BB_MID': '볼린저중심', 'BB_LOWER': '볼린저하단', 'BB_PCTB': '볼린저%B',
    'STOCH_K': '스토캐스틱%K', 'STOCH_D': '스토캐스틱%D',
    'CCI': 'CCI', 'WILLIAMS_R': '윌리엄스%R', 'ROC': '변화율', 'MOMENTUM': '모멘텀', 'MFI': 'MFI',
    'ADX': 'ADX', 'ATR': 'ATR', 'OBV': 'OBV', 'VWAP': 'VWAP',
    'HIGHEST': 'N일최고가', 'LOWEST': 'N일최저가', 'DISPARITY': '이격도', 'CHANGE': '등락률',
    'BULLISH': '양봉', 'BEARISH': '음봉', 'DOJI': '도지',
    'REF': '과거값', 'AVG': '평균', 'SMAX': '최댓값', 'SMIN': '최솟값', 'COUNT': '충족일수', 'ABS': '절댓값',
    'FRG_NET': '외국인순매수', 'INST_NET': '기관순매수', 'SMART_NET': '외인+기관순매수',
    'INDIV_NET': '개인순매수', 'FRG_RATIO': '외국인지분율',
    'FW_PER': '선행PER', 'PER': 'PER', 'PBR': 'PBR', 'TARGET_UPSIDE': '목표가괴리율',
    'VIX': '공포지수(VIX)', 'US10Y': '미10년금리', 'USDKRW': '원달러환율',
}


def _ko_symbol(name: str) -> str:
    u = name.upper()
    prefix = ''
    base = u
    if u.startswith('M_'):
        prefix = '🟧 월봉 '
        base = u[2:]
    elif u.startswith('W_'):
        prefix = '🟦 주봉 '
        base = u[2:]
    return prefix + _KO_NAMES.get(base, base)


def _node_to_ko(node) -> str:
    if isinstance(node, ast.BoolOp):
        sep = ' 이고, ' if isinstance(node.op, ast.And) else ' 또는 '
        return sep.join(_node_to_ko(v) for v in node.values)

    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return f"({_node_to_ko(node.operand)}) 아님"
        if isinstance(node.op, ast.USub):
            return f"-{_node_to_ko(node.operand)}"
        return _node_to_ko(node.operand)

    if isinstance(node, ast.Compare):
        op_map = {ast.Lt: '<', ast.LtE: '≤', ast.Gt: '>', ast.GtE: '≥', ast.Eq: '=', ast.NotEq: '≠'}
        parts = [_node_to_ko(node.left)]
        for op, comp in zip(node.ops, node.comparators):
            sym = op_map.get(type(op), '?')
            parts.append(f"{sym} {_node_to_ko(comp)}")
        return " ".join(parts)

    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Mult) and isinstance(node.right, ast.Constant) and isinstance(node.right.value, (int, float)):
            v = float(node.right.value)
            left_txt = _node_to_ko(node.left)
            if 0.5 <= v <= 1.5 and v != 1.0:
                diff = (v - 1.0) * 100.0
                return f"{left_txt}의 {v * 100:.1f}% ({diff:+.1f}%)"
            return f"{left_txt} × {v:g}"
        op_s = {ast.Add: '+', ast.Sub: '-', ast.Mult: '×', ast.Div: '÷'}.get(type(node.op), '?')
        return f"{_node_to_ko(node.left)} {op_s} {_node_to_ko(node.right)}"

    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        fn = node.func.id.upper()
        args_ko = [_node_to_ko(a) for a in node.args]
        if fn == 'CROSSUP' and len(args_ko) >= 2:
            return f"{args_ko[0]} ↗ {args_ko[1]} 상향돌파"
        if fn == 'CROSSDOWN' and len(args_ko) >= 2:
            return f"{args_ko[0]} ↘ {args_ko[1]} 하향돌파"
        if fn == 'REF' and len(args_ko) >= 2:
            return f"{args_ko[1]}봉 전 {args_ko[0]}"
        return f"{_ko_symbol(fn)}({', '.join(args_ko)})"

    if isinstance(node, ast.Name):
        u = node.id.upper()
        if u == 'TRUE':
            return '항상 매수'
        if u == 'FALSE':
            return '매도 안 함'
        return _ko_symbol(u)

    if isinstance(node, ast.Constant):
        v = node.value
        return str(int(v)) if isinstance(v, float) and v == int(v) else str(v)

    return ''


def translate_formula_to_korean(expr: str) -> str:
    """수식을 초보자도 바로 읽을 수 있는 한국어 문장으로 번역"""
    if not expr or not str(expr).strip():
        return '조건 없음 (빈 수식)'
    try:
        tree = ast.parse(normalize(expr), mode='eval')
        return _node_to_ko(tree.body)
    except Exception:
        return '문법을 확인해 주세요'


def convert_formula_timeframe(expr: str, target_tf: str) -> str:
    """수식 내 지표의 타임프레임 일괄 변환 ('D' | 'W' | 'M')"""
    if not expr:
        return expr
    prefix = '' if target_tf == 'D' else ('W_' if target_tf == 'W' else 'M_')

    def _sub(m):
        base = m.group(1).upper()
        return f"{prefix}{base}"

    return _MTF_RE.sub(_sub, expr)


def _candidate_grid(val: float, is_float: bool, kind: str, fn_name: str = ''):
    if kind == 'mult':
        steps = [round(val + d, 3) for d in (-0.03, -0.02, -0.01, 0.0, 0.01, 0.02, 0.03)]
        return tuple(x for x in steps if 0.5 <= x <= 1.5)
    if kind == 'period':
        iv = max(2, int(round(val)))
        if iv <= 10:
            cands = [max(2, iv - 3), max(2, iv - 2), max(2, iv - 1), iv, iv + 2, iv + 4, iv + 7]
        elif iv <= 30:
            cands = [max(3, iv - 10), max(3, iv - 5), iv, iv + 5, iv + 10, iv + 20, iv + 30]
        else:
            cands = sorted({max(5, int(round(iv * r / 5) * 5)) for r in (0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6)})
        return tuple(dict.fromkeys(cands))
    if is_float:
        return tuple(round(val + d, 2) for d in (-1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5) if val + d > 0)
    iv = int(round(val))
    step = 5 if iv >= 15 else 2
    return tuple(max(1, iv + d * step) for d in (-3, -2, -1, 0, 1, 2, 3))


def extract_formula_numbers(expr: str) -> list:
    """수식 내 조정 가능한 숫자 파라미터를 문맥과 함께 추출"""
    if not expr or not str(expr).strip():
        return []
    out = []
    for idx, m in enumerate(_NUM_RE.finditer(expr)):
        raw = m.group()
        is_float = '.' in raw
        val = float(raw) if is_float else int(raw)
        left_ctx = expr[:m.start()].rstrip()

        kind = 'value'
        label = '기준 숫자'
        fn_name = ''
        step = 0.01 if is_float else 1
        min_v = 0.01 if is_float else 1
        max_v = 10000.0 if is_float else 500

        if left_ctx.endswith(('*', '/')) and 0.5 <= float(val) <= 1.5:
            kind = 'mult'
            op_ch = left_ctx[-1]
            label = f"비율 ({op_ch} {raw})"
            min_v, max_v, step = 0.50, 1.50, 0.01
            is_float = True
            val = float(val)
        else:
            fm = re.search(r'([A-Za-z_][A-Za-z0-9_]*)\s*\([^()]*$', left_ctx)
            if fm:
                fn_name = fm.group(1).upper()
                kind = 'period'
                label = f"{_ko_symbol(fn_name)} 기간"
                min_v, max_v, step = 1, 300, 1
            else:
                cm = re.search(r'([A-Za-z_][A-Za-z0-9_]*(?:\([^()]*\))?)\s*(?:<=|>=|<|>|==)\s*$', left_ctx)
                if cm:
                    label = f"{cm.group(1)} 기준값"

        sync_key = f"{kind}:{fn_name or label}:{raw}"
        out.append({
            'index': idx,
            'start': m.start(),
            'end': m.end(),
            'raw': raw,
            'value': val,
            'label': label,
            'kind': kind,
            'fn_name': fn_name,
            'sync_key': sync_key,
            'is_float': is_float,
            'min': min_v,
            'max': max_v,
            'step': step,
            'grid': _candidate_grid(float(val), is_float, kind, fn_name),
        })
    return out


def replace_formula_numbers(expr: str, values: list) -> str:
    """extract_formula_numbers 순서대로 새 값을 수식에 안전하게 치환"""
    tokens = extract_formula_numbers(expr)
    if not tokens or len(tokens) != len(values):
        return expr
    s = expr
    for tok, new_val in reversed(list(zip(tokens, values))):
        if tok['is_float']:
            rep = f"{float(new_val):.4f}".rstrip('0').rstrip('.')
            if '.' not in rep and tok['kind'] == 'mult':
                rep = f"{float(new_val):.2f}"
        else:
            rep = str(int(round(float(new_val))))
        s = s[:tok['start']] + rep + s[tok['end']:]
    return s


def _extract_indicator_calls(expr: str) -> list:
    if not expr or not str(expr).strip():
        return []
    calls = []
    try:
        tree = ast.parse(normalize(expr), mode='eval')
    except Exception:
        return []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            fn = node.func.id.upper()
            if fn in ('CROSSUP', 'CROSSDOWN'):
                continue
            try:
                seg = ast.unparse(node)
                if seg not in calls:
                    calls.append(seg)
            except Exception:
                pass
        elif isinstance(node, ast.Name):
            u = node.id.upper()
            if u not in ('TRUE', 'FALSE', 'AND', 'OR', 'NOT') and u not in calls:
                # 함수명 자체(Call.func)가 아닌 단독 변수만
                calls.append(u)
    # ast.walk는 Call.func의 Name도 방문하므로 함수 이름이 괄호 포함 호출과 중복될 수 있음 → 필터링
    fn_names = {c.split('(')[0].upper() for c in calls if '(' in c}
    cleaned = []
    for c in calls:
        if '(' not in c and c.upper() in fn_names and c.upper() not in ('CLOSE', 'OPEN', 'HIGH', 'LOW', 'VOLUME', 'W_CLOSE', 'M_CLOSE'):
            continue
        if c not in cleaned:
            cleaned.append(c)
    return cleaned[:6]


def analyze_formula_clauses(df: pd.DataFrame, expr: str, start_date=None, end_date=None, mtf_close_only: bool = True) -> dict:
    """복합 수식(AND / OR)의 각 절별 조건 충족 횟수 해부 (병목 조건 X-Ray)"""
    if df is None or df.empty or not expr or not str(expr).strip():
        return {'op': 'EMPTY', 'total_bars': 0, 'combined_count': 0, 'clauses': []}

    try:
        tree = ast.parse(normalize(expr), mode='eval')
    except Exception:
        return {'op': 'INVALID', 'total_bars': 0, 'combined_count': 0, 'clauses': []}

    body = tree.body
    if isinstance(body, ast.BoolOp):
        op_name = 'AND' if isinstance(body.op, ast.And) else 'OR'
        sub_nodes = body.values
    else:
        op_name = 'SINGLE'
        sub_nodes = [body]

    def _restore_kw(s: str) -> str:
        s = re.sub(r'\band\b', 'AND', s)
        s = re.sub(r'\bor\b', 'OR', s)
        s = re.sub(r'\bnot\b', 'NOT', s)
        return s

    tf = uses_timeframe(expr) if mtf_close_only else None
    mask = _period_end_mask(df.index, tf) if tf else pd.Series(True, index=df.index)

    date_mask = pd.Series(True, index=df.index)
    if start_date:
        date_mask &= df.index >= pd.Timestamp(start_date)
    if end_date:
        date_mask &= df.index <= pd.Timestamp(end_date)

    eff_mask = mask & date_mask
    clauses = []
    for node in sub_nodes:
        sub_expr = _restore_kw(ast.unparse(node))
        try:
            sig = to_signal(df, sub_expr) & mask
            sig_slice = sig[date_mask]
            eval_base = sig[eff_mask] if tf else sig_slice
            n_eval = int(len(eval_base))
            cnt = int(sig_slice.sum())
            clauses.append({
                'expr': sub_expr,
                'ko': translate_formula_to_korean(sub_expr),
                'count': cnt,
                'pct': round((cnt / n_eval) * 100.0, 1) if n_eval > 0 else 0.0,
                'today': bool(sig.iloc[-1]) if len(sig) else False,
            })
        except Exception:
            clauses.append({
                'expr': sub_expr,
                'ko': translate_formula_to_korean(sub_expr),
                'count': 0,
                'pct': 0.0,
                'today': False,
            })

    comb = to_signal(df, expr) & mask
    comb_slice = comb[date_mask]
    eval_tot = int(eff_mask.sum()) if tf else int(date_mask.sum())
    return {
        'op': op_name,
        'tf': tf or 'D',
        'total_bars': eval_tot,
        'combined_count': int(comb_slice.sum()),
        'clauses': clauses,
    }


def extract_chart_overlays(entry_expr: str, exit_expr: str) -> dict:
    """수식에서 캔들 차트에 직접 그릴 가격 기준선(MA, BB 등)과 하단 보조지표(RSI, STOCH 등) 추출"""
    PRICE_FUNCS = {'MA', 'SMA', 'EMA', 'WMA', 'BB_UPPER', 'BB_MID', 'BB_LOWER', 'HIGHEST', 'LOWEST', 'VWAP'}
    OSC_FUNCS = {
        'RSI', 'DISPARITY', 'STOCH_K', 'STOCH_D', 'CCI', 'MFI', 'WILLIAMS_R',
        'MACD', 'MACD_SIGNAL', 'MACD_HIST', 'ROC', 'MOMENTUM', 'ADX', 'ATR',
        'FRG_NET', 'INST_NET', 'SMART_NET', 'INDIV_NET', 'FRG_RATIO',
        'VIX', 'US10Y', 'USDKRW', 'FW_PER', 'PER', 'PBR', 'TARGET_UPSIDE',
    }

    price_overlays = []
    oscillators = []

    def _base_name(name: str) -> str:
        u = name.upper()
        if u.startswith(('W_', 'M_')):
            return u[2:]
        return u

    def _collect(expr: str):
        if not expr or not str(expr).strip():
            return
        try:
            tree = ast.parse(normalize(expr), mode='eval')
        except Exception:
            return

        # 1) Compare / BinOp 우변에 MA(60) * 0.99 형태가 있으면 통째로 가격선 오버레이로 추출
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp) and isinstance(node.left, ast.Call) and isinstance(node.left.func, ast.Name):
                b = _base_name(node.left.func.id)
                if b in PRICE_FUNCS:
                    seg = ast.unparse(node)
                    if seg not in price_overlays:
                        price_overlays.append(seg)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                b = _base_name(node.func.id)
                seg = ast.unparse(node)
                if b in PRICE_FUNCS:
                    if seg not in price_overlays:
                        price_overlays.append(seg)
                elif b in OSC_FUNCS:
                    if seg not in oscillators:
                        oscillators.append(seg)
            elif isinstance(node, ast.Name):
                u = node.id.upper()
                b = _base_name(u)
                if b in ('MACD', 'MACD_SIGNAL', 'RSI', 'STOCH_K', 'STOCH_D') and u not in oscillators:
                    # 단독 변수 호출 여부는 부모가 Call이 아닐 때만 의미
                    pass
                elif b in ('FRG_NET', 'INST_NET', 'SMART_NET', 'INDIV_NET', 'FRG_RATIO', 'VIX', 'US10Y', 'USDKRW', 'FW_PER', 'PER', 'PBR', 'TARGET_UPSIDE'):
                    if u not in oscillators and not any(k.startswith(u + '(') for k in oscillators):
                        oscillators.append(u)

    _collect(entry_expr)
    _collect(exit_expr)
    # 중복 기본 MA(5) vs MA(5) * 0.99 모두 유용하므로 최대 6개 가격선, 3개 오실레이터 반환
    return {
        'price_overlays': price_overlays[:6],
        'oscillators': oscillators[:3],
    }


def inspect_formula_live_values(df: pd.DataFrame, entry_expr: str, exit_expr: str,
                                mtf_close_only: bool = True, start_date=None, end_date=None) -> dict:
    """주어진 종목 데이터에서 수식의 신호 발생 횟수, 병목 X-Ray, 최근 거래일 지표 실제값 계산"""
    if df is None or df.empty:
        return {'valid': False, 'error': '데이터가 비어 있습니다.'}

    ok1, m1 = ConditionParser.validate_syntax(entry_expr)
    if not ok1:
        return {'valid': False, 'error': f"매수식 오류: {m1}"}
    ok2, m2 = ConditionParser.validate_syntax(exit_expr)
    if not ok2:
        return {'valid': False, 'error': f"매도식 오류: {m2}"}

    try:
        en = to_signal(df, entry_expr)
        ex = to_signal(df, exit_expr) if exit_expr and str(exit_expr).strip() else pd.Series(False, index=df.index)
        tf = uses_timeframe(f"{entry_expr} {exit_expr}") if mtf_close_only else None
        if tf:
            mask = _period_end_mask(df.index, tf)
            en, ex = en & mask, ex & mask

        en_dates = df.index[en]
        ex_dates = df.index[ex]
        last_dt = df.index[-1]

        parser = ConditionParser(df)
        ind_vals = {}
        for expr_part in (entry_expr, exit_expr):
            for tok in _extract_indicator_calls(expr_part):
                if tok in ind_vals:
                    continue
                try:
                    s = parser.evaluate(tok)
                    if isinstance(s, pd.Series) and not s.empty:
                        v = s.iloc[-1]
                        if pd.notna(v):
                            ind_vals[tok] = float(v)
                except Exception:
                    pass

        return {
            'valid': True,
            'error': '',
            'last_date': last_dt.strftime('%Y-%m-%d'),
            'last_close': float(df['Close'].iloc[-1]),
            'entry_count': int(en.sum()),
            'exit_count': int(ex.sum()),
            'last_entry_date': en_dates[-1].strftime('%Y-%m-%d') if len(en_dates) > 0 else '없음',
            'last_exit_date': ex_dates[-1].strftime('%Y-%m-%d') if len(ex_dates) > 0 else '없음',
            'entry_today': bool(en.iloc[-1]),
            'exit_today': bool(ex.iloc[-1]),
            'indicator_values': ind_vals,
            'entry_xray': analyze_formula_clauses(df, entry_expr, start_date=start_date, end_date=end_date, mtf_close_only=mtf_close_only),
            'exit_xray': analyze_formula_clauses(df, exit_expr, start_date=start_date, end_date=end_date, mtf_close_only=mtf_close_only) if exit_expr and str(exit_expr).strip() else {'op': 'EMPTY', 'clauses': []},
        }
    except Exception as e:
        return {'valid': False, 'error': str(e)}


def append_formula_block(current_expr: str, block: str, join_op: str = 'AND') -> str:
    """기존 수식에 조건 조각을 깔끔하게 추가 또는 덮어쓰기"""
    cur = (current_expr or '').strip()
    blk = (block or '').strip()
    if not blk:
        return cur
    if join_op == 'REPLACE' or not cur or cur.upper() in ('TRUE', 'FALSE'):
        return blk
    op = 'OR' if join_op == 'OR' else 'AND'
    return f"{cur} {op} {blk}"


def build_formula_snippets(tf_prefix: str = '') -> dict:
    """타임프레임 접두사('', 'W_', 'M_')별 원클릭 수식 조각 팔레트"""
    p = tf_prefix
    return {
        '📈 이평선 · 눌림목': [
            ("종가 > 20선", f"{p}CLOSE > {p}MA(20)", "20선 위 상승추세 유지"),
            ("종가 ≤ 60선 -1%", f"{p}CLOSE <= {p}MA(60) * 0.99", "60선보다 1% 아래 깊은 눌림"),
            ("종가 ≤ 5선 -1%", f"{p}CLOSE <= {p}MA(5) * 0.99", "5선보다 1% 아래 단기 이탈"),
            ("5선 > 10선", f"{p}MA(5) > {p}MA(10)", "단기 이평선이 중기선 위"),
            ("완전 정배열", f"{p}MA(5) > {p}MA(20) AND {p}MA(20) > {p}MA(60)", "5 > 20 > 60 정배열"),
            ("골든크로스 5↗20", f"CROSSUP({p}MA(5), {p}MA(20))", "5선이 20선 상향 돌파"),
            ("데드크로스 5↘20", f"CROSSDOWN({p}MA(5), {p}MA(20))", "5선이 20선 하향 돌파"),
        ],
        '🎯 과매도 · 역추세': [
            ("RSI < 30 과매도", f"{p}RSI(14) < 30", "너무 많이 떨어졌을 때"),
            ("RSI > 70 과매수", f"{p}RSI(14) > 70", "단기 과열 구간 도달"),
            ("볼린저 하단 이탈", f"{p}CLOSE < {p}BB_LOWER(20, 2)", "볼린저 밴드 하단 아래"),
            ("볼린저 상단 돌파", f"{p}CLOSE > {p}BB_UPPER(20, 2)", "볼린저 밴드 상단 위"),
            ("이격도 95 미만", "DISPARITY(20) < 95", "20일선 대비 -5% 이상 과락"),
        ],
        '🚀 돌파 · 모멘텀': [
            ("20봉 신고가 돌파", f"{p}CLOSE > {p}HIGHEST(20)", "직전 20봉 최고가 돌파"),
            ("10봉 신저가 이탈", f"{p}CLOSE < {p}LOWEST(10)", "직전 10봉 최저가 깨짐"),
            ("MACD 골든크로스", f"CROSSUP({p}MACD(12,26,9), {p}MACD_SIGNAL(12,26,9))", "MACD 시그널 상향돌파"),
            ("MACD 데드크로스", f"CROSSDOWN({p}MACD(12,26,9), {p}MACD_SIGNAL(12,26,9))", "MACD 시그널 하향돌파"),
        ],
        '🕯️ 캔들 · 거래량': [
            ("양봉 (종가>시가)", f"{p}BULLISH", "양봉 마감"),
            ("음봉 (종가<시가)", f"{p}BEARISH", "음봉 마감"),
            ("전봉 대비 상승", f"{p}CLOSE > REF({p}CLOSE, 1)", "직전 봉 종가보다 높음"),
            ("거래량 2배 급증", "VOLUME > VOLUME(20) * 2", "20일 평균 거래량의 2배 이상"),
        ],
        '🏦 수급 · 밸류 · 매크로': [
            ("외인+기관 5일 순매수", "SMART_NET(5) > 0", "[한국] 외국인+기관 5일 누적 순매수"),
            ("외국인 지분율 증가", "FRG_RATIO > REF(FRG_RATIO, 20)", "[한국] 20일 전보다 외국인 지분율 상승"),
            ("선행 PER 저평가(≤15)", "FW_PER > 0 AND FW_PER <= 15", "컨센서스 Forward PER 15배 이하"),
            ("목표가 괴리 15%↑", "TARGET_UPSIDE >= 15", "애널리스트 평균 목표가 대비 +15% 이상 여력"),
            ("VIX 공포 진정(<25)", "VIX < 25", "공포지수(VIX) 25 미만 안정 구간"),
            ("미 10년물 금리 안정(<4.5%)", "US10Y < 4.5", "미국 국채 10년물 금리 4.5% 미만"),
        ],
    }
