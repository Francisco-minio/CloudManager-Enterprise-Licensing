import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.config import settings
from app.db.models import Base
import sys

async def check():
    print(f"Checking connection to: {settings.DATABASE_URL}")
    try:
        engine = create_async_engine(settings.DATABASE_URL)
        async with engine.begin() as conn:
            print("Connected successfully!")
            # Check tables
            from sqlalchemy import inspect
            def get_tables(connection):
                inspective = inspect(connection)
                return inspective.get_table_names()
            
            tables = await conn.run_sync(get_tables)
            print(f"Existing tables: {tables}")
            
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(check())
