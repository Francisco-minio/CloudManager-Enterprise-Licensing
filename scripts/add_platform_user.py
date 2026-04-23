import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import asyncio
import argparse
from sqlalchemy.future import select
from app.db.session import async_session
from app.db.models import PlatformUser

async def create_user(email: str, name: str, role: str):
    print(f"Buscando / Creando usuario: {email}")
    async with async_session() as db:
        stmt = select(PlatformUser).where(PlatformUser.email == email)
        res = await db.execute(stmt)
        user = res.scalar_one_or_none()

        if user:
            print("El usuario ya existe. Actualizando datos...")
            user.name = name
            user.role = role
            user.is_active = True
        else:
            print("Creando nuevo usuario de plataforma...")
            user = PlatformUser(
                email=email,
                name=name,
                role=role,
                is_active=True
            )
            db.add(user)
        
        await db.commit()
        print(f"Usuario {email} configurado exitosamente con rol {role}.")

def main():
    parser = argparse.ArgumentParser(description="Administrar usuarios de plataforma (RBAC)")
    parser.add_argument("email", type=str, help="Correo electrónico de Office 365 (UPN)")
    parser.add_argument("--name", type=str, default="Administrador", help="Nombre a mostrar")
    parser.add_argument("--role", type=str, choices=["SUPERADMIN", "ADMIN", "VIEWER"], default="SUPERADMIN", help="Rol asignado al usuario")
    args = parser.parse_args()

    asyncio.run(create_user(args.email, args.name, args.role))

if __name__ == "__main__":
    main()
