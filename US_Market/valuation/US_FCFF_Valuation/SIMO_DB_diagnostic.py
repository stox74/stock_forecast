# ═══════════════════════════════════════════════════════════════════
# SIMO DB 매출 데이터 진단 스크립트
# ───────────────────────────────────────────────────────────────────
# 목적:
#   us_revenue_forecast_data 테이블에 저장된 SIMO 의 매출 actual/forecast
#   데이터를 다각도로 추출 → FCFF 노트북이 어느 값을 읽어 들이는지 검증
#
# 실행:
#   별도 .py 파일로 저장해서 실행하거나, Jupyter 셀에 붙여넣어 실행.
#   (engine 자동 생성 — 별도 환경 setup 불필요)
# ═══════════════════════════════════════════════════════════════════

import os
import sys
from pathlib import Path

# ── 경로 자동 감지 ────────────────────────────────────────────────
_CANDIDATE_ROOTS = [
    r"C:\Users\Hoyoung_Park\PyCharmMiscProject\stock_forecast",
    r"C:\Users\82108\OneDrive\바탕 화면\investment\investment_strategy",
]
for cand in _CANDIDATE_ROOTS:
    if os.path.isdir(os.path.join(cand, "DATA")):
        if cand not in sys.path:
            sys.path.insert(0, cand)
        break

import pandas as pd
from sqlalchemy import text
from DATA.config import get_db_info, get_engine

pd.set_option("display.max_rows", 200)
pd.set_option("display.float_format", lambda x: f"{x:,.0f}" if abs(x) >= 1 else f"{x}")
pd.set_option("display.width", 200)

engine = get_engine(get_db_info())

TICKER = "SIMO"
TABLE  = "us_revenue_forecast_data"


def section(title: str):
    print("\n" + "═" * 80)
    print(f"  {title}")
    print("═" * 80)


# ═══════════════════════════════════════════════════════════════════
# [1] forecast_date 별 행 수 통계 — 어느 batch 가 SIMO 를 다뤘는지
# ═══════════════════════════════════════════════════════════════════
section("[1] forecast_date 별 SIMO 행 수")

with engine.connect() as conn:
    df1 = pd.read_sql(text(f"""
        SELECT forecast_date,
               data_type,
               COUNT(*) AS n_rows,
               COUNT(DISTINCT date) AS n_dates,
               COUNT(DISTINCT model) AS n_models
        FROM   `{TABLE}`
        WHERE  ticker = :tk AND item = 'sale'
        GROUP  BY forecast_date, data_type
        ORDER  BY forecast_date DESC, data_type
    """), conn, params={"tk": TICKER})

print(df1.to_string(index=False))


# ═══════════════════════════════════════════════════════════════════
# [2] SIMO actual 데이터 — 모든 forecast_date × 모든 date 매트릭스
# ═══════════════════════════════════════════════════════════════════
section("[2] SIMO actual — date × forecast_date 매트릭스 (단위: $M)")

with engine.connect() as conn:
    df2 = pd.read_sql(text(f"""
        SELECT date, forecast_date, value
        FROM   `{TABLE}`
        WHERE  ticker = :tk AND item = 'sale' AND data_type = 'actual'
        ORDER  BY date DESC, forecast_date DESC
    """), conn, params={"tk": TICKER})

if df2.empty:
    print("[!] SIMO actual 데이터 없음")
else:
    df2["value_M"] = (df2["value"] / 1e6).round(1)
    pivot = df2.pivot_table(
        index="date", columns="forecast_date",
        values="value_M", aggfunc="first",
    ).sort_index(ascending=False)
    print(f"총 {len(df2)}개 행, date {df2['date'].nunique()}개 × forecast_date {df2['forecast_date'].nunique()}개")
    print(pivot.head(15).to_string())


# ═══════════════════════════════════════════════════════════════════
# [3] SIMO 2026-03-31 actual 의 모든 행 — 핵심 검증 포인트
# ═══════════════════════════════════════════════════════════════════
section("[3] SIMO 2026-03-31 actual 의 전체 행")

with engine.connect() as conn:
    df3 = pd.read_sql(text(f"""
        SELECT date, model, value, forecast_date, period, created_at
        FROM   `{TABLE}`
        WHERE  ticker = :tk AND item = 'sale' AND data_type = 'actual'
          AND  date = '2026-03-31'
        ORDER  BY forecast_date DESC, created_at DESC
    """), conn, params={"tk": TICKER})

if df3.empty:
    print("[!] 2026-03-31 actual 행 없음 → 매출 예측이 SIMO 를 처리하지 못한 상태")
else:
    df3["value_M"] = (df3["value"] / 1e6).round(2).astype(str) + "M"
    print(df3.to_string(index=False))

    # 정답 확인
    print()
    correct = (df3["value"] - 342_100_000).abs() < 1000
    wrong   = (df3["value"] - 278_461_000).abs() < 1000
    n_correct = correct.sum()
    n_wrong   = wrong.sum()
    n_other   = len(df3) - n_correct - n_wrong

    print(f"  ✅ 정답 ($342.1M)        : {n_correct}행")
    print(f"  ❌ 잘못됨 ($278.5M)      : {n_wrong}행")
    print(f"  ⚠ 기타                  : {n_other}행")


# ═══════════════════════════════════════════════════════════════════
# [4] FCFF v12 의 load_sales SQL 을 동일하게 실행 — 실제 입력 검증
# ═══════════════════════════════════════════════════════════════════
section("[4] FCFF v12 load_sales SQL 시뮬레이션 — 실제 FCFF 가 읽는 값")

with engine.connect() as conn:
    df4 = pd.read_sql(text(f"""
        SELECT a.date, a.data_type, a.model, a.value, a.forecast_date
        FROM   `{TABLE}` a
        INNER JOIN (
            SELECT date, MAX(forecast_date) AS max_fd
            FROM   `{TABLE}`
            WHERE  ticker = :tk AND item = 'sale' AND data_type = 'actual'
            GROUP  BY date
        ) b ON a.date = b.date AND a.forecast_date = b.max_fd
        WHERE  a.ticker = :tk AND a.item = 'sale' AND a.data_type = 'actual'
        ORDER  BY a.date DESC
    """), conn, params={"tk": TICKER})

if df4.empty:
    print("[!] 결과 없음")
else:
    df4["value_M"] = (df4["value"] / 1e6).round(2)
    print(f"FCFF v12 load_sales 가 가져갈 actual 행 수: {len(df4)}")
    print(f"\n최근 8분기:")
    print(df4.head(8)[["date","forecast_date","value_M"]].to_string(index=False))

    # 2026Q1 검증
    q1_2026 = df4[df4["date"] == pd.Timestamp("2026-03-31")]
    if not q1_2026.empty:
        v = float(q1_2026["value"].iloc[0])
        print(f"\n  → FCFF 가 2026Q1 actual 로 읽을 값: ${v/1e6:,.1f}M")
        if abs(v - 342_100_000) < 1000:
            print(f"  ✅ 정상 — override 정상 반영됨")
        elif abs(v - 278_461_000) < 1000:
            print(f"  ❌ 옛 값 그대로 — override 미반영")
        else:
            print(f"  ⚠ 예상 외 값")


# ═══════════════════════════════════════════════════════════════════
# [5] v11.5 (옛 SQL) 시뮬레이션 — 비교
# ═══════════════════════════════════════════════════════════════════
section("[5] v11.5 옛 SQL 시뮬레이션 (비결정적, drop_duplicates keep='last')")

with engine.connect() as conn:
    df5 = pd.read_sql(text(f"""
        SELECT date, data_type, model, value, forecast_date
        FROM   `{TABLE}`
        WHERE  ticker = :tk AND item = 'sale' AND data_type = 'actual'
        ORDER  BY date
    """), conn, params={"tk": TICKER})

if df5.empty:
    print("[!] 결과 없음")
else:
    # v11.5 의 처리 방식 모사
    act = (df5.sort_values("date")
              .drop_duplicates("date", keep="last")
              .set_index("date")["value"])
    last_q = act.tail(8)
    last_q_M = (last_q / 1e6).round(2)
    print(f"v11.5 처리 결과 (drop_duplicates keep='last') 최근 8분기:")
    for d, v in last_q_M.items():
        print(f"  {d.date()} : ${v:,.2f}M")

    # 2026Q1
    if pd.Timestamp("2026-03-31") in act.index:
        v = float(act.loc[pd.Timestamp("2026-03-31")])
        print(f"\n  → v11.5 가 2026Q1 actual 로 읽을 값: ${v/1e6:,.1f}M")


# ═══════════════════════════════════════════════════════════════════
# [6] SIMO forecast 데이터 검토 (가장 최신)
# ═══════════════════════════════════════════════════════════════════
section("[6] SIMO forecast 가장 최신 forecast_date (Ensemble & SARIMA)")

with engine.connect() as conn:
    max_fd = pd.read_sql(text(f"""
        SELECT MAX(forecast_date) AS max_fd
        FROM   `{TABLE}`
        WHERE  ticker = :tk AND item = 'sale' AND data_type = 'forecast'
    """), conn, params={"tk": TICKER})["max_fd"].iloc[0]

print(f"최신 forecast_date: {max_fd}")

if max_fd:
    with engine.connect() as conn:
        df6 = pd.read_sql(text(f"""
            SELECT date, model, value
            FROM   `{TABLE}`
            WHERE  ticker = :tk AND item = 'sale' AND data_type = 'forecast'
              AND  forecast_date = :fd
              AND  model IN ('Ensemble', 'SARIMA', 'ETS', 'Theta')
            ORDER  BY date, model
        """), conn, params={"tk": TICKER, "fd": str(max_fd)})

    df6["value_M"] = (df6["value"] / 1e6).round(2)
    pivot = df6.pivot_table(index="date", columns="model", values="value_M", aggfunc="first")
    print(f"\nForecast 값 ($M):")
    print(pivot.to_string())


# ═══════════════════════════════════════════════════════════════════
# [7] 종합 진단
# ═══════════════════════════════════════════════════════════════════
section("[7] 종합 진단")

if not df3.empty:
    n_correct = ((df3["value"] - 342_100_000).abs() < 1000).sum()
    n_wrong = ((df3["value"] - 278_461_000).abs() < 1000).sum()

    if n_correct == len(df3):
        print("✅ DB 의 모든 SIMO 2026-03-31 actual 행 = $342.1M (정상)")
        print("   → FCFF v12 가 올바른 값을 읽어야 정상")
        print("   → 그래도 FCFF 결과가 $278.5M 이라면 FCFF 노트북 캐시 문제일 수 있음")
        print("   → FCFF 커널 재시작 후 전체 셀 재실행 권장")
    elif n_wrong == len(df3):
        print("❌ DB 의 모든 SIMO 2026-03-31 actual 행 = $278.5M (override 미반영)")
        print("   → sync_overrides_to_db(engine) 가 실행되지 않았거나 작동 안 함")
    elif n_correct > 0 and n_wrong > 0:
        print(f"⚠ 혼재: 정답 {n_correct}행, 옛값 {n_wrong}행")
        print(f"   → v12 의 max(forecast_date) JOIN 이 정상이라면 정답 값이 사용됨")
        print(f"   → [4] 결과 확인하여 검증")
else:
    print("❌ DB 에 SIMO 2026-03-31 actual 행 자체가 없음")
    print("   → 매출 예측이 SIMO 에 대해 실행되지 않았음")

print("\n" + "═" * 80)
