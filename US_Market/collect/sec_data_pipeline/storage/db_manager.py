#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Database Manager
SEC 재무데이터를 MySQL/MariaDB에 저장 및 관리
"""

import pandas as pd
from sqlalchemy import create_engine, text, Column, Integer, String, Float, Date, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from typing import Dict, List, Optional

Base = declarative_base()


class SECFinancialData(Base):
    """SEC 재무데이터 테이블"""
    __tablename__ = 'sec_financial_data'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, index=True)
    cik = Column(String(20), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    fiscal_year = Column(Integer)
    fiscal_period = Column(String(10))
    item_name = Column(String(100), nullable=False, index=True)
    value = Column(Float)
    unit = Column(String(20))
    data_source = Column(String(50))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class DBManager:
    """데이터베이스 관리 클래스"""
    
    def __init__(self, db_config: Dict):
        """
        Args:
            db_config: DB 연결 정보
                - host, port, database, user, password
        """
        self.db_config = db_config
        self.engine = None
        self.Session = None
        self._connect()
    
    def _connect(self):
        """DB 연결"""
        conn_str = (
            f"mysql+pymysql://{self.db_config['user']}:{self.db_config['password']}@"
            f"{self.db_config['host']}:{self.db_config['port']}/"
            f"{self.db_config['database']}?charset=utf8mb4"
        )
        self.engine = create_engine(conn_str, pool_pre_ping=True)
        self.Session = sessionmaker(bind=self.engine)
        print(f"✓ DB connected: {self.db_config['host']}:{self.db_config['port']}/{self.db_config['database']}")
    
    def create_tables(self):
        """테이블 생성"""
        Base.metadata.create_all(self.engine)
        print("✓ Tables created")
    
    def save_normalized_data(self, ticker: str, cik: str, df: pd.DataFrame, 
                            item_mapping: Dict[str, str] = None):
        """
        정규화된 재무데이터 저장
        
        Args:
            ticker: 주식 티커
            cik: CIK 번호
            df: 정규화된 DataFrame (date를 인덱스로)
            item_mapping: 컬럼명 -> item_name 매핑 (선택사항)
        """
        session = self.Session()
        
        try:
            for date_idx, row in df.iterrows():
                for col_name, value in row.items():
                    if pd.isna(value):
                        continue
                    
                    item_name = item_mapping.get(col_name, col_name) if item_mapping else col_name
                    
                    # UPSERT: 존재하면 UPDATE, 없으면 INSERT
                    existing = session.query(SECFinancialData).filter_by(
                        ticker=ticker,
                        date=date_idx,
                        item_name=item_name
                    ).first()
                    
                    if existing:
                        existing.value = float(value)
                        existing.updated_at = datetime.now()
                    else:
                        record = SECFinancialData(
                            ticker=ticker,
                            cik=cik,
                            date=date_idx,
                            item_name=item_name,
                            value=float(value),
                            unit='USD',
                            data_source='SEC_EDGAR'
                        )
                        session.add(record)
            
            session.commit()
            print(f"✓ Saved {ticker} data: {len(df)} dates x {len(df.columns)} items")
            
        except Exception as e:
            session.rollback()
            print(f"✗ Error saving {ticker}: {e}")
            raise
        finally:
            session.close()
    
    def query_financial_data(self, ticker: str, item_name: str = None,
                            start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """
        재무데이터 조회
        
        Args:
            ticker: 주식 티커
            item_name: 항목명 (선택사항)
            start_date: 시작 날짜 (선택사항)
            end_date: 종료 날짜 (선택사항)
            
        Returns:
            조회 결과 DataFrame
        """
        query = f"""
        SELECT date, item_name, value, fiscal_year, fiscal_period, unit
        FROM sec_financial_data
        WHERE ticker = :ticker
        """
        
        params = {'ticker': ticker}
        
        if item_name:
            query += " AND item_name = :item_name"
            params['item_name'] = item_name
        
        if start_date:
            query += " AND date >= :start_date"
            params['start_date'] = start_date
        
        if end_date:
            query += " AND date <= :end_date"
            params['end_date'] = end_date
        
        query += " ORDER BY date, item_name"
        
        with self.engine.connect() as conn:
            df = pd.read_sql(text(query), conn, params=params)
        
        return df


# FMP 검증용 테이블 (추가)
class FMPFinancialData(Base):
    """FMP 재무데이터 테이블 (검증용)"""
    __tablename__ = 'fmp_financial_data'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    item_name = Column(String(100), nullable=False, index=True)
    value = Column(Float)
    created_at = Column(DateTime, default=datetime.now)


def main():
    """테스트"""
    # DB 설정
    db_config = {
        'host': 'localhost',
        'port': 3306,
        'database': 'stock_db',
        'user': 'root',
        'password': ''
    }
    
    # DB Manager 생성
    db_manager = DBManager(db_config)
    
    # 테이블 생성
    db_manager.create_tables()
    
    print("\n✓ DB Manager test completed")


if __name__ == "__main__":
    main()
