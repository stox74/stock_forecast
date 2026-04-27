# ─────────────────────────────────────────────────────────────────
# Cell 5 · 배치 실행  (★ EXPORT_EXCEL 옵션 추가)
# ─────────────────────────────────────────────────────────────────

# ★ NEW: 배치 실행 시 ticker 별 Excel 파일 자동 생성 옵션 ────────
EXPORT_EXCEL     = False                                         # ← True 로 바꾸면 활성화
EXPORT_DIR_BATCH = (Path(r"C:/reports/batch") if os.name == "nt"
                    else Path.home() / "reports" / "batch")
if EXPORT_EXCEL:
    EXPORT_DIR_BATCH.mkdir(parents=True, exist_ok=True)
    log("BATCH", f"EXPORT_EXCEL=True → 각 ticker 별 Excel 생성: {EXPORT_DIR_BATCH}")
# ──────────────────────────────────────────────────────────────────

RUN_TICKERS = US_TICKER_LIST[TICKER_START:TICKER_END]
total   = len(RUN_TICKERS)
run_date = datetime.now().strftime("%Y-%m-%d")

done_set = set()
if SKIP_DONE and os.path.exists(DONE_PATH):
    with open(DONE_PATH) as f:
        done_set = {l.strip() for l in f if l.strip()}

ok_cnt = skip_cnt = fail_cnt = 0
results = []
t0 = time.time()

log("BATCH", f"배치 시작: {total:,}개  run_date={run_date}  SKIP_DONE={SKIP_DONE}  EXPORT_EXCEL={EXPORT_EXCEL}")
print("=" * 70)

for idx, ticker in enumerate(RUN_TICKERS, 1):
    pct    = idx / total * 100
    prefix = f"[{idx:>5}/{total}] ({pct:5.1f}%) {ticker:<8}"

    if SKIP_DONE and ticker in done_set:
        print(f"{prefix} SKIP (checkpoint)", flush=True)
        skip_cnt += 1
        continue

    # ★ EXPORT_EXCEL 분기 ─────────────────────────────────────────
    if EXPORT_EXCEL:
        # 인라인 처리 — model 인스턴스를 보존해서 export_v10_excel 호출
        try:
            model = DCFModel(ticker=ticker, engine=engine, verbose=False)
            model.run()
            rows = model.save_to_db(run_date)

            # Excel 저장은 try/except 로 감싸 — 실패해도 batch 진행
            try:
                model.export_v10_excel(out_dir=EXPORT_DIR_BATCH)
                excel_tag = " +xlsx"
            except Exception as e_x:
                excel_tag = f" (xlsx fail: {str(e_x)[:40]})"

            v = model.valuation
            res = {
                "status": "ok", "ticker": ticker,
                "target_price": v.get("target_price", np.nan),
                "upside_pct":   v.get("upside_pct", np.nan),
                "wacc":         v.get("wacc", np.nan),
                "g_terminal":   v.get("g_terminal", np.nan),
                "rows_saved":   rows,
                "msg":          f"TP={v.get('target_price', np.nan):.2f}{excel_tag}",
            }
        except Exception as e:
            res = {
                "status": "fail", "ticker": ticker,
                "target_price": np.nan, "upside_pct": np.nan,
                "wacc": np.nan, "g_terminal": np.nan,
                "rows_saved": 0, "msg": str(e)[:120],
            }
        finally:
            clear_memory()
    else:
        # 기존 경로 — process_one_ticker 그대로 사용
        res = process_one_ticker(ticker, engine, verbose=False,
                                  run_date=run_date, save_db=True)

    results.append(res)

    if res["status"] == "ok":
        tp  = res["target_price"]
        up  = res["upside_pct"]
        w   = res["wacc"]
        tp_str = f"TP=${tp:.2f}" if not np.isnan(tp) else "TP=N/A"
        up_str = f"↑{up:.1f}%" if not np.isnan(up) else ""
        suffix = res["msg"].split(" ", 1)[1] if " " in res["msg"] and EXPORT_EXCEL else ""
        print(f"{prefix} OK  {tp_str} {up_str}  WACC={w:.3f} {suffix}", flush=True)
        with open(DONE_PATH, "a") as f:
            f.write(ticker + "\n")
        ok_cnt += 1
    else:
        print(f"{prefix} FAIL  {res['msg']}", flush=True)
        with open(FAIL_PATH, "a") as f:
            f.write(ticker + "\n")
        fail_cnt += 1

elapsed = time.time() - t0
print("=" * 70)
log("BATCH", f"완료  OK={ok_cnt}  SKIP={skip_cnt}  FAIL={fail_cnt}  "
             f"경과={elapsed:.0f}s  평균={elapsed/max(ok_cnt+fail_cnt,1):.1f}s/ticker")

# 결과 요약
if results:
    summary = pd.DataFrame(results)
    summary = summary[summary["status"]=="ok"].sort_values("upside_pct", ascending=False)
    print("\n[상위 업사이드 종목 TOP 20]")
    display(summary[["ticker","target_price","upside_pct","wacc","g_terminal"]].head(20))
