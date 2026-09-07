# ==========================================================
# FMP_fs_analyzer_v1_latest_patch.py
# 기업별 "가장 최근 발표된" 분기 기준 YoY / QoQ 스크리너
#
# 기존 F.yoy_screen / F.qoq_screen 의 문제:
#   - asof=None 이면 전 종목 공통의 '완전 적재 분기'를 찾아 뒤로 후퇴
#     -> 어닝시즌 중간(부분 적재)이면 이미 2026Q2 를 발표한 기업까지
#        2026Q1 vs 2025Q1 로 비교됨
# 이 패치:
#   - 종목별로 자기 자신의 최신 분기 t 를 잡고 t-4(YoY) / t-1(QoQ) 매칭
#   - max_lag 로 오래 전에 멈춘 종목(상장폐지/피인수 등) 제외
#   - fx_guard 로 보고통화 변경(YPF ARS, TKC TRY 등) 의심 종목 필터
#
# 사용법 (노트북에서):
#   import FMP_fs_analyzer_v1_latest_patch as FL
#   FL.asof_coverage(panel)                                  # 분기별 커버리지 진단
#   FL.latest_screen(panel, '매출액',  mode='yoy', n=TOP_N, unit=UNIT, min_base=1e7)
#   FL.latest_screen(panel, '영업이익', mode='qoq', n=TOP_N, unit=UNIT)
# ==========================================================
import numpy as np
import pandas as pd


# ---------- 내부: 패널 컬럼 자동 탐지 ----------
def _detect_cols(panel):
    cols = set(panel.columns)
    need = {'ticker', 'q', 'concept'}
    if not need.issubset(cols):
        raise ValueError(f'panel 에 {need - cols} 컬럼이 없습니다. columns={list(panel.columns)}')
    val_col = None
    for c in ('value', 'val', 'amount', 'amt'):
        if c in cols:
            val_col = c
            break
    if val_col is None:
        num = [c for c in panel.columns
               if c not in ('ticker', 'q', 'concept', 'company_name', 'date', 'sj_div', 'item')
               and pd.api.types.is_numeric_dtype(panel[c])]
        if len(num) != 1:
            raise ValueError(f'값 컬럼을 특정할 수 없습니다. 후보={num}')
        val_col = num[0]
    name_col = 'company_name' if 'company_name' in cols else None
    date_col = 'date' if 'date' in cols else None
    return val_col, name_col, date_col


def _wide(panel, concept, val_col):
    sub = panel.loc[panel['concept'] == concept]
    if sub.empty:
        raise ValueError(f"concept '{concept}' 이(가) panel 에 없습니다.")
    w = (sub.pivot_table(index='ticker', columns='q', values=val_col, aggfunc='last')
            .sort_index(axis=1))
    return w


# ---------- 진단: 분기별 적재 커버리지 ----------
def asof_coverage(panel, concept='매출액', tail=8):
    """분기별로 값이 있는 종목 수 / 비율. 자동 asof 가 왜 뒤로 후퇴했는지 확인용."""
    val_col, _, _ = _detect_cols(panel)
    w = _wide(panel, concept, val_col)
    total = w.shape[0]
    cov = w.notna().sum(axis=0).to_frame('n_ticker')
    cov['coverage_%'] = (cov['n_ticker'] / total * 100).round(1)
    return cov.tail(tail)


# ---------- 보고통화 변경 의심 탐지 ----------
def _fx_suspect(row_series, lookback=6, jump=8.0):
    """최근 lookback 분기 내 인접 분기 |값| 비율이 jump 배를 넘으면 통화변경/재표시 의심."""
    s = row_series.dropna().astype(float).abs()
    s = s[s > 0].tail(lookback)
    if len(s) < 2:
        return False
    ratio = (s / s.shift(1)).dropna()
    return bool(((ratio > jump) | (ratio < 1.0 / jump)).any())


# ---------- 메인: 종목별 최신 분기 기준 스크리너 ----------
def latest_screen(panel, concept, mode='yoy', n=100, unit=1e6, min_base=None,
                  max_lag=1, fx_guard=True, fx_jump=8.0, ascending=False):
    """
    종목별 최신 분기 t 기준 YoY(t vs t-4) / QoQ(t vs t-1) 상위 N.

    max_lag  : 전체 최신 분기(global max q) 대비 허용 지연 분기 수.
               1 이면 '전체 최신 분기' 또는 '그 직전 분기'가 최신인 종목만 포함
               (상장폐지·피인수로 데이터가 멈춘 종목 제거).
    min_base : 기준값(분모) 하한, USD 원단위 (1e7 = $10M).
    fx_guard : 최근 분기 값이 통째로 수배~수십 배 점프한 종목(보고통화 변경 의심) 제외.
    """
    assert mode in ('yoy', 'qoq')
    lag = 4 if mode == 'yoy' else 1

    val_col, name_col, date_col = _detect_cols(panel)
    w = _wide(panel, concept, val_col)          # index=ticker, columns=q (오름차순)
    qs = list(w.columns)
    q_pos = {q: i for i, q in enumerate(qs)}
    global_max = qs[-1]

    # 종목별 최신 유효 분기
    notna = w.notna()
    last_q = notna.apply(lambda r: r[r].index[-1] if r.any() else pd.NaT, axis=1)

    rows = []
    for tkr, tq in last_q.items():
        if pd.isna(tq):
            continue
        # 전체 최신 분기 대비 지연 필터 (멈춘 종목 제거)
        if (global_max - tq).n > max_lag:
            continue
        i = q_pos[tq]
        if i - lag < 0:
            continue
        bq = qs[i - lag]
        t_val = w.at[tkr, tq]
        b_val = w.at[tkr, bq]
        if pd.isna(t_val) or pd.isna(b_val):
            continue
        if min_base is not None and abs(b_val) < min_base:
            continue
        if b_val == 0:
            continue
        if fx_guard and _fx_suspect(w.loc[tkr], jump=fx_jump):
            continue
        growth = (t_val - b_val) / abs(b_val) * 100
        rows.append((tkr, bq, tq, b_val / unit, t_val / unit, growth))

    tag = 't-4' if mode == 'yoy' else 't-1'
    out = pd.DataFrame(rows, columns=['ticker', 'base_q', 't_q',
                                      f'{concept}({tag})', f'{concept}(t)', 'growth_%'])

    # 기업명 매핑
    if name_col:
        nm = (panel[['ticker', name_col]].dropna().drop_duplicates('ticker')
              .set_index('ticker')[name_col])
        out.insert(1, 'company_name', out['ticker'].map(nm).fillna(''))

    out = (out.sort_values('growth_%', ascending=ascending)
              .head(n).reset_index(drop=True))
    out.attrs['global_max_q'] = str(global_max)
    return out


def turnaround_latest(panel, mode='yoy', unit=1e6, max_lag=1, fx_guard=True):
    """종목별 최신 분기 기준 영업이익 흑자전환 (base<0 → t>0)."""
    scr = latest_screen(panel, '영업이익', mode=mode, n=10**9, unit=unit,
                        min_base=None, max_lag=max_lag, fx_guard=fx_guard)
    tag = 't-4' if mode == 'yoy' else 't-1'
    b, t = f'영업이익({tag})', '영업이익(t)'
    out = scr[(scr[b] < 0) & (scr[t] > 0)].copy()
    out['swing'] = out[t] - out[b]
    return out.sort_values('swing', ascending=False).reset_index(drop=True)
