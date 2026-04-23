import asyncio
from sqlalchemy.future import select
from app.db.session import async_session
from app.db.models import Tenant
from app.core.config import crypto
import getpass

async def add_tenant():
    print("--- Registro de Nuevo Tenant Microsoft 365 ---")
    name = input("Nombre del Cliente: ")
    tenant_id = input("Azure Tenant ID: ")
    client_id = input("Client ID (App Registration): ")
    client_secret = getpass.getpass("Client Secret: ")

    async with async_session() as session:
        # Buscamos si ya existe el tenant
        stmt = select(Tenant).where(Tenant.tenant_id == tenant_id)
        res = await session.execute(stmt)
        existing_tenant = res.scalar_one_or_none()

        encrypted_secret = crypto.encrypt(client_secret)
        
        if existing_tenant:
            print(f"Detectado tenant existente. Actualizando credenciales para '{name}'...")
            existing_tenant.name = name
            existing_tenant.client_id = client_id
            existing_tenant.client_secret_encrypted = encrypted_secret
        else:
            new_tenant = Tenant(
                name=name,
                tenant_id=tenant_id,
                client_id=client_id,
                client_secret_encrypted=encrypted_secret
            )
            session.add(new_tenant)
        
        await session.commit()
        print(f"\n✅ Tenant '{name}' actualizado/registrado exitosamente.")

if __name__ == "__main__":
    asyncio.run(add_tenant())
