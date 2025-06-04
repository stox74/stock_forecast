import schedule
import time
import datetime
from update_korea_economy_data import update_korea_economy_data  # update_korea_economy_data 함수를 임포트해야 합니다.

# 작업 정의: 오전 9시에 실행될 함수
def job():
    print(f"작업 시작 시간: {datetime.datetime.now()}")
    try:
        update_korea_economy_data()
        print(f"작업 완료 시간: {datetime.datetime.now()}")
    except Exception as e:
        print(f"작업 중 오류 발생: {e}")

# 매일 오전 9시에 job 실행 예약
schedule.every().day.at("17:00").do(job)

# 매 초 현재 시간을 출력하며 스케줄러 작동
print("스케줄러 시작...")
while True:
    schedule.run_pending()
    print(f"현재 시간: {datetime.datetime.now()}")
    time.sleep(1)