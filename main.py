import logging
from datetime import datetime, timedelta
from fastapi import FastAPI, Depends, HTTPException, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from pydantic import BaseModel
from sqlalchemy.orm import joinedload, selectinload
from app.db.session import get_db, async_session, engine
from app.db.models import Tenant, License, User, SyncLog, UserLicense, Base
from app.services.sync_engine import SyncEngine
from app.core.license_mapper import get_friendly_name, is_free_license
from app.core.config import settings, crypto
from app.core.auth import get_auth_url, get_token_from_code
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from contextlib import asynccontextmanager
import pandas as pd
from io import BytesIO
from fastapi.responses import StreamingResponse
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.units import inch
import uuid

class TenantCreate(BaseModel):
    id: str | None = None
    name: str
    tenant_id: str
    client_id: str
    client_secret: str | None = None

class PlatformUserSchema(BaseModel):
    id: str | None = None
    email: str
    name: str | None = None
    role: str
    is_active: bool = True

# --- Background Tasks ---
async def scheduled_sync_all():
    logger.info("Iniciando sincronización automática de todos los tenants...")
    async with async_session() as db:
        stmt = select(Tenant)
        res = await db.execute(stmt)
        tenants = res.scalars().all()
        engine = SyncEngine(db)
        for t in tenants:
            try:
                await engine.sync_tenant(t.id)
                logger.info(f"Sync automático completado para {t.name}")
            except Exception:
                logger.exception(f"Error en sync automático para {t.name}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Crear tablas si no existen
    logger.info("Verificando y creando tablas de base de datos...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Startup: Scheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(scheduled_sync_all, 'interval', hours=6)
    scheduler.start()
    logger.info("Programador de tareas iniciado (Sync cada 6h)")
    yield
    # Shutdown
    scheduler.shutdown()

app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Middleware para manejo de sesiones (requerido para OAuth2)
app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["friendly_name"] = get_friendly_name
templates.env.filters["is_free"] = is_free_license

@app.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")

@app.get("/login/microsoft")
async def login_microsoft(request: Request):
    redirect_uri = str(request.url_for("auth_callback"))
    auth_url = get_auth_url(redirect_uri)
    return RedirectResponse(auth_url)

@app.get("/auth/callback")
async def auth_callback(request: Request, code: str, db: AsyncSession = Depends(get_db)):
    from app.db.models import PlatformUser
    redirect_uri = str(request.url_for("auth_callback"))
    user_info = await get_token_from_code(code, redirect_uri)
    
    email = user_info.get("preferred_username")
    if not email:
        raise HTTPException(status_code=400, detail="No se pudo obtener el correo de Microsoft 365.")

    # Validamos contra nuestra tabla de usuarios permitidos
    stmt = select(PlatformUser).where(PlatformUser.email == email)
    res = await db.execute(stmt)
    platform_user = res.scalar_one_or_none()

    if not platform_user or not platform_user.is_active:
        raise HTTPException(status_code=403, detail="No tienes autorización para acceder a esta plataforma.")

    # Guardamos los datos autorizados en sesión
    request.session["user"] = {
        "email": platform_user.email,
        "name": platform_user.name or user_info.get("name", "Usuario"),
        "role": platform_user.role
    }
    return RedirectResponse(url="/")

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login")

@app.get("/platform-settings")
async def platform_settings(request: Request, db: AsyncSession = Depends(get_db)):
    from app.db.models import PlatformUser
    user = request.session.get("user")
    if not user or user.get("role") != "SUPERADMIN":
        return RedirectResponse(url="/")
    
    stmt = select(PlatformUser)
    res = await db.execute(stmt)
    platform_users = res.scalars().all()
    
    return templates.TemplateResponse(request=request, name="platform_users.html", context={
        "platform_users": platform_users,
        "current_user": user
    })

@app.get("/api/platform-users/{id}")
async def get_platform_user_api(id: str, request: Request, db: AsyncSession = Depends(get_db)):
    from app.db.models import PlatformUser
    user = request.session.get("user")
    if not user or user.get("role") != "SUPERADMIN":
        raise HTTPException(status_code=403)
    
    import uuid
    stmt = select(PlatformUser).where(PlatformUser.id == uuid.UUID(id))
    res = await db.execute(stmt)
    p_user = res.scalar_one_or_none()
    
    if not p_user: raise HTTPException(status_code=404)
    return {
        "id": str(p_user.id),
        "name": p_user.name,
        "email": p_user.email,
        "role": p_user.role,
        "is_active": p_user.is_active
    }

@app.post("/api/platform-users")
async def save_platform_user(data: PlatformUserSchema, request: Request, db: AsyncSession = Depends(get_db)):
    from app.db.models import PlatformUser
    user = request.session.get("user")
    if not user or user.get("role") != "SUPERADMIN":
        raise HTTPException(status_code=403)

    p_user = None
    if data.id:
        import uuid
        stmt = select(PlatformUser).where(PlatformUser.id == uuid.UUID(data.id))
        res = await db.execute(stmt)
        p_user = res.scalar_one_or_none()
    
    if p_user:
        p_user.name = data.name
        p_user.email = data.email
        p_user.role = data.role
        p_user.is_active = data.is_active
    else:
        p_user = PlatformUser(
            name=data.name,
            email=data.email,
            role=data.role,
            is_active=data.is_active
        )
        db.add(p_user)
    
    await db.commit()
    return {"message": "Usuario guardado"}

@app.delete("/api/platform-users/{id}")
async def delete_platform_user(id: str, request: Request, db: AsyncSession = Depends(get_db)):
    from app.db.models import PlatformUser
    user = request.session.get("user")
    if not user or user.get("role") != "SUPERADMIN":
        raise HTTPException(status_code=403)
    
    import uuid
    stmt = select(PlatformUser).where(PlatformUser.id == uuid.UUID(id))
    res = await db.execute(stmt)
    p_user = res.scalar_one_or_none()
    
    if p_user:
        if p_user.email == user.get("email"):
            raise HTTPException(status_code=400, detail="No puedes eliminarte a ti mismo")
        await db.delete(p_user)
        await db.commit()
    return {"message": "Usuario eliminado"}

@app.get("/")
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    # Protección de ruta simple
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login")

    # Obtener tenants
    stmt_tenants = select(Tenant)
    res_tenants = await db.execute(stmt_tenants)
    tenants_list = res_tenants.scalars().all()

    # Métricas globales
    total_users_stmt = select(User)
    total_users_res = await db.execute(total_users_stmt)
    total_users = len(total_users_res.scalars().all())

    # Alertas de Sync (Logs fallidos en las últimas 24h)
    yesterday = datetime.utcnow() - timedelta(days=1)
    stmt_alerts = select(SyncLog).where(SyncLog.status == "FAILED", SyncLog.start_time >= yesterday)
    res_alerts = await db.execute(stmt_alerts)
    sync_alerts_count = len(res_alerts.scalars().all())

    return templates.TemplateResponse(request=request, name="dashboard.html", context={
        "tenants": tenants_list,
        "total_users": total_users,
        "sync_alerts": sync_alerts_count,
        "current_user": user
    })

@app.post("/tenants/add")
async def add_tenant(request: Request, data: TenantCreate, db: AsyncSession = Depends(get_db)):
    user = request.session.get("user")
    if not user or user.get("role") not in ["ADMIN", "SUPERADMIN"]:
        raise HTTPException(status_code=403, detail="No tienes permisos para esta acción.")
    try:
        logger.info(f"Intentando registrar tenant: {data.name} ({data.tenant_id})")
        
        # Lógica de Upsert
        tenant = None
        if data.id:
            import uuid
            stmt = select(Tenant).where(Tenant.id == uuid.UUID(data.id))
            res = await db.execute(stmt)
            tenant = res.scalar_one_or_none()
        
        # Si no hay ID o no se encontró por ID, buscamos por tenant_id
        if not tenant:
            stmt = select(Tenant).where(Tenant.tenant_id == data.tenant_id)
            res = await db.execute(stmt)
            tenant = res.scalar_one_or_none()
        
        if tenant:
            logger.info(f"Actualizando tenant: {tenant.name}")
            tenant.name = data.name
            tenant.tenant_id = data.tenant_id
            tenant.client_id = data.client_id
            if data.client_secret:
                tenant.client_secret_encrypted = crypto.encrypt(data.client_secret)
        else:
            if not data.client_secret:
                raise HTTPException(status_code=400, detail="Client Secret is required for new tenants")
            
            logger.info("Creando nuevo tenant en BD")
            tenant = Tenant(
                name=data.name,
                tenant_id=data.tenant_id,
                client_id=data.client_id,
                client_secret_encrypted=crypto.encrypt(data.client_secret)
            )
            db.add(tenant)
            
        await db.commit()
        logger.info("Tenant guardado con éxito")
        return {"message": "Tenant guardado correctamente"}
    except Exception as e:
        logger.exception("Error al guardar tenant")
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/tenants/delete/{id}")
async def delete_tenant(id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user = request.session.get("user")
    if not user or user.get("role") not in ["ADMIN", "SUPERADMIN"]:
        raise HTTPException(status_code=403, detail="No tienes permisos para esta acción.")
    import uuid
    try:
        tenant_uuid = uuid.UUID(id)
        stmt = select(Tenant).where(Tenant.id == tenant_uuid)
        res = await db.execute(stmt)
        tenant = res.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        
        await db.delete(tenant)
        await db.commit()
        return {"message": "Tenant eliminado correctamente"}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/tenants/toggle/{id}")
async def toggle_tenant(id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user = request.session.get("user")
    if not user or user.get("role") not in ["ADMIN", "SUPERADMIN"]:
        raise HTTPException(status_code=403, detail="No tienes permisos para esta acción.")
    import uuid
    try:
        tenant_uuid = uuid.UUID(id)
        stmt = select(Tenant).where(Tenant.id == tenant_uuid)
        res = await db.execute(stmt)
        tenant = res.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        
        tenant.is_active = not tenant.is_active
        await db.commit()
        return {"message": f"Tenant {'activado' if tenant.is_active else 'desactivado'}"}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/users")
async def list_users(request: Request, db: AsyncSession = Depends(get_db)):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login")
    stmt = select(User).options(
        joinedload(User.tenant),
        selectinload(User.licenses).joinedload(UserLicense.license)
    )
    res = await db.execute(stmt)
    users = res.scalars().all()
    return templates.TemplateResponse(request=request, name="users.html", context={"users": users, "current_user": user})

@app.get("/licenses")
async def list_licenses(request: Request, tenant_id: str = None, db: AsyncSession = Depends(get_db)):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login")
    # 1. Si NO hay tenant_id, mostramos el Directorio de Tenants
    if not tenant_id:
        stmt = select(Tenant).options(selectinload(Tenant.licenses))
        res = await db.execute(stmt)
        tenants_list = res.scalars().all()
        return templates.TemplateResponse(request=request, name="licenses_tenants.html", context={
            "tenants": tenants_list,
            "current_user": user
        })

    # 2. Si HAY tenant_id, mostramos el detalle ya construido
    stmt_t = select(Tenant).where(Tenant.tenant_id == tenant_id)
    res_t = await db.execute(stmt_t)
    target_tenant = res_t.scalar_one_or_none()

    if not target_tenant:
        raise HTTPException(status_code=404, detail="Tenant no encontrado")

    stmt = select(License).where(License.tenant_id == target_tenant.id).options(
        joinedload(License.tenant),
        selectinload(License.user_assignments).joinedload(UserLicense.user)
    )
    res = await db.execute(stmt)
    licenses_list = res.scalars().all()

    licenses_data = []
    for lic in licenses_list:
        domains = set()
        for assignment in lic.user_assignments:
            if assignment.user and assignment.user.upn:
                domain = assignment.user.upn.split('@')[-1]
                domains.add(domain)
        
        licenses_data.append({
            "obj": lic,
            "domains": sorted(list(domains))
        })

    return templates.TemplateResponse(request=request, name="licenses_detail.html", context={
        "tenant": target_tenant,
        "licenses_data": licenses_data,
        "current_user": user
    })

@app.post("/sync/{tenant_id}")
async def trigger_sync(tenant_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user = request.session.get("user")
    if not user or user.get("role") not in ["ADMIN", "SUPERADMIN"]:
        raise HTTPException(status_code=403, detail="No tienes permisos para esta acción.")
    stmt = select(Tenant).where(Tenant.tenant_id == tenant_id)
    res = await db.execute(stmt)
    tenant = res.scalar_one_or_none()

    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    
    engine = SyncEngine(db)
    await engine.sync_tenant(tenant.id)
    return {"message": f"Sync started/finished for {tenant.name}"}

@app.get("/tenant/{id}")
async def tenant_detail(id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login")
        
    import uuid
    try:
        tenant_uuid = uuid.UUID(id)
    except:
        raise HTTPException(status_code=400, detail="Invalid UUID format")

    stmt = select(Tenant).where(Tenant.id == tenant_uuid).options(
        selectinload(Tenant.licenses).selectinload(License.user_assignments).joinedload(UserLicense.user),
        selectinload(Tenant.users),
        selectinload(Tenant.sync_logs)
    )
    res = await db.execute(stmt)
    tenant = res.scalar_one_or_none()

    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
        
    return templates.TemplateResponse(request=request, name="tenant_detail.html", context={
        "tenant": tenant,
        "current_user": user
    })

@app.get("/licenses/export/excel")
async def export_licenses_excel(tenant_id: str = None, db: AsyncSession = Depends(get_db)):
    stmt = select(License).options(joinedload(License.tenant))
    if tenant_id:
        stmt = stmt.join(License.tenant).where(Tenant.tenant_id == tenant_id)
        
    res = await db.execute(stmt)
    licenses = res.scalars().all()
    
    data = []
    for l in licenses:
        data.append({
            "Cliente": l.tenant.name,
            "ID Producto": l.sku_id,
            "SKU": l.sku_part_number,
            "Licencia (Nombre Amigable)": get_friendly_name(l.sku_part_number),
            "Total": l.total_units,
            "Consumidas": l.consumed_units,
            "Disponibles": l.total_units - l.consumed_units
        })
    
    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Licencias 365')
    
    output.seek(0)
    headers = {'Content-Disposition': 'attachment; filename="reporte_licencias_365.xlsx"'}
    return StreamingResponse(output, headers=headers, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.get("/licenses/export/pdf")
async def export_licenses_pdf(tenant_id: str = None, db: AsyncSession = Depends(get_db)):
    stmt = select(License).options(joinedload(License.tenant))
    if tenant_id:
        stmt = stmt.join(License.tenant).where(Tenant.tenant_id == tenant_id)
        
    res = await db.execute(stmt)
    licenses = res.scalars().all()
    
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    
    # Header Estilo BACKUPCODE
    p.setFillColor(colors.HexColor("#1e1b4b")) # Azul oscuro
    p.rect(0, height - 1.2 * inch, width, 1.2 * inch, fill=1)
    
    p.setFillColor(colors.white)
    p.setFont("Helvetica-Bold", 22)
    p.drawString(0.5 * inch, height - 0.7 * inch, "BACKUPCODE")
    p.setFont("Helvetica", 10)
    p.drawString(0.5 * inch, height - 0.9 * inch, "Enterprise IT Managed Services | Microsoft 365 Audit")
    
    p.setFont("Helvetica", 9)
    p.drawRightString(width - 0.5 * inch, height - 0.7 * inch, f"Fecha: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}")
    p.drawRightString(width - 0.5 * inch, height - 0.9 * inch, "Reporte de Inventario de Licencias")
    
    # Body
    y = height - 1.6 * inch
    p.setFillColor(colors.black)
    p.setFont("Helvetica-Bold", 14)
    p.drawString(0.5 * inch, y, "Reporte de Licencias por Cliente")
    y -= 0.4 * inch
    
    # Tabla Header
    p.setFont("Helvetica-Bold", 10)
    p.line(0.5 * inch, y + 2, width - 0.5 * inch, y + 2)
    p.drawString(0.5 * inch, y, "Cliente")
    p.drawString(2.5 * inch, y, "Licencia")
    p.drawString(5.5 * inch, y, "Total")
    p.drawString(6.5 * inch, y, "Cons.")
    p.drawString(7.2 * inch, y, "Disp.")
    y -= 5
    p.line(0.5 * inch, y, width - 0.5 * inch, y)
    y -= 15
    
    p.setFont("Helvetica", 9)
    for l in licenses:
        if y < 1 * inch:
            p.showPage()
            y = height - 1 * inch
            p.setFont("Helvetica-Bold", 10)
            p.drawString(0.5 * inch, y, "Cliente (Cont.)")
            y -= 20
            p.setFont("Helvetica", 9)
            
        tenant_name = l.tenant.name if l.tenant and l.tenant.name else "Cliente Desconocido"
        p.drawString(0.5 * inch, y, tenant_name[:25])
        p.drawString(2.5 * inch, y, get_friendly_name(l.sku_part_number)[:45])
        p.drawString(5.5 * inch, y, str(l.total_units))
        p.drawString(6.5 * inch, y, str(l.consumed_units))
        p.drawString(7.2 * inch, y, str(l.total_units - l.consumed_units))
        y -= 15
        
    p.save()
    buffer.seek(0)
    return StreamingResponse(buffer, media_type='application/pdf', headers={'Content-Disposition': 'attachment; filename="reporte_backupcode_m365.pdf"'})

@app.get("/api/tenants/{id}")
async def get_tenant_api(id: str, db: AsyncSession = Depends(get_db)):
    import uuid
    try:
        tenant_uuid = uuid.UUID(id)
        stmt = select(Tenant).where(Tenant.id == tenant_uuid)
        res = await db.execute(stmt)
        tenant = res.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        
        return {
            "id": str(tenant.id),
            "name": tenant.name,
            "tenant_id": tenant.tenant_id,
            "client_id": tenant.client_id,
            "is_active": tenant.is_active
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
