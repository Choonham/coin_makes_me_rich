
# ===================================================================================
#   state/repo.py: 데이터베이스 리포지토리
# ===================================================================================
#
#   - 데이터베이스와의 모든 상호작용을 담당하는 리포지토리(Repository) 패턴 구현체입니다.
#   - 데이터베이스 연결, 세션 관리, CRUD(Create, Read, Update, Delete) 작업을 위한
#     메서드를 제공합니다.
#   - ORM으로 SQLModel을 사용하여 비동기 데이터베이스 작업을 수행합니다.
#
#   **주요 기능:**
#   - **비동기 DB 연결**: `aiosqlite` (SQLite용) 또는 `asyncpg` (PostgreSQL용) 드라이버와
#     연동하여 비동기적으로 데이터베이스에 연결합니다.
#   - **테이블 생성**: 애플리케이션 시작 시, `SQLModel`로 정의된 모델들을 기반으로
#     데이터베이스에 테이블이 없으면 자동으로 생성합니다.
#   - **세션 관리**: 비동기 세션(`AsyncSession`)을 사용하여 데이터베이스 트랜잭션을 관리합니다.
#   - **데이터 로깅**: `add_trade`, `add_event`, `add_trend_event`와 같은 메서드를 통해
#     거래, 시스템 이벤트, 트렌드 데이터를 영구적으로 저장합니다.
#   - **데이터 조회**: 과거 데이터를 조회하는 메서드를 제공합니다 (예: `get_trades_for_symbol`).
#
#   **데이터베이스 선택:**
#   - 기본값은 `SQLite`로, 별도의 DB 서버 없이 파일 기반으로 간단하게 사용할 수 있습니다.
#   - `.env` 파일에서 `DB_URL`을 `postgresql+asyncpg://...` 형태로 변경하면
#     PostgreSQL 데이터베이스를 사용할 수 있도록 확장 가능하게 설계되었습니다.
#
#
import asyncio
from typing import List, Sequence

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel, select
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.state.models import EventLog, TradeLog, TrendEventLog
from app.utils.typing import TrendEvent

class Database:
    """데이터베이스 연결 및 세션 관리를 담당하는 클래스"""

    def __init__(self, db_url: str):
        self.engine = create_async_engine(db_url, echo=False) # echo=True로 설정 시 SQL 쿼리 로깅
        self.session_maker = sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        logger.info(f"Database engine created for URL: {db_url}")

    async def connect(self):
        """데이터베이스에 연결하고 테이블을 생성합니다."""
        async with self.engine.begin() as conn:
            # SQLModel 모델에 정의된 모든 테이블을 생성합니다.
            await conn.run_sync(SQLModel.metadata.create_all)
        logger.info("Database connected and tables created (if not exist)." )

    async def disconnect(self):
        """데이터베이스 연결을 종료합니다."""
        await self.engine.dispose()
        logger.info("Database connection closed.")

    async def get_session(self) -> AsyncSession:
        """비동기 데이터베이스 세션을 반환합니다."""
        async with self.session_maker() as session:
            return session

    async def add_trade(self, trade_data: dict):
        """체결된 거래를 데이터베이스에 기록합니다."""
        async with self.session_maker() as session:
            trade_log = TradeLog(**trade_data) # dict를 SQLModel 객체로 변환
            session.add(trade_log)
            await session.commit()
            logger.debug(f"Trade logged to DB: {trade_log}")

    async def add_event(self, event_type: str, details: str):
        """시스템 이벤트를 데이터베이스에 기록합니다."""
        async with self.session_maker() as session:
            event_log = EventLog(event_type=event_type, details=details)
            session.add(event_log)
            await session.commit()
            logger.info(f"Event logged to DB: {event_type} - {details}")

    async def add_trend_event(self, trend_event: TrendEvent):
        """수집된 트렌드 이벤트를 데이터베이스에 기록합니다."""
        async with self.session_maker() as session:
            # Pydantic 모델을 SQLModel로 변환
            trend_log = TrendEventLog.model_validate(trend_event)
            session.add(trend_log)
            await session.commit()
            logger.debug(f"Trend event logged to DB: {trend_event.symbol_final} - {trend_event.text[:30]}...")

    async def get_trades_for_symbol(self, symbol: str, limit: int = 100) -> Sequence[TradeLog]:
        """특정 심볼에 대한 최근 거래 기록을 조회합니다."""
        async with self.session_maker() as session:
            statement = select(TradeLog).where(TradeLog.symbol == symbol).order_by(TradeLog.timestamp.desc()).limit(limit)
            result = await session.execute(statement)
            return result.scalars().all()

    async def get_all_trades(self, limit: int = 100) -> Sequence[TradeLog]:
        """모든 심볼에 대한 최근 거래 기록을 조회합니다."""
        async with self.session_maker() as session:
            statement = select(TradeLog).order_by(TradeLog.timestamp.desc()).limit(limit)
            result = await session.execute(statement)
            return result.scalars().all()

from pathlib import Path

# 프로젝트 루트를 기준으로 절대 경로 생성
PROJECT_ROOT = Path(__file__).parent.parent
DB_URL = f"sqlite+aiosqlite:///{PROJECT_ROOT.joinpath('trading_bot.db')}"

# 전역 데이터베이스 인스턴스 생성
db = Database(DB_URL)
