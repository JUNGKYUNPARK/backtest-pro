"""
AST 기반 안전한 조건식 파서
- 문자열 수식 → pandas Boolean/float Series
- eval() 미사용 (허용된 노드/지표만 실행)
"""
import ast
import re
import difflib
import pandas as pd

from .indicators import INDICATOR_MAP, SERIES_FUNCS

_LOGIC_RE = [
    (re.compile(r'\bAND\b', re.I), ' and '),
    (re.compile(r'\bOR\b', re.I), ' or '),
    (re.compile(r'\bNOT\b', re.I), ' not '),
    (re.compile(r'그리고'), ' and '),
    (re.compile(r'또는'), ' or '),
]
_RESERVED = {'TRUE', 'FALSE'}


def normalize(expr: str) -> str:
    if expr is None:
        return ''
    s = str(expr).replace('\n', ' ').replace('\r', ' ').strip()
    s = s.replace('≥', '>=').replace('≤', '<=').replace('×', '*').replace('÷', '/')
    # '=' 단독은 '=='로 (>=, <=, ==, != 제외)
    s = re.sub(r'(?<![<>=!])=(?!=)', '==', s)
    for rx, rep in _LOGIC_RE:
        s = rx.sub(rep, s)
    return re.sub(r'\s+', ' ', s).strip()


def _suggest(name):
    m = difflib.get_close_matches(name.upper(), list(INDICATOR_MAP) + list(_RESERVED), n=3, cutoff=0.5)
    return f" → 혹시 {', '.join(m)}?" if m else ""


class ConditionParser:
    def __init__(self, df):
        self.df = df
        self._cache = {}

    # ───────────── 문법 검증 ─────────────
    @staticmethod
    def validate_syntax(expression_str):
        if not expression_str or not str(expression_str).strip():
            return True, "빈 수식 (항상 참)"
        expr = normalize(expression_str)
        try:
            tree = ast.parse(expr, mode='eval')
        except SyntaxError as e:
            pointer = " " * max((e.offset or 1) - 1, 0) + "^"
            return False, f"괄호나 연산자를 확인하세요.\n{expr}\n{pointer}"

        errors = []
        allowed = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.UAdd,
                   ast.Compare, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq,
                   ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div,
                   ast.Call, ast.Name, ast.Load, ast.Constant)
        for node in ast.walk(tree):
            if not isinstance(node, allowed):
                errors.append(f"허용되지 않는 문법: {type(node).__name__}")
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name):
                    errors.append("함수 호출 형태가 올바르지 않습니다")
                elif node.func.id.upper() not in INDICATOR_MAP:
                    errors.append(f"지원하지 않는 지표: '{node.func.id}'{_suggest(node.func.id)}")
            elif isinstance(node, ast.Name):
                n = node.id.upper()
                if n not in INDICATOR_MAP and n not in _RESERVED:
                    errors.append(f"알 수 없는 이름: '{node.id}'{_suggest(node.id)}")
            elif isinstance(node, ast.Constant) and not isinstance(node.value, (int, float, bool)):
                errors.append(f"숫자만 사용할 수 있습니다: {node.value!r}")
        if errors:
            # 중복 제거
            seen, uniq = set(), []
            for e in errors:
                if e not in seen:
                    seen.add(e)
                    uniq.append(e)
            return False, "\n".join(f"• {e}" for e in uniq)
        return True, "올바른 수식입니다"

    # ───────────── 실행 ─────────────
    def evaluate(self, expression_str):
        if not expression_str or not str(expression_str).strip():
            return pd.Series(True, index=self.df.index)
        expr = normalize(expression_str)
        try:
            tree = ast.parse(expr, mode='eval')
            return self._eval(tree.body)
        except Exception as e:
            raise ValueError(f"수식 실행 오류 ({expression_str}): {e}")

    @staticmethod
    def _to_bool(v):
        if isinstance(v, pd.Series):
            return v.fillna(0).astype(bool)
        return bool(v)

    def _eval(self, node):
        if isinstance(node, ast.BoolOp):
            vals = [self._to_bool(self._eval(v)) for v in node.values]
            res = vals[0]
            for v in vals[1:]:
                res = (res & v) if isinstance(node.op, ast.And) else (res | v)
            return res

        if isinstance(node, ast.UnaryOp):
            v = self._eval(node.operand)
            if isinstance(node.op, ast.Not):
                b = self._to_bool(v)
                return ~b if isinstance(b, pd.Series) else (not b)
            if isinstance(node.op, ast.USub):
                return -v
            return v

        if isinstance(node, ast.Compare):
            left = self._eval(node.left)
            result = None
            for op, comp in zip(node.ops, node.comparators):
                right = self._eval(comp)
                if isinstance(op, ast.Lt):
                    r = left < right
                elif isinstance(op, ast.LtE):
                    r = left <= right
                elif isinstance(op, ast.Gt):
                    r = left > right
                elif isinstance(op, ast.GtE):
                    r = left >= right
                elif isinstance(op, ast.Eq):
                    r = left == right
                else:
                    r = left != right
                result = r if result is None else (result & r)
                left = right
            return result

        if isinstance(node, ast.BinOp):
            a, b = self._eval(node.left), self._eval(node.right)
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            if isinstance(node.op, ast.Mult):
                return a * b
            if isinstance(node.op, ast.Div):
                if isinstance(b, pd.Series):
                    b = b.replace(0, float('nan'))
                elif b == 0:
                    return float('nan')
                return a / b
            raise TypeError("지원하지 않는 연산자")

        if isinstance(node, ast.Call):
            name = node.func.id.upper()
            if name not in INDICATOR_MAP:
                raise NameError(f"지원하지 않는 지표: {name}")
            key = ast.dump(node)
            if key in self._cache:
                return self._cache[key]
            args = [self._eval(a) for a in node.args]
            if name not in SERIES_FUNCS:
                # 숫자 인자만 허용
                args = [a for a in args]
            res = INDICATOR_MAP[name](self.df, *args)
            self._cache[key] = res
            return res

        if isinstance(node, ast.Name):
            name = node.id.upper()
            if name == 'TRUE':
                return pd.Series(True, index=self.df.index)
            if name == 'FALSE':
                return pd.Series(False, index=self.df.index)
            if name in INDICATOR_MAP:
                if name in self._cache:
                    return self._cache[name]
                res = INDICATOR_MAP[name](self.df)
                self._cache[name] = res
                return res
            raise NameError(f"알 수 없는 이름: {node.id}")

        if isinstance(node, ast.Constant):
            return node.value

        raise TypeError(f"지원하지 않는 수식 형태: {type(node).__name__}")


def evaluate_condition(df, expression_str):
    return ConditionParser(df).evaluate(expression_str)


def to_signal(df, expression_str):
    """수식 → bool Series (NaN=False)"""
    v = evaluate_condition(df, expression_str)
    if not isinstance(v, pd.Series):
        v = pd.Series(bool(v), index=df.index)
    return v.reindex(df.index).fillna(0).astype(bool)


def uses_timeframe(expr):
    """수식이 주봉/월봉 지표를 쓰는지 → ('M' | 'W' | None)"""
    s = (expr or '').upper()
    if re.search(r'\bM_[A-Z]', s):
        return 'M'
    if re.search(r'\bW_[A-Z]', s):
        return 'W'
    return None
