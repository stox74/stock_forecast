# -*- coding: utf-8 -*-
# -*- coding: utf-8 -*-
"""
STEP 11. 구글트렌드 0-floor(검열) 구간 제거 후 재검증.

apr_us_google_trends.is_zero_floor = 1 인 구간은 구글이 0으로 바닥처리한 값이다.
medicube 는 262주 중 84주(32%)가 0-floor이며 전부 2023-06-04 이전이다.
이 구간을 포함하면 계열이 인위적인 하키스틱 모양이 되어 상관이 부풀려진다.
-> 0-floor 제거 표본(2023Q3~)에서 다시 확인한다.
"""
import pandas as pd, numpy as np
from scipy import stats
from common import D_DATA, D_TAB, log
pd.set_option('display.width',240)
f = pd.read_csv(D_DATA/'apr_alt_features.csv', parse_dates=['date']).set_index('date')
p = pd.read_csv(D_DATA/'company_278470_features.csv', parse_dates=['date']).set_index('date')
from deepdive import load_monthly, cumulative_quarter_features
hs = cumulative_quarter_features(load_monthly('330499'))
f['HS330499_yoy']=hs['cum3_usd_yoy'].reindex(f.index)

VARS = {'gt_brand_level':'메디큐브 검색수준','gt_rel_peer':'상대검색','gt_rel_peer_yoy':'상대검색 YoY',
        'gt_sos':'검색 점유율','gt_brand_level_yoy':'메디큐브 검색 YoY',
        'az_gmv_index_fixed':'아마존 GMV지수','coh_rev_velocity_yoy':'코호트 리뷰속도 YoY',
        'HS330499_yoy':'HS330499 수출 YoY','PLB_trend':'[위약] 시간추세','PLB_n_asin':'[위약] ASIN 패널크기'}

ALL = []
for cut,lab in [('2021-10-01','전체(0-floor 포함, 기존)'), ('2023-07-01','0-floor 제거 후')]:
    print("\n"+"="*95); print("### 표본:",lab," (시작 %s)"%cut)
    rows=[]
    for tgt,tl in (('rev_yoy','매출액 YoY'),('opm','영업이익률')):
        for c,nm in VARS.items():
            m = pd.concat([p[tgt], f[c]], axis=1).loc[cut:].dropna()
            m.columns=['y','x']
            if len(m)<6 or m['x'].std()==0: continue
            r,pv = stats.pearsonr(m['y'],m['x'])
            t=np.arange(len(m),dtype=float)
            ry=stats.linregress(t,m['y']).slope*t; rx=stats.linregress(t,m['x']).slope*t
            pr,pp = stats.pearsonr(m['y']-ry, m['x']-rx)
            rows.append({'목표':tl,'지표':nm,'n':len(m),'r':round(r,3),'p':round(pv,4),
                         '편상관r':round(pr,3),'편상관p':round(pp,4)})
    d=pd.DataFrame(rows); d['표본']=lab; ALL.append(d)
    for tl in ('매출액 YoY','영업이익률'):
        print("\n["+tl+"]")
        print(d[d['목표']==tl].sort_values('편상관r',ascending=False).drop(columns='목표').to_string(index=False))


out = pd.concat(ALL, ignore_index=True)
out.to_csv(D_TAB/"step18_apr_zerofloor_recheck.csv", index=False, encoding="utf-8-sig")
log("ZEROFLOOR", "{}행 저장".format(len(out)))
