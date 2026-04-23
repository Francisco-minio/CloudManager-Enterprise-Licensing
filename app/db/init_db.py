import asyncio
from app.db.session import engine
from app.db.models import Base

async def init_models():
    async with engine.begin() as conn:
        # En producción se recomienda usar Alembic para migraciones
        # pero para el setup inicial creamos todo.
        print("Creando tablas en la base de datos...")
        await conn.run_sync(Base.metadata.create_all)
        print("Tablas creadas exitosamente.")

if __name__ == "__main__":
    asyncio.run(init_models())
