# ═══════════════════════════════════════════════════════════════════════
#  Cell 5.5 (NEW)  ·  단일 ticker → 즉시 V10 형식 Excel 출력
# ═══════════════════════════════════════════════════════════════════════
#  배치 실행 (Cell 5) 위에 두기를 권장.
#  EXPORT_TICKER 만 바꿔서 실행하면 한 종목에 대해 즉시 9-sheet xlsx 생성.
#  DB 저장은 안 하므로 실험/단발성 분석에 안전.
# ═══════════════════════════════════════════════════════════════════════

EXPORT_TICKER = "NVDA"          # ← 여기만 바꾸세요
EXPORT_DIR    = (Path(r"C:/reports") if os.name == "nt"
                 else Path.home() / "reports")

if EXPORT_TICKER:
    print(f"\n{'='*70}")
    print(f"[Single Export] {EXPORT_TICKER} → V10 형식 Excel")
    print(f"{'='*70}")

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    _model = DCFModel(ticker=EXPORT_TICKER, engine=engine, verbose=True)
    try:
        _model.run()
        _path = _model.export_v10_excel(out_dir=EXPORT_DIR)
        v = _model.valuation
        print(f"\n  ✓ Saved: {_path}")
        print(f"    Target Price : ${v['target_price']:.2f}")
        print(f"    Current Price: ${v['current_price']:.2f}")
        print(f"    Upside       : {v['upside_pct']:.1f}%")
        print(f"    WACC         : {v['wacc']*100:.2f}%")
        print(f"    g_terminal   : {v['g_terminal']*100:.2f}%")
        print(f"    Moat         : {v['moat_label']}  (ρ={v['moat_rho']:.3f}, Phase2={v['n_phase2']}년)")
    except Exception as e:
        print(f"\n  ✗ FAIL: {e}")
        import traceback
        traceback.print_exc()
    finally:
        clear_memory()
