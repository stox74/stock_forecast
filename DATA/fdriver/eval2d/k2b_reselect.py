# -*- coding: utf-8 -*-
"""2d 작업 2b: 위약 기준 80곳 재선정 + 키워드 보강 + 수동 후보 (명세서 2d v3 9절 7항). DB 쓰기 없음.

선정 구간 2019Q1~2023Q4 자료만 쓴다. 매출은 fd_rev_adj_2d, 후보 계산 방법은 k2_candidates 와 같다. 바뀐 점:
1. 키워드 보강: hs_keywords.KW4 + KW4_ADD_K2B. 행 글자와 키워드의 띄어쓰기를 지우고 맞춘다 ('스 프 링').
   미연결 품목 = 작업 2 규칙(KW4, 띄어쓰기 그대로)으로 어떤 키워드에도 걸리지 않은 DART 행 (확정 기업, 비중 0~100%).
   목록에 보강 뒤 연결 여부(linked_k2b)와 잡힌 호(codes_k2b)를 붙인다.
2. 수동 후보 (출처 manual): hs_keywords.MANUAL_K2B. 수출 테이블에 선정 구간 자료가 없는 코드는 계산하지 못하고
   k2b_manual_unavailable.csv 에 남긴다.
3. 제외: hs_keywords.EXCLUDE_K2B (KSIC 582 기업의 'kosis_idx:서적 문구' 후보).
4. 위약검정: k2_candidates.placebo 와 같음 (무관 수출 코드를 수출 후보 수만큼 무작위 추출, N_PLACEBO_K2B=200 회).
   시드는 기업마다 [0, 종목코드] (per_firm_seed=True, n_reps=N_PLACEBO_K2B). 작업 2 는 난수열 하나를 기업 순서대로 이어 썼다.
5. 자격 (9절 7항): 최고 후보(corr_full 최대) 의 corr_full >= 0.3 또는 direct6, 그리고 같은 후보의 corr_excovid >= 0.3.
   순위: 위약 대비 초과 = 최고 corr_full - 위약 최고상관 평균, 큰 순 상위 80곳. 81위 이하 자격 기업은 '대기'.
   대조군(한국화장품제조, 동원산업)은 같은 방법으로 계산만 하고 대조군으로 둔다.
6. v1 대안순위_80 (k2_firm_tiers.alt_rank) 과 비교해 들어오고 나간 기업과 이유를 낸다.
결과: 2d/K2b/ (fdriver_2d_driver_mapping_v2.xlsx, CSV)
실행: python -m DATA.fdriver.eval2d.k2b_reselect
"""
import json
import logging
import re
from typing import Dict, List

import numpy as np
import pandas as pd

from ..config import output_path
from . import hs_keywords as H
from . import k2_candidates as C

log = logging.getLogger(__name__)
OUT = output_path("2d", "K2b")
K2 = output_path("2d", "K2")
K1 = output_path("2d", "K1")
MAP_FILE = "fdriver_2d_driver_mapping_v2.xlsx"
N_MAIN = 80
N_PLACEBO_K2B = 200      # 작업 2 는 50회. 경계 순위가 난수에 덜 흔들리도록 늘림
CORR_MIN = C.CORR_MIN


def kw_merged() -> Dict[str, List[str]]:
    kw = {k: list(v) for k, v in H.KW4.items()}
    for k, v in H.KW4_ADD_K2B.items():
        kw.setdefault(k, [])
        kw[k] += [x for x in v if x not in kw[k]]
    return kw


def _match_codes(label: str, kw: Dict, nospace: bool) -> List[str]:
    up = re.sub(r"\s+", "", str(label)).upper() if nospace else str(label).upper()
    return [c for c, ks in kw.items() if any((re.sub(r"\s+", "", k) if nospace else k).upper() in up for k in ks)]


def unlinked_items(rows: pd.DataFrame, firms: pd.DataFrame, pstatus: pd.DataFrame) -> pd.DataFrame:
    r = rows[rows["ticker"].isin(firms["ticker"])].merge(firms[["ticker", "name"]], on="ticker", how="left")
    r = r.merge(pstatus[["ticker", "status"]], on="ticker", how="left")
    r["linked_k2"] = [bool(_match_codes(l, H.KW4, False)) for l in r["label"]]
    kw = kw_merged()
    r["codes_k2b"] = [",".join(_match_codes(l, kw, True)) for l in r["label"]]
    r["linked_k2b"] = r["codes_k2b"] != ""
    u = r[~r["linked_k2"]].copy()
    u["item"] = u["label"].str.replace(r"\s+", " ", regex=True).str[-120:]
    u["review"] = u["pct"].between(10, 100)       # 비중 10~100% (100 초과는 표 밖 숫자를 읽은 잡음)
    u = u[["ticker", "name", "status", "fy", "item", "pct", "review", "linked_k2b", "codes_k2b"]]
    return u.sort_values(["review", "pct"], ascending=[False, False]).reset_index(drop=True)


def manual_candidates(inv: pd.DataFrame) -> (pd.DataFrame, pd.DataFrame):
    ok, bad = [], []
    for t, items in H.MANUAL_K2B.items():
        for code, why in items:
            (ok if code in inv.index else bad).append({"ticker": t, "code": code, "kw": why})
    ok = pd.DataFrame(ok, columns=["ticker", "code", "kw"]).assign(product_share=np.nan, direct6=False,
                                                                   source="manual")
    return ok, pd.DataFrame(bad, columns=["ticker", "code", "kw"])


def build(firms: pd.DataFrame, rows: pd.DataFrame, pstatus: pd.DataFrame, inv, names, manual: pd.DataFrame):
    """k2_candidates.build 와 같은 순서 (dart -> hs_map -> ksic, + domestic) 에 보강 사전과 수동 후보, 제외를 더한다."""
    ok = set(pstatus.loc[pstatus["status"] == "ok", "ticker"])
    dc = C.dart_candidates(rows[rows["ticker"].isin(ok)], inv, names, kw4=kw_merged(), nospace=True)
    have_dart = set(dc["ticker"]) if len(dc) else set()
    rest = [t for t in firms["ticker"] if t not in have_dart]
    hm = C.hsmap_candidates(rest, inv) if rest else pd.DataFrame()
    have_map = set(hm["ticker"]) if len(hm) else set()
    rest2 = firms[firms["ticker"].isin([t for t in rest if t not in have_map])]
    kc = C.ksic_candidates(rest2, inv)
    dm = C.domestic_candidates(firms)
    mn = manual[manual["ticker"].isin(firms["ticker"])]
    cands = pd.concat([x for x in (dc, hm, kc, dm, mn) if len(x)], ignore_index=True)
    cols = ["ticker", "code", "source", "kw", "product_share", "direct6"]
    cands = cands[cols].drop_duplicates(["ticker", "code"], keep="last")
    ks = firms.set_index("ticker")["ksic"].astype(str)
    drop = pd.Series(False, index=cands.index)
    for pre, code in H.EXCLUDE_K2B:
        drop |= cands["ticker"].map(ks).fillna("").str.startswith(pre) & (cands["code"] == code)
    excluded = cands[drop].copy()
    return cands[~drop].reset_index(drop=True), excluded


def qualify(c: pd.DataFrame, firm: pd.DataFrame) -> pd.DataFrame:
    c = c.sort_values(["ticker", "corr_full"], ascending=[True, False], na_position="last").copy()
    c["rank"] = c.groupby("ticker").cumcount() + 1
    best = c[c["rank"] == 1].set_index("ticker")
    f = firm.copy()
    f["n_candidates"] = f["ticker"].map(c.groupby("ticker").size()).fillna(0).astype(int)
    f["best_code"] = f["ticker"].map(best["code"])
    f["best_source"] = f["ticker"].map(best["source"])
    f["best_kw"] = f["ticker"].map(best["kw"])
    f["best_corr"] = f["ticker"].map(best["corr_full"])
    f["best_corr_excovid"] = f["ticker"].map(best["corr_excovid"])
    f["best_corr_lead1"] = f["ticker"].map(best["corr_lead1"])
    f["best_direct6"] = f["ticker"].map(best["direct6"]).fillna(False).astype(bool)
    f["cond_full"] = (f["best_corr"] >= CORR_MIN) | f["best_direct6"]
    f["cond_excovid"] = f["best_corr_excovid"] >= CORR_MIN
    f["qualified"] = f["cond_full"] & f["cond_excovid"]
    f["excess_over_placebo"] = f["best_corr"] - f["placebo_best_mean"]
    q = f[f["qualified"]].sort_values("excess_over_placebo", ascending=False)
    rank = {t: i + 1 for i, t in enumerate(q["ticker"])}
    f["rank_v2"] = f["ticker"].map(rank)
    f["tier_v2"] = np.where(f["rank_v2"] <= N_MAIN, "승인 대상", np.where(f["qualified"], "대기", "자격 없음"))
    return c, f


def compare_v1(f: pd.DataFrame, v1: pd.DataFrame, c_v1: pd.DataFrame) -> pd.DataFrame:
    v1 = v1.copy()
    v1["v1_qualified"] = v1["passed"] & (v1["best_corr_excovid"] >= CORR_MIN)
    b1 = c_v1.sort_values("corr_full", ascending=False).drop_duplicates("ticker").set_index("ticker")
    v1["v1_best_code"] = v1["ticker"].map(b1["code"])
    v1["v1_best_source"] = v1["ticker"].map(b1["source"])
    m = f.merge(v1[["ticker", "alt_rank", "v1_qualified", "best_pass_corr", "best_corr_excovid", "placebo_best_mean",
                    "excess_over_placebo", "v1_best_code", "v1_best_source"]].rename(columns={
                        "alt_rank": "rank_v1", "best_pass_corr": "v1_best_corr",
                        "best_corr_excovid": "v1_best_corr_excovid", "placebo_best_mean": "v1_placebo",
                        "excess_over_placebo": "v1_excess"}), on="ticker", how="left")
    m["in_v1"] = m["rank_v1"].notna()
    m["in_v2"] = m["rank_v2"] <= N_MAIN
    d = m[m["in_v1"] | m["in_v2"]].copy()

    def why(r):
        if r.in_v1 and r.in_v2:
            return "유지"
        parts = []
        if r.best_code != r.v1_best_code:
            parts.append(f"최고 후보 바뀜 ({r.v1_best_source}:{r.v1_best_code} -> {r.best_source}:{r.best_code})")
        if r.in_v1:
            if not r.qualified:
                if not r.cond_excovid:
                    parts.append(f"최고 후보 corr_excovid {r.best_corr_excovid:.2f} < 0.3")
                if not r.cond_full:
                    parts.append(f"최고 후보 corr_full {r.best_corr:.2f} < 0.3")
            else:
                if abs(r.placebo_best_mean - r.v1_placebo) > 0.03:
                    parts.append(f"위약 평균 {r.v1_placebo:.3f} -> {r.placebo_best_mean:.3f}")
                parts.append(f"순위 밀림 (v1 {int(r.rank_v1)}위 -> v2 {int(r.rank_v2)}위, 초과 {r.v1_excess:.3f} -> "
                             f"{r.excess_over_placebo:.3f})")
            return "나감: " + "; ".join(parts)
        if not r.v1_qualified:
            parts.append("v1 자격 미달 -> v2 자격")
        if pd.notna(r.v1_placebo) and abs(r.placebo_best_mean - r.v1_placebo) > 0.03:
            parts.append(f"위약 평균 {r.v1_placebo:.3f} -> {r.placebo_best_mean:.3f}")
        parts.append(f"v2 {int(r.rank_v2)}위, 초과 {r.excess_over_placebo:.3f}"
                     + (f" (v1 초과 {r.v1_excess:.3f})" if pd.notna(r.v1_excess) else ""))
        return "들어옴: " + "; ".join(parts)
    d["change"] = d.apply(why, axis=1)
    cols = ["ticker", "name", "industry_group", "rank_v1", "rank_v2", "change", "v1_qualified", "v1_best_source", "v1_best_code",
            "v1_best_corr", "v1_best_corr_excovid", "v1_placebo", "v1_excess", "best_source", "best_code", "best_kw",
            "best_corr", "best_corr_excovid", "placebo_best_mean", "excess_over_placebo", "n_candidates"]
    return d[cols].sort_values(["rank_v2", "rank_v1"], na_position="last")


def write_excel(f, c, names, dom, unl, targets, k1, manual_bad, excluded):
    main = f[f["tier_v2"] == "승인 대상"].sort_values("rank_v2")
    wait = f[f["tier_v2"] == "대기"].sort_values("rank_v2")
    info = targets.merge(k1[["ticker", "rev_jump_quarters"]], on="ticker", how="left")

    def firm_rows(x):
        t = x
        return pd.DataFrame({
            "순위 (위약 대비 초과)": t["rank_v2"], "종목코드": t["ticker"], "기업": t["name"], "시장": t["market"],
            "업종": t["ksic_div_name"], "KSIC": t["ksic"], "coverage_class": t["coverage_class"],
            "최근4분기 매출(억)": t["rev_last4_sum_eok"], "매출 기준": t["basis"],
            "flag_basis (기준불일치)": t["flag_basis"], "flag_jump (급변)": t["flag_jump"],
            "최고 후보 출처": t["best_source"], "최고 후보 코드/계열": t["best_code"], "일치 키워드": t["best_kw"],
            "최고 corr_full": t["best_corr"].round(3), "같은 후보 corr_excovid": t["best_corr_excovid"].round(3),
            "같은 후보 corr_lead1": t["best_corr_lead1"].round(3),
            "주력 6자리 직접 일치": np.where(t["best_direct6"], "Y", ""), "후보 수": t["n_candidates"],
            "위약 최고상관 평균": t["placebo_best_mean"].round(3), "위약 0.3 통과율": t["placebo_pass_share"].round(2),
            "위약 대비 초과": t["excess_over_placebo"].round(3), "수출총액지수 상관": t["corr_total_exports"].round(3),
            "생산지수 상관": t["corr_ip"].round(3), "제안 (Y/N/검토)": "", "제안 이유": ""})

    s = c[c["ticker"].isin(main["ticker"])].merge(main[["ticker", "name", "rank_v2"]], on="ticker")
    s["item"] = [dom[k]["note"] if src == "domestic" else names.get(k, "") for k, src in zip(s["code"], s["source"])]
    s["lag"] = [dom[k]["lag_days"] if src == "domestic" else "" for k, src in zip(s["code"], s["source"])]
    s = s.sort_values(["rank_v2", "rank"])
    cand = pd.DataFrame({
        "기업 순위": s["rank_v2"], "종목코드": s["ticker"], "기업": s["name"], "후보 순위": s["rank"],
        "출처": s["source"], "HS 코드 / 내수 계열": s["code"], "품목 (참고)": s["item"], "일치 키워드": s["kw"],
        "DART 매출 비중 합(%)": s["product_share"], "corr_full (2019~2023)": s["corr_full"].round(3), "n": s["n_full"],
        "corr_excovid": s["corr_excovid"].round(3), "corr_lead1 (보고만)": s["corr_lead1"].round(3),
        "주력 6자리 직접 일치": np.where(s["direct6"].astype(bool), "Y", ""), "공표 시차(일)": s["lag"],
        "사용 (Y/N)": "", "세그먼트": "", "가중치": ""})
    manual_tpl = pd.DataFrame({"종목코드": [""] * 20, "HS 코드/계열": "", "이유": ""})
    ev = main.merge(k1[["ticker", "rev_jump_quarters"]], on="ticker", how="left")
    ev = ev[ev["flag_jump"] == 1]
    ev_rows = pd.DataFrame({"종목코드": ev["ticker"], "기업": ev["name"], "분기": ev["rev_jump_quarters"],
                            "내용": "매출 전년 동기 대비 2배 초과 또는 절반 미만 분기 4개 이상 (급변)",
                            "기본안": "D1 판정에서 따로 보고 (9절 4)", "판단 (제외/유지/공시 확인)": "", "메모": ""})
    unl_rows = pd.DataFrame({"종목코드": unl["ticker"], "기업": unl["name"], "DART 표 추출": unl["status"],
                             "사업연도": unl["fy"], "품목 (DART 행)": unl["item"], "비중(%)": unl["pct"],
                             "검토 대상 (비중 10~100%)": np.where(unl["review"], "Y", ""),
                             "K2b 보강 뒤 연결": np.where(unl["linked_k2b"], "Y", ""), "잡힌 호": unl["codes_k2b"]})
    guide = pd.DataFrame({"fdriver 2d 드라이버 매핑 입력표 v2": [
        f"작성: {pd.Timestamp.today().date()} (2d 작업 2b). 명세서 2d v3 9절 7항 (위약 기준 재선정).",
        "자격: 최고 후보 corr_full 0.3 이상(또는 주력 6자리 직접 일치) + 같은 후보 corr_excovid 0.3 이상.",
        f"순위: 위약 대비 초과 = 최고 corr_full - 위약 최고상관 평균 (무관 수출 코드 무작위 {N_PLACEBO_K2B}회, 기업별 seed [0, 종목코드]).",
        "기업: 상위 80곳. '제안 (Y/N/검토)'·'제안 이유'를 채워 주세요.",
        "후보: 80곳의 모든 후보 (기업 순위, 후보 순위 순). '사용 (Y/N)', '세그먼트', '가중치'를 채워 주세요.",
        "출처: dart / hs_map / ksic / domestic / manual. 수동 후보를 더하려면 '수동후보_추가' 시트에 적어 주세요.",
        "미연결_품목: 작업 2 키워드로 연결되지 않은 DART 행과 K2b 보강 뒤 연결 여부.",
        "대기: 81위 이하 자격 기업. 기업사건: 80곳 중 급변 표시 기업.",
        "이 파일의 HS 코드·드라이버명·가중치는 어떤 공개물에도 넣지 않습니다 (7절).",
    ]})
    with pd.ExcelWriter(OUT / MAP_FILE, engine="openpyxl") as xw:
        guide.to_excel(xw, sheet_name="안내", index=False)
        firm_rows(main).to_excel(xw, sheet_name="기업", index=False)
        cand.to_excel(xw, sheet_name="후보", index=False)
        manual_tpl.to_excel(xw, sheet_name="수동후보_추가", index=False)
        unl_rows.to_excel(xw, sheet_name="미연결_품목", index=False)
        firm_rows(wait).to_excel(xw, sheet_name="대기", index=False)
        ev_rows.to_excel(xw, sheet_name="기업사건", index=False)


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    targets = pd.read_csv(K2 / "k2_targets.csv", dtype={"ticker": str, "ksic": str})
    k1 = pd.read_csv(K1 / "k1_universe.csv", dtype={"ticker": str, "ksic": str})
    firms = targets[targets["confirmed"]].copy()
    rows = pd.read_csv(K2 / "k2_products.csv", dtype={"ticker": str})
    pstatus = pd.read_csv(K2 / "k2_products_status.csv", dtype={"ticker": str})
    inv = C.trade_inventory()
    names = C.hs_names()

    unl = unlinked_items(rows, firms, pstatus)
    manual, manual_bad = manual_candidates(inv)
    cands, excluded = build(firms, rows, pstatus, inv, names, manual)
    ctl = C.control_frame()
    ccands, _ = build(ctl["firm"], ctl["rows"], ctl["status"], inv, names, manual)

    codes = sorted(set(cands.loc[cands["source"] != "domestic", "code"]) |
                   set(ccands.loc[ccands["source"] != "domestic", "code"]))
    xq = C.export_yoy(codes)
    dom = C.domestic_series(sorted(set(cands.loc[cands["source"] == "domestic", "code"]) |
                                   set(ccands.loc[ccands["source"] == "domestic", "code"])))
    rev = C.rev_yoy_frame(firms["ticker"].tolist())
    corr = C.correlations(cands, rev, xq, dom)
    xq_all = C.export_yoy(inv.index.tolist())
    macro = C.domestic_series(["bok:수출금액지수", "bok:전산업생산지수"])
    pl = C.placebo(firms[["ticker"]], corr, rev, xq_all, macro, per_firm_seed=True, n_reps=N_PLACEBO_K2B)
    c2, f = qualify(corr, firms.merge(pl, on="ticker", how="left"))

    ccorr = C.correlations(ccands, ctl["rev_yoy"], xq, dom)
    cpl = C.placebo(ctl["firm"][["ticker"]], ccorr, ctl["rev_yoy"], xq_all, macro, per_firm_seed=True, n_reps=N_PLACEBO_K2B)
    _, cf = qualify(ccorr, ctl["firm"].merge(cpl, on="ticker", how="left"))

    v1 = pd.read_csv(K2 / "k2_firm_tiers.csv", dtype={"ticker": str})
    c_v1 = pd.read_csv(K2 / "k2_candidates_all.csv", dtype={"ticker": str, "code": str})
    chg = compare_v1(f, v1, c_v1)

    # 작업 2 후보와 비교: 후보가 바뀐 기업 수
    old = pd.read_csv(K2 / "k2_candidates_all.csv", dtype={"ticker": str, "code": str})
    old_sets = old.groupby("ticker")["code"].agg(frozenset)
    new_sets = c2.groupby("ticker")["code"].agg(frozenset)
    changed = [t for t in new_sets.index if old_sets.get(t, frozenset()) != new_sets[t]]
    new_kw_hits = c2[(c2["source"] == "dart")].merge(
        old[["ticker", "code"]].assign(_old=1), on=["ticker", "code"], how="left")
    new_kw_hits = new_kw_hits[new_kw_hits["_old"].isna()]

    write_excel(f, c2, names, dom, unl, targets, k1, manual_bad, excluded)
    unl.to_csv(OUT / "k2b_unlinked_items.csv", index=False, encoding="utf-8-sig")
    c2.to_csv(OUT / "k2b_candidates_all.csv", index=False, encoding="utf-8-sig")
    f.to_csv(OUT / "k2b_firm_tiers.csv", index=False, encoding="utf-8-sig")
    chg.to_csv(OUT / "k2b_changes_vs_v1.csv", index=False, encoding="utf-8-sig")
    manual_bad.to_csv(OUT / "k2b_manual_unavailable.csv", index=False, encoding="utf-8-sig")
    excluded.to_csv(OUT / "k2b_excluded_candidates.csv", index=False, encoding="utf-8-sig")
    ccorr.to_csv(OUT / "k2b_controls_candidates.csv", index=False, encoding="utf-8-sig")
    new_kw_hits.to_csv(OUT / "k2b_new_dart_candidates.csv", index=False, encoding="utf-8-sig")
    dw = c2[c2["ticker"] == "000430"]
    summary = {
        "placebo": {"n_reps": N_PLACEBO_K2B, "seed": "[0, 종목코드] 기업별"},
        "unlinked_rows": len(unl), "unlinked_firms": int(unl["ticker"].nunique()),
        "unlinked_rows_ge10": int((unl["pct"] >= 10).sum()),
        "unlinked_rows_review": int(unl["review"].sum()), "unlinked_firms_review": int(unl.loc[unl["review"], "ticker"].nunique()),
        "unlinked_review_linked_after": int((unl["review"] & unl["linked_k2b"]).sum()),
        "unlinked_ge10_linked_after": int(((unl["pct"] >= 10) & unl["linked_k2b"]).sum()),
        "unlinked_linked_after_all": int(unl["linked_k2b"].sum()),
        "firms_candidates_changed": len(changed), "new_dart_candidates": len(new_kw_hits),
        "new_dart_candidate_firms": int(new_kw_hits["ticker"].nunique()),
        "candidates_by_source": c2["source"].value_counts().to_dict(),
        "excluded_candidates": len(excluded), "excluded_firms": int(excluded["ticker"].nunique()),
        "manual_unavailable": manual_bad.to_dict(orient="records"),
        "daewon": dw[["code", "source", "corr_full", "corr_excovid", "corr_lead1", "rank"]].to_dict(orient="records"),
        "daewon_firm": f[f["ticker"] == "000430"][["best_code", "best_corr", "best_corr_excovid", "placebo_best_mean",
                                                  "excess_over_placebo", "qualified", "rank_v2"]]
        .to_dict(orient="records"),
        "qualified": int(f["qualified"].sum()), "tiers": f["tier_v2"].value_counts().to_dict(),
        "cond_full_only": int((f["cond_full"] & ~f["cond_excovid"]).sum()),
        "main_by_source": f[f["tier_v2"] == "승인 대상"]["best_source"].value_counts().to_dict(),
        "main_by_industry": f[f["tier_v2"] == "승인 대상"]["industry_group"].value_counts().to_dict(),
        "main_by_coverage": f[f["tier_v2"] == "승인 대상"]["coverage_class"].value_counts().to_dict(),
        "main_stats": f[f["tier_v2"] == "승인 대상"][["best_corr", "best_corr_excovid", "placebo_best_mean",
                                                    "excess_over_placebo", "corr_total_exports", "n_candidates"]]
        .describe().round(3).to_dict(),
        "main_flag_basis": int(f.loc[f["tier_v2"] == "승인 대상", "ticker"].isin(
            targets.loc[targets["flag_basis"] == 1, "ticker"]).sum()),
        "main_flag_jump": int(f.loc[f["tier_v2"] == "승인 대상", "ticker"].isin(
            targets.loc[targets["flag_jump"] == 1, "ticker"]).sum()),
        "overlap_v1_alt": int((chg["change"] == "유지").sum()),
        "entered": int(chg["change"].str.startswith("들어옴").sum()),
        "exited": int(chg["change"].str.startswith("나감").sum()),
        "controls": cf[["ticker", "name", "best_code", "best_corr", "best_corr_excovid", "excess_over_placebo",
                        "qualified"]].to_dict(orient="records"),
    }
    (OUT / "k2b_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str),
                                         encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
    return f, chg


if __name__ == "__main__":
    main()
