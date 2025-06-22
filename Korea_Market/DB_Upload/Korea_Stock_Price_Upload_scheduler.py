import os
import schedule
import time
from datetime import datetime

def run_stock_loader():
    """
    stock_price_loader.py 파일 실행
    """
    print(f"[{datetime.now()}] stock_price_loader.py 실행 시작")
    os.system("python Korea_stock_price_loader.py")  # 외부 파이썬 파일 실행
    print(f"[{datetime.now()}] stock_price_loader.py 실행 완료")

def main():
    # 매일 오후 5시 실행 스케줄 설정
    schedule.every().day.at("18:20").do(run_stock_loader)
    print("스케줄러가 시작되었습니다. 매일 오후 5시에 stock_price_loader.py를 실행합니다.")

    # 스케줄러 루프
    while True:
        schedule.run_pending()  # 예약된 작업 실행
        time.sleep(60)  # 60초 대기 (CPU 부담 줄이기)

if __name__ == "__main__":
    main()