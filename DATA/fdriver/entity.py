# -*- coding: utf-8 -*-
"""기업 기준표: fd_entity_master, fd_entity_link 생성·적재·조회.

master
- 한국: korea_dart_corp_master 중 KSE_Price 또는 DG 에 나오는 종목 (stock_code '000000' 제외)
  market 은 DG market(KS/KQ) 최신값, 없으면 corp_cls. listed_status 는 KSE_Price 마지막 거래일로 판정.
  fiscal_year_end 는 acc_mt, 없으면 12.
- 해외: 시범 기업 비교 대상만 (TW:2330 등)

link
- hs_code: kr_company_hs_map 활성 행 (fix_type 'unresolved' 제외). 0 보정은 그 표가 이미 한 것을 그대로 쓴다 (D1).
- 시범 6개 기업 수작업 연결 (PILOT_LINKS)

series_key 컬럼(명세서 확장): adapter 에 넘길 키. hs_code 는 hs6, tw_peer 는 대만 종목코드,
alt_data 는 family 접두어를 뗀 키. link_family() 가 link_type/link_key 로 adapter family 를 알려준다.
"""
import logging
from typing import List, Optional

import pandas as pd

from . import db

log = logging.getLogger(__name__)

T_MASTER = "fd_entity_master"
T_LINK = "fd_entity_link"

PILOTS = {
    "005930": "삼성전자", "006040": "동원산업", "267270": "현대건설기계",
    "278470": "에이피알", "003350": "한국화장품제조", "039130": "하나투어",
}

FOREIGN_ENTITIES = [
    # entity_id, country, ticker, market, name_en
    ("TW:2330", "TW", "2330", "TWSE", "TSMC"),
    ("TW:2408", "TW", "2408", "TWSE", "Nanya Technology"),
]

MARKET_FROM_DG = {"KS": "KOSPI", "KQ": "KOSDAQ"}
MARKET_FROM_CORP_CLS = {"Y": "KOSPI", "K": "KOSDAQ", "N": "KONEX"}
LISTED_GRACE_DAYS = 14   # KSE_Price 마지막 날짜에서 이 기간 안에 거래가 있으면 listed

LINK_TYPES = ("hs_code", "tw_peer", "us_peer", "kr_peer", "fred_series", "kosis_series", "komis_series",
              "tourism_series", "airline_series", "alt_data")
LINK_FAMILY = {
    "hs_code": "trade", "tw_peer": "tw_revenue", "kr_peer": "revenue", "fred_series": "fred",
    "kosis_series": "kosis", "komis_series": "komis", "tourism_series": "tourism", "airline_series": "airline",
}

DDL_MASTER = f"""
CREATE TABLE IF NOT EXISTS `{T_MASTER}` (
  `entity_id` VARCHAR(20) NOT NULL,
  `country` CHAR(2) NOT NULL,
  `ticker` VARCHAR(20) NOT NULL,
  `market` VARCHAR(10) NULL,
  `name_kr` VARCHAR(100) NULL,
  `name_en` VARCHAR(100) NULL,
  `dart_corp_code` CHAR(8) NULL,
  `fiscal_year_end` TINYINT NOT NULL DEFAULT 12,
  `listed_status` VARCHAR(10) NULL,
  `is_pilot` TINYINT NOT NULL DEFAULT 0,
  `first_collected_at` DATETIME NULL,
  `last_collected_at` DATETIME NULL,
  `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`entity_id`),
  KEY `idx_country_market` (`country`, `market`),
  KEY `idx_pilot` (`is_pilot`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

DDL_LINK = f"""
CREATE TABLE IF NOT EXISTS `{T_LINK}` (
  `entity_id` VARCHAR(20) NOT NULL,
  `link_type` VARCHAR(20) NOT NULL,
  `link_key` VARCHAR(100) NOT NULL,
  `series_key` VARCHAR(100) NOT NULL,
  `link_source` VARCHAR(50) NOT NULL,
  `role` VARCHAR(20) NULL,
  `note` VARCHAR(255) NULL,
  `is_active` TINYINT NOT NULL DEFAULT 1,
  `first_collected_at` DATETIME NULL,
  `last_collected_at` DATETIME NULL,
  `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`entity_id`, `link_type`, `link_key`),
  KEY `idx_link_key` (`link_type`, `series_key`),
  CONSTRAINT `fk_fd_entity_link_entity` FOREIGN KEY (`entity_id`)
    REFERENCES `{T_MASTER}` (`entity_id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

LINK_COLUMNS = ["entity_id", "link_type", "link_key", "series_key", "link_source", "role", "note", "is_active"]
MASTER_COLUMNS = ["entity_id", "country", "ticker", "market", "name_kr", "name_en", "dart_corp_code",
                  "fiscal_year_end", "listed_status", "is_pilot"]

# 하나투어 출국 수요 보조 지표: 국제선만 쓴다.
# AIRPORTAL·KAC 항공사별 통계는 dims_json 이 비어 있어 국내선/국제선 구분이 없다 (2026-10-03 확인).
# 인천공항공사 항공사별 출발 여객은 공항 특성상 사실상 국제선이므로 국적사 3곳만 쓴다 (2022-01부터).
_AIRLINE_INTL_DEP = [
    ("icn_96ab40e621a044fc", "대한항공(KE)"), ("icn_f1d4f303eeca792c", "아시아나항공(OZ)"),
    ("icn_fad8e743f4501a4e", "제주항공(7C)"),
]


def _m(eid, ltype, key, series_key, role, note):
    return {"entity_id": eid, "link_type": ltype, "link_key": key, "series_key": series_key,
            "link_source": "manual", "role": role, "note": note, "is_active": 1}


def pilot_links(apr_asins: Optional[List[str]] = None) -> pd.DataFrame:
    """시범 6개 기업 수작업 연결."""
    rows = [
        # 삼성전자
        _m("005930", "hs_code", "854231", "854231", "product", "프로세서·컨트롤러 (명세서 시범 드라이버, HS맵에 없음)"),
        _m("005930", "tw_peer", "TW:2330", "2330", "peer", "TSMC 월매출 (파운드리·반도체 업황)"),
        _m("005930", "tw_peer", "TW:2408", "2408", "peer", "Nanya 월매출 (DRAM 업황)"),
        _m("005930", "kr_peer", "000660", "000660", "peer", "SK하이닉스 분기 매출 (메모리 업황)"),
        # 현대건설기계
        _m("267270", "fred_series", "HOUST", "HOUST", "customer", "미국 주택착공 (TTLCONS 미수집)"),
        _m("267270", "fred_series", "PERMIT", "PERMIT", "customer", "미국 건축허가"),
        # 에이피알
        _m("278470", "fred_series", "RSHPCS", "RSHPCS", "customer", "미국 건강·뷰티 소매판매"),
        _m("278470", "kosis_series", "kosis_113bb45a04e152ed", "kosis_113bb45a04e152ed", "customer",
           "온라인쇼핑 거래액: 화장품 계 (DT_1KE10041)"),
        _m("278470", "kosis_series", "kosis_b85f8425a11ca86a", "kosis_b85f8425a11ca86a", "customer",
           "소매판매액지수 경상: 화장품 (DT_1K41012)"),
        _m("278470", "alt_data", "google_trends:US:medicube", "US:medicube", "product", "구글 트렌드 medicube (재조정 영향)"),
        _m("278470", "alt_data", "google_trends:US:korean skincare", "US:korean skincare", "customer",
           "구글 트렌드 korean skincare (재조정 영향)"),
        # 한국화장품제조
        _m("003350", "hs_code", "330420", "330420", "product", "눈화장용 제품류 (명세서 시범 드라이버, HS맵에 없음)"),
        # 하나투어
        _m("039130", "tourism_series", "D:100", "D:100", "customer", "출국 내국인 (direction D, nat_code 100)"),
        _m("039130", "tourism_series", "E:TOTAL", "E:TOTAL", "customer", "방한 외국인 국가 합계"),
    ]
    for sid, name in _AIRLINE_INTL_DEP:
        rows.append(_m("039130", "airline_series", sid, sid, "customer", f"인천공항 출발 여객(국제선) {name}"))
    for asin in apr_asins or []:
        rows.append(_m("278470", "alt_data", f"amazon_bsr:US:{asin}", f"US:{asin}", "product", "아마존 BSR medicube"))
    return pd.DataFrame(rows, columns=LINK_COLUMNS)


def hs_links(hs_map: pd.DataFrame) -> pd.DataFrame:
    """kr_company_hs_map 행을 링크로. 문자열은 그대로 두고, 보정하지 않는다."""
    m = hs_map.copy()
    m = m[(m["is_active"].fillna(0).astype(int) == 1) & (m["fix_type"].fillna("") != "unresolved")]
    note = m["hs_name"].fillna("").astype(str) + " | fix=" + m["fix_type"].fillna("").astype(str)
    out = pd.DataFrame({
        "entity_id": m["ticker"].astype(str),
        "link_type": "hs_code",
        "link_key": m["hs_code"].astype(str),
        "series_key": m["hs6"].astype(str),
        "link_source": "kr_company_hs_map",
        "role": "product",
        "note": note.str.slice(0, 255),
        "is_active": 1,
    })
    return out.drop_duplicates(["entity_id", "link_type", "link_key"]).reset_index(drop=True)


def link_family(link_type: str, link_key: str) -> str:
    if link_type == "alt_data":
        return link_key.split(":", 1)[0]
    return LINK_FAMILY[link_type]


# ---------------------------------------------------------------- build from DB (read only)
def build_master() -> pd.DataFrame:
    corp = db.read_sql(
        "SELECT corp_code, stock_code, corp_name, corp_cls, acc_mt, updated_at FROM korea_dart_corp_master "
        "WHERE stock_code IS NOT NULL AND TRIM(stock_code) <> '' AND stock_code <> '000000'"
    )
    corp["stock_code"] = corp["stock_code"].astype(str).str.strip()
    corp = corp.sort_values("updated_at").drop_duplicates("stock_code", keep="last")

    kse = db.read_sql("SELECT `code`, MAX(`date`) AS last_date FROM `KSE_Price` GROUP BY `code`")
    kse["code"] = kse["code"].astype(str)
    kse_max = pd.to_datetime(kse["last_date"]).max()
    dg = db.read_sql(
        "SELECT d.ticker, d.market FROM korea_fs_data_from_DG d "
        "JOIN (SELECT ticker, MAX(date) AS md FROM korea_fs_data_from_DG GROUP BY ticker) x "
        "ON d.ticker = x.ticker AND d.date = x.md GROUP BY d.ticker, d.market"
    )
    dg["code"] = dg["ticker"].astype(str).str[1:]
    dg = dg.drop_duplicates("code", keep="last")

    universe = set(kse["code"]) | set(dg["code"])
    kr = corp[corp["stock_code"].isin(universe)].copy()
    last = kr["stock_code"].map(kse.set_index("code")["last_date"])
    last = pd.to_datetime(last)
    mk = kr["stock_code"].map(dg.set_index("code")["market"]).map(MARKET_FROM_DG)
    mk = mk.fillna(kr["corp_cls"].map(MARKET_FROM_CORP_CLS))
    fye = pd.to_numeric(kr["acc_mt"], errors="coerce").fillna(12).astype(int)

    master = pd.DataFrame({
        "entity_id": kr["stock_code"],
        "country": "KR",
        "ticker": kr["stock_code"],
        "market": mk,
        "name_kr": kr["corp_name"].astype(str).str.slice(0, 100),
        "name_en": None,
        "dart_corp_code": kr["corp_code"].astype(str),
        "fiscal_year_end": fye,
        "listed_status": (last >= kse_max - pd.Timedelta(days=LISTED_GRACE_DAYS)).map({True: "listed", False: "delisted"}),
        "is_pilot": kr["stock_code"].isin(PILOTS).astype(int),
    })
    foreign = pd.DataFrame([
        {"entity_id": e, "country": c, "ticker": t, "market": m, "name_kr": None, "name_en": n,
         "dart_corp_code": None, "fiscal_year_end": 12, "listed_status": "listed", "is_pilot": 0}
        for e, c, t, m, n in FOREIGN_ENTITIES
    ])
    out = pd.concat([master, foreign], ignore_index=True)[MASTER_COLUMNS]
    missing = set(PILOTS) - set(out["entity_id"])
    if missing:
        raise RuntimeError(f"pilot entities missing from master: {sorted(missing)}")
    return out.reset_index(drop=True)


def build_links(master_ids) -> pd.DataFrame:
    hs_map = db.read_sql(
        "SELECT ticker, hs_code, hs6, hs_name, fix_type, is_active FROM kr_company_hs_map")
    asins = db.read_sql(
        "SELECT asin FROM amazon_asin_master WHERE ticker = 'A278470' AND domain = 'US' AND is_active = 1")
    links = pd.concat([hs_links(hs_map), pilot_links(sorted(asins["asin"].astype(str)))], ignore_index=True)
    links = links.drop_duplicates(["entity_id", "link_type", "link_key"], keep="last")
    unknown = ~links["entity_id"].isin(set(master_ids))
    if unknown.any():
        log.warning("links dropped (entity not in master): %d rows, %s", int(unknown.sum()),
                    sorted(links.loc[unknown, "entity_id"].unique())[:20])
    return links[~unknown].reset_index(drop=True)


def create_tables() -> None:
    db.execute_fd_ddl(DDL_MASTER)
    db.execute_fd_ddl(DDL_LINK)


def build_and_load() -> dict:
    """fd_entity_master, fd_entity_link 을 만들고 채운다. 쓰기는 이 두 테이블에만 한다.

    이번 빌드에 없는 기존 연결은 지우지 않고 is_active = 0 으로 바꾼다.
    """
    master = build_master()
    links = build_links(master["entity_id"])
    create_tables()
    n_m = db.write_fd(master, T_MASTER, ["entity_id"])
    n_l = db.write_fd(links, T_LINK, ["entity_id", "link_type", "link_key"])

    existing = db.read_sql(f"SELECT entity_id, link_type, link_key, series_key, link_source, role, note "
                           f"FROM `{T_LINK}` WHERE is_active = 1")
    cur = set(zip(links["entity_id"], links["link_type"], links["link_key"]))
    stale = existing[[k not in cur for k in zip(existing["entity_id"], existing["link_type"], existing["link_key"])]]
    n_s = 0
    if not stale.empty:
        stale = stale.assign(is_active=0)
        n_s = db.write_fd(stale, T_LINK, ["entity_id", "link_type", "link_key"])
    return {"master_rows": n_m, "link_rows": n_l, "deactivated": n_s, "stale": stale,
            "master": master, "links": links}


# ---------------------------------------------------------------- query
def entity_links(entity_id: str, link_type: Optional[str] = None) -> pd.DataFrame:
    sql = f"SELECT * FROM `{T_LINK}` WHERE `entity_id` = :e AND `is_active` = 1"
    p = {"e": entity_id}
    if link_type:
        sql += " AND `link_type` = :t"
        p["t"] = link_type
    df = db.read_sql(sql + " ORDER BY `link_type`, `link_key`", p)
    if not df.empty:
        df["family"] = [link_family(t, k) for t, k in zip(df["link_type"], df["link_key"])]
    return df


def entity_info(entity_ids) -> pd.DataFrame:
    frag, p = db.in_clause("e", list(entity_ids))
    return db.read_sql(f"SELECT * FROM `{T_MASTER}` WHERE `entity_id` IN {frag}", p)
