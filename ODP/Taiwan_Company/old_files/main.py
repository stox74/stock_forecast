"""CLI 진입점.

사용 예
  python main.py collect --start 2019-01 --end 2026-06   # 과거 이력 일괄 수집
  python main.py latest                                  # 최신 월 증분 수집(OpenAPI→MOPS 폴백)
  python main.py forecast --horizon 6                    # SARIMA 예측 후 DB 저장
  python main.py plot                                    # 차트 PNG 생성 (output/)
  python main.py run --horizon 6                         # latest → forecast → plot 한번에
"""
import argparse
from datetime import date

from config import COMPANIES
from db import get_conn


def main():
    p = argparse.ArgumentParser(description="대만 대표기업 월 매출 수집/예측/시각화")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="MOPS에서 기간 일괄 수집")
    c.add_argument("--start", required=True, help="YYYY-MM")
    c.add_argument("--end", default=None, help="YYYY-MM (기본: 지난달)")

    sub.add_parser("latest", help="최신 월 증분 수집")

    f = sub.add_parser("forecast", help="SARIMA 예측")
    f.add_argument("--horizon", type=int, default=6, help="예측 개월 수")

    sub.add_parser("plot", help="시각화 PNG 생성")

    r = sub.add_parser("run", help="latest → forecast → plot")
    r.add_argument("--horizon", type=int, default=6)

    args = p.parse_args()
    conn = get_conn()
    print(f"대상 기업: {', '.join(f'{v}({k})' for k, v in COMPANIES.items())}\n")

    if args.cmd == "collect":
        from collector import collect_range
        end = args.end
        if end is None:
            t = date.today()
            y, m = (t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12)
            end = f"{y}-{m:02d}"
        collect_range(conn, args.start, end)

    elif args.cmd == "latest":
        from collector import collect_latest
        collect_latest(conn)

    elif args.cmd == "forecast":
        from forecaster import forecast_all
        forecast_all(conn, horizon=args.horizon)

    elif args.cmd == "plot":
        from visualizer import plot_all
        plot_all(conn)

    elif args.cmd == "run":
        from collector import collect_latest
        from forecaster import forecast_all
        from visualizer import plot_all
        collect_latest(conn)
        forecast_all(conn, horizon=args.horizon)
        plot_all(conn)

    conn.close()


if __name__ == "__main__":
    main()
