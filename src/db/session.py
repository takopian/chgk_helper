from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
import os

DATABASE_URL = os.environ.get("DATABASE_URL") or (
    f"postgresql+asyncpg://{os.environ.get('PG_USERNAME','chgk_user')}:{os.environ.get('PG_PASSWORD','changeme')}@postgres:5432/{os.environ.get('PG_DATABASE','chgk_db')}"
)

async_engine = create_async_engine(DATABASE_URL, echo=False, future=True)

async_session = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
