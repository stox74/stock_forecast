from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import pandas as pd
import time


def crawl_tsmc_monthly_revenue_selenium(year=2025):
    """
    Selenium을 사용하여 TSMC 월별 매출 데이터를 크롤링하는 함수

    Parameters:
    year (int): 조회할 연도 (기본값: 2025)

    Returns:
    pd.DataFrame: 월별 매출 데이터
    """

    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_argument(
        'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)

    driver = None

    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)

        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        url = f"https://investor.tsmc.com/english/monthly-revenue/{year}"
        print(f"접속 중: {url}")

        driver.get(url)

        wait = WebDriverWait(driver, 15)
        table = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.basicTable")))

        time.sleep(2)

        data = []
        rows = driver.find_elements(By.CSS_SELECTOR, "table.basicTable tbody tr")

        for row in rows:
            cols = row.find_elements(By.TAG_NAME, "td")

            if len(cols) >= 2:
                month = cols[0].text.strip()
                net_revenue = cols[1].text.strip()

                if month and net_revenue and month != 'Total':
                    net_revenue_clean = net_revenue.replace(',', '')

                    yoy_change = None
                    if len(cols) >= 3:
                        yoy_change = cols[2].text.strip()

                    data.append({
                        'Year': year,
                        'Month': month,
                        'Net_Revenue_NTD_Million': int(
                            net_revenue_clean) if net_revenue_clean.isdigit() else net_revenue_clean,
                        'YoY_Change': yoy_change
                    })

        df = pd.DataFrame(data)

        if len(df) > 0:
            print(f"\n{year}년 TSMC 월별 매출 데이터:")
            print(df.to_string(index=False))
            print(f"\n총 {len(df)}개월 데이터 수집 완료")

        return df

    except Exception as e:
        print(f"오류 발생: {e}")
        return None

    finally:
        if driver:
            driver.quit()


def crawl_multiple_years_selenium(start_year, end_year):
    """
    여러 연도의 TSMC 월별 매출 데이터를 크롤링하는 함수

    Parameters:
    start_year (int): 시작 연도
    end_year (int): 종료 연도

    Returns:
    pd.DataFrame: 통합된 월별 매출 데이터
    """

    all_data = []

    for year in range(start_year, end_year + 1):
        print(f"\n{'=' * 50}")
        print(f"{year}년 데이터 수집 중...")
        print(f"{'=' * 50}")

        df = crawl_tsmc_monthly_revenue_selenium(year)

        if df is not None and len(df) > 0:
            all_data.append(df)

        time.sleep(2)

    if all_data:
        combined_df = pd.concat(all_data, ignore_index=True)
        return combined_df
    else:
        return None


if __name__ == "__main__":
    df_2025 = crawl_tsmc_monthly_revenue_selenium(2025)

    if df_2025 is not None and len(df_2025) > 0:
        df_2025.to_csv('tsmc_monthly_revenue_2025.csv', index=False, encoding='utf-8-sig')
        print("\n✓ 데이터가 'tsmc_monthly_revenue_2025.csv' 파일로 저장되었습니다.")

        print("\n여러 연도 데이터를 수집하시겠습니까? (예: 2023-2025)")
        response = input("수집하려면 'y'를 입력하세요: ")

        if response.lower() == 'y':
            df_multiple = crawl_multiple_years_selenium(2023, 2025)
            if df_multiple is not None:
                df_multiple.to_csv('tsmc_monthly_revenue_2023_2025.csv', index=False, encoding='utf-8-sig')
                print("\n✓ 여러 연도 데이터가 'tsmc_monthly_revenue_2023_2025.csv' 파일로 저장되었습니다.")
    else:
        print("\n✗ 데이터 수집에 실패했습니다.")