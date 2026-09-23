# -*- coding: utf-8 -*-
"""전체 파이프라인 재현 실행.
   python run_all.py
"""
import extract, features, analysis, backtest, report, validate
import deepdive, placebo, addendum
import apr_alt, apr_headtohead, apr_final_checks, apr_report
# apr_uncensored 는 모듈 최상위에서 실행되므로 필요한 시점에 import 한다
from common import log

if __name__ == "__main__":
    log("RUN", "STEP1 추출/검증"); extract.main()
    log("RUN", "STEP2 변수생성"); features.main()
    log("RUN", "STEP3A 상관/그래프"); analysis.main()
    log("RUN", "STEP3B 표본밖 검증"); backtest.main()
    log("RUN", "STEP4 누수검증"); validate.main()
    log("RUN", "STEP5 보고서"); report.main()
    log("RUN", "STEP6 심화(월별누적/대안지표)"); deepdive.main()
    log("RUN", "STEP7 위약검정"); placebo.main()
    log("RUN", "STEP8 보론"); addendum.main()
    log("RUN", "STEP9 에이피알 대안지표"); apr_alt.main()
    log("RUN", "STEP10 정면비교"); apr_headtohead.main()
    log("RUN", "STEP11 방향성/부문별"); apr_final_checks.main()
    log("RUN", "STEP12 0-floor 재검증"); __import__("apr_uncensored")
    log("RUN", "STEP13 에이피알 보고서"); apr_report.main()
    log("RUN", "완료")
