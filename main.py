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
from typing import Any

class AssignLicensesSchema(BaseModel):
    add_licenses: list[dict[str, Any]]
    remove_licenses: list[str]

class GroupMemberSchema(BaseModel):
    user_graph_id: str

from sqlalchemy.orm import joinedload, selectinload
from app.db.session import get_db, async_session, engine
from app.db.models import Tenant, License, User, SyncLog, UserLicense, Base, AuditLog
from app.services.graph_service import GraphService
from app.services.utils import get_graph_service_for_tenant

from app.services.sync_engine import SyncEngine
from app.core.license_mapper import get_friendly_name, is_free_license
from app.core.config import settings, crypto
from app.core.auth import get_auth_url, get_token_from_code
from app.services.notification_service import NotificationService
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
    legal_name: str | None = None
    tax_id: str | None = None
    contact_email: str | None = None
    send_notifications: bool = True

class PlatformUserSchema(BaseModel):
    id: str | None = None
    email: str
    name: str | None = None
    role: str # SUPERADMIN, ADMIN, FINANCE, VIEWER
    is_active: bool = True

class BillingSchema(BaseModel):
    id: str | None = None
    tenant_id: str
    sku_id: str
    sku_part_number: str | None = None
    unit_cost: float
    quantity: int
    invoice_number: str
    expiration_date: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    currency: str = "USD"
    notes: str | None = None

class NotificationConfigSchema(BaseModel):
    telegram_enabled: bool
    telegram_token: str | None = None
    telegram_chat_id: str | None = None
    email_enabled: bool
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_pass: str | None = None
    email_from: str | None = None
    notify_days_before: str = "30,15,5"

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

async def scheduled_notifications_check():
    logger.info("Iniciando verificación diaria de vencimientos...")
    async with async_session() as db:
        service = NotificationService(db)
        await service.check_expirations_and_notify()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Crear tablas si no existen
    logger.info("Verificando y creando tablas de base de datos...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Startup: Scheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(scheduled_sync_all, 'interval', hours=6)
    scheduler.add_job(scheduled_notifications_check, 'cron', hour=9, minute=0) # Todos los días a las 9 AM
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

@app.get("/audit-logs")
async def audit_logs_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login")
    
    # Obtener logs ordenados por fecha descendente
    stmt = select(AuditLog).options(joinedload(AuditLog.tenant)).order_by(AuditLog.created_at.desc())
    res = await db.execute(stmt)
    logs = res.scalars().all()
    
    # También necesitamos los tenants para el filtro
    stmt_tenants = select(Tenant)
    res_tenants = await db.execute(stmt_tenants)
    tenants = res_tenants.scalars().all()
    
    return templates.TemplateResponse(request=request, name="audit_logs.html", context={
        "logs": logs,
        "tenants": tenants,
        "current_user": user
    })

@app.get("/changelog")
async def changelog_page(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse(request=request, name="changelog.html", context={
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

@app.get("/adminconsent/login")
async def adminconsent_login(request: Request):
    import os
    operator = request.session.get("user")
    if not operator or operator.get("role") not in ["ADMIN", "SUPERADMIN"]:
        raise HTTPException(status_code=403, detail="No autorizado")
        
    msp_client_id = os.getenv("MSP_CLIENT_ID") or os.getenv("AZURE_AD_CLIENT_ID")
    if not msp_client_id:
        raise HTTPException(status_code=400, detail="MSP_CLIENT_ID / AZURE_AD_CLIENT_ID no configurado en el entorno")
        
    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/adminconsent/callback"
    
    consent_url = (
        f"https://login.microsoftonline.com/common/adminconsent"
        f"?client_id={msp_client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&state=msp_consent"
    )
    return RedirectResponse(consent_url)

@app.get("/adminconsent/callback")
async def adminconsent_callback(
    request: Request,
    tenant: str | None = None,
    admin_consent: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: AsyncSession = Depends(get_db)
):
    import os
    operator = request.session.get("user")
    if not operator or operator.get("role") not in ["ADMIN", "SUPERADMIN"]:
        raise HTTPException(status_code=403, detail="No autorizado")
        
    if error:
        raise HTTPException(status_code=400, detail=f"Error de Microsoft: {error_description or error}")
        
    if not tenant:
        raise HTTPException(status_code=400, detail="Falta el parámetro 'tenant' de Microsoft")
        
    msp_client_id = os.getenv("MSP_CLIENT_ID") or os.getenv("AZURE_AD_CLIENT_ID")
    msp_client_secret = os.getenv("MSP_CLIENT_SECRET") or os.getenv("AZURE_AD_CLIENT_SECRET")
    if not msp_client_id or not msp_client_secret:
        raise HTTPException(status_code=400, detail="Credenciales globales de la aplicación multi-tenant no configuradas en el entorno (.env)")
        
    import aiohttp
    token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    payload = {
        "client_id": msp_client_id,
        "client_secret": msp_client_secret,
        "grant_type": "client_credentials",
        "scope": "https://graph.microsoft.com/.default"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(token_url, data=payload) as resp:
                if resp.status != 200:
                    err_body = await resp.text()
                    raise Exception(f"No se pudo obtener token para el tenant consentido: {err_body}")
                token_data = await resp.json()
                access_token = token_data.get("access_token")
                
        org_url = "https://graph.microsoft.com/v1.0/organization"
        headers = {"Authorization": f"Bearer {access_token}"}
        async with aiohttp.ClientSession() as session:
            async with session.get(org_url, headers=headers) as resp:
                if resp.status != 200:
                    resp_text = await resp.text()
                    raise Exception(f"No se pudieron obtener los detalles de la organización en Graph (Status {resp.status}): {resp_text}")
                org_data = await resp.json()
                org_info = org_data.get("value", [{}])[0]
                org_name = org_info.get("displayName", "Nuevo Cliente Multi-Tenant")
                
        stmt = select(Tenant).where(Tenant.tenant_id == tenant)
        res = await db.execute(stmt)
        db_tenant = res.scalar_one_or_none()
        
        if not db_tenant:
            db_tenant = Tenant(
                name=org_name,
                tenant_id=tenant,
                client_id=None,
                client_secret_encrypted=None,
                is_active=True
            )
            db.add(db_tenant)
            await db.commit()
            await db.refresh(db_tenant)
            
            sync_engine = SyncEngine(db)
            try:
                await sync_engine.sync_tenant(db_tenant.id)
            except Exception as sync_err:
                logger.error(f"Error en sincronización inicial de {org_name}: {sync_err}")
        
        return RedirectResponse(url="/?msg=consent_success", status_code=303)
        
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

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
            tenant.legal_name = data.legal_name
            tenant.tax_id = data.tax_id
            tenant.contact_email = data.contact_email
            tenant.send_notifications = data.send_notifications
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
                client_secret_encrypted=crypto.encrypt(data.client_secret),
                legal_name=data.legal_name,
                tax_id=data.tax_id,
                contact_email=data.contact_email,
                send_notifications=data.send_notifications
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
    stmt = select(User).join(User.tenant).options(
        joinedload(User.tenant),
        selectinload(User.licenses).joinedload(UserLicense.license)
    ).order_by(Tenant.name, User.display_name)
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
        selectinload(Tenant.users).selectinload(User.licenses).joinedload(UserLicense.license),
        selectinload(Tenant.sync_logs),
        selectinload(Tenant.billing_records)
    )
    res = await db.execute(stmt)
    tenant = res.scalar_one_or_none()

    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
        
    return templates.TemplateResponse(request=request, name="tenant_detail.html", context={
        "tenant": tenant,
        "current_user": user,
        "now": datetime.utcnow()
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

@app.get("/api/users/{id}/details")
async def get_user_details_api(id: str, request: Request, db: AsyncSession = Depends(get_db)):
    operator = request.session.get("user")
    if not operator:
        raise HTTPException(status_code=401, detail="No autenticado")
    
    import uuid
    import asyncio
    try:
        user_uuid = uuid.UUID(id)
        stmt = select(User).where(User.id == user_uuid).options(joinedload(User.tenant))
        res = await db.execute(stmt)
        db_user = res.scalar_one_or_none()
        if not db_user:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        
        tenant = db_user.tenant
        graph = get_graph_service_for_tenant(tenant)
        
        # Parallel fetch from Microsoft Graph
        profile, memberships, manager, licenses, tenant_skus = await asyncio.gather(
            graph.fetch_user_extended_profile(db_user.graph_id),
            graph.fetch_user_member_of(db_user.graph_id),
            graph.fetch_user_manager(db_user.graph_id),
            graph.fetch_user_license_details(db_user.graph_id),
            graph.fetch_licenses()
        )
        
        # Parse memberships: split into Groups vs Directory Roles
        groups = []
        roles = []
        for member in memberships:
            odType = member.get("@odata.type", "")
            if "#microsoft.graph.directoryRole" in odType:
                roles.append({
                    "id": member.get("id"),
                    "name": member.get("displayName"),
                    "description": member.get("description")
                })
            else:
                groups.append({
                    "id": member.get("id"),
                    "name": member.get("displayName"),
                    "description": member.get("description"),
                    "mail": member.get("mail"),
                    "type": member.get("groupTypes", [])
                })
                
        # Map license friendly names
        mapped_licenses = []
        sku_map = {sku.get("skuId"): sku for sku in tenant_skus}
        for lic in licenses:
            sku_id = lic.get("skuId")
            sku_info = sku_map.get(sku_id, {})
            sku_part = sku_info.get("skuPartNumber", "Unknown SKU")
            
            mapped_licenses.append({
                "skuId": sku_id,
                "skuPartNumber": sku_part,
                "friendlyName": sku_part
            })
            
        return {
            "profile": profile,
            "manager": manager,
            "groups": groups,
            "roles": roles,
            "licenses": mapped_licenses,
            "tenantName": tenant.name
        }
    except Exception as e:
        logger.error(f"Error getting user details: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

# --- User Actions Endpoints (CIPP Phase 1) ---

@app.post("/api/users/{id}/toggle-status")
async def toggle_user_status_api(id: str, request: Request, db: AsyncSession = Depends(get_db)):
    operator = request.session.get("user")
    if not operator or operator.get("role") not in ["ADMIN", "SUPERADMIN"]:
        raise HTTPException(status_code=403, detail="No autorizado")
    
    import uuid
    try:
        user_uuid = uuid.UUID(id)
        stmt = select(User).where(User.id == user_uuid).options(joinedload(User.tenant))
        res = await db.execute(stmt)
        db_user = res.scalar_one_or_none()
        if not db_user:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        
        tenant = db_user.tenant
        graph = get_graph_service_for_tenant(tenant)
        
        new_status = not db_user.is_active
        success = await graph.update_user_status(db_user.graph_id, new_status)
        
        if not success:
            raise HTTPException(status_code=400, detail="No se pudo actualizar el estado en Microsoft 365")
            
        db_user.is_active = new_status
        
        log = AuditLog(
            tenant_id=tenant.id,
            operator_email=operator.get("email"),
            action="TOGGLE_STATUS",
            target_upn=db_user.upn,
            status="SUCCESS",
            details=f"Cuenta {'habilitada' if new_status else 'deshabilitada'}"
        )
        db.add(log)
        await db.commit()
        return {"message": f"Usuario {'activado' if new_status else 'desactivado'} con éxito", "is_active": new_status}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

class ResetPasswordSchema(BaseModel):
    password: str | None = None
    force_change: bool = True

@app.post("/api/users/{id}/reset-password")
async def reset_user_password_api(id: str, request: Request, data: ResetPasswordSchema | None = None, db: AsyncSession = Depends(get_db)):
    operator = request.session.get("user")
    if not operator or operator.get("role") not in ["ADMIN", "SUPERADMIN"]:
        raise HTTPException(status_code=403, detail="No autorizado")
        
    import uuid
    import secrets
    import string
    try:
        user_uuid = uuid.UUID(id)
        stmt = select(User).where(User.id == user_uuid).options(joinedload(User.tenant))
        res = await db.execute(stmt)
        db_user = res.scalar_one_or_none()
        if not db_user:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
            
        # Parse payload options
        force_change = data.force_change if data is not None else True
        custom_pass = data.password if data is not None else None
        
        if custom_pass:
            new_pass = custom_pass
        else:
            # Generar contraseña temporal segura
            alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
            # Asegurar longitud de 12
            new_pass = "".join(secrets.choice(alphabet) for _ in range(12))
        
        tenant = db_user.tenant
        graph = get_graph_service_for_tenant(tenant)
        
        success = await graph.reset_password(db_user.graph_id, new_pass, force_change=force_change)
        if not success:
            raise HTTPException(status_code=400, detail="No se pudo restablecer la contraseña en Microsoft 365")
            
        log = AuditLog(
            tenant_id=tenant.id,
            operator_email=operator.get("email"),
            action="RESET_PASSWORD",
            target_upn=db_user.upn,
            status="SUCCESS",
            details=f"Contraseña restablecida exitosamente (Exigir cambio: {force_change})"
        )
        db.add(log)
        await db.commit()
        return {"message": "Contraseña restablecida", "password": new_pass}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/users/{id}/offboard")
async def offboard_user_api(id: str, request: Request, db: AsyncSession = Depends(get_db)):
    operator = request.session.get("user")
    if not operator or operator.get("role") not in ["ADMIN", "SUPERADMIN"]:
        raise HTTPException(status_code=403, detail="No autorizado")
        
    import uuid
    try:
        user_uuid = uuid.UUID(id)
        stmt = select(User).where(User.id == user_uuid).options(
            joinedload(User.tenant),
            selectinload(User.licenses).joinedload(UserLicense.license)
        )
        res = await db.execute(stmt)
        db_user = res.scalar_one_or_none()
        if not db_user:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
            
        tenant = db_user.tenant
        graph = get_graph_service_for_tenant(tenant)
        
        # 1. Obtener licencias asignadas
        lic_details = await graph.fetch_user_license_details(db_user.graph_id)
        sku_ids = [l["skuId"] for l in lic_details]
        
        steps_taken = []
        
        # 2. Deshabilitar cuenta
        status_ok = await graph.update_user_status(db_user.graph_id, False)
        steps_taken.append(f"Bloqueo de cuenta: {'OK' if status_ok else 'FALLÓ'}")
        
        # 3. Revocar sesiones
        sessions_ok = await graph.revoke_sessions(db_user.graph_id)
        steps_taken.append(f"Revocación de sesiones: {'OK' if sessions_ok else 'FALLÓ'}")
        
        # 4. Remover licencias
        licenses_ok = True
        if sku_ids:
            licenses_ok = await graph.remove_licenses(db_user.graph_id, sku_ids)
            steps_taken.append(f"Remoción de licencias ({len(sku_ids)}): {'OK' if licenses_ok else 'FALLÓ'}")
        else:
            steps_taken.append("No tenía licencias asignadas")
            
        overall_status = "SUCCESS" if (status_ok and sessions_ok and licenses_ok) else "FAILED"
        
        if status_ok:
            db_user.is_active = False
            
        # Remover localmente asignaciones
        if licenses_ok:
            for assignment in db_user.licenses:
                await db.delete(assignment)
                
        log = AuditLog(
            tenant_id=tenant.id,
            operator_email=operator.get("email"),
            action="OFFBOARD_USER",
            target_upn=db_user.upn,
            status=overall_status,
            details=", ".join(steps_taken)
        )
        db.add(log)
        await db.commit()
        
        if overall_status == "FAILED":
            raise HTTPException(status_code=400, detail=f"Offboarding incompleto: {', '.join(steps_taken)}")
            
        return {"message": "Offboarding ejecutado correctamente", "steps": steps_taken}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/users/{id}/assign-licenses")
async def assign_user_licenses_api(id: str, data: AssignLicensesSchema, request: Request, db: AsyncSession = Depends(get_db)):
    operator = request.session.get("user")
    if not operator or operator.get("role") not in ["ADMIN", "SUPERADMIN"]:
        raise HTTPException(status_code=403, detail="No autorizado")

    import uuid
    try:
        user_uuid = uuid.UUID(id)
        stmt = select(User).where(User.id == user_uuid).options(
            joinedload(User.tenant),
            selectinload(User.licenses).joinedload(UserLicense.license)
        )
        res = await db.execute(stmt)
        db_user = res.scalar_one_or_none()
        if not db_user:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")

        tenant = db_user.tenant
        graph = get_graph_service_for_tenant(tenant)

        # Call Graph
        success = await graph.assign_user_licenses(db_user.graph_id, data.add_licenses, data.remove_licenses)
        if not success:
            raise HTTPException(status_code=400, detail="Error al actualizar licencias en Microsoft 365")

        # Locally synchronize DB
        if data.remove_licenses:
            for assignment in db_user.licenses:
                if assignment.license.sku_id in data.remove_licenses:
                    await db.delete(assignment)

        if data.add_licenses:
            for item in data.add_licenses:
                sku_id = item["skuId"]
                stmt_l = select(License).where(License.tenant_id == tenant.id, License.sku_id == sku_id)
                res_l = await db.execute(stmt_l)
                db_license = res_l.scalar_one_or_none()
                if db_license:
                    already_assigned = any(ul.license.sku_id == sku_id for ul in db_user.licenses)
                    if not already_assigned:
                        new_ul = UserLicense(user_id=db_user.id, license_id=db_license.id)
                        db.add(new_ul)

        # Register audit log
        added_skus = [l.get("skuId") for l in data.add_licenses]
        log = AuditLog(
            tenant_id=tenant.id,
            operator_email=operator.get("email"),
            action="UPDATE_LICENSES",
            target_upn=db_user.upn,
            status="SUCCESS",
            details=f"Añadidas: {', '.join(added_skus) if added_skus else 'Ninguna'}, Removidas: {', '.join(data.remove_licenses) if data.remove_licenses else 'Ninguna'}"
        )
        db.add(log)
        await db.commit()
        return {"message": "Licencias actualizadas correctamente"}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/tenants/{id}/groups")
async def get_tenant_groups_api(id: str, request: Request, db: AsyncSession = Depends(get_db)):
    operator = request.session.get("user")
    if not operator:
        raise HTTPException(status_code=401, detail="No autenticado")
        
    import uuid
    try:
        tenant_uuid = uuid.UUID(id)
        stmt = select(Tenant).where(Tenant.id == tenant_uuid)
        res = await db.execute(stmt)
        tenant = res.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant no encontrado")
            
        graph = get_graph_service_for_tenant(tenant)
        groups = await graph.fetch_groups()
        return groups
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/tenants/{id}/licenses")
async def get_tenant_licenses_api(id: str, request: Request, db: AsyncSession = Depends(get_db)):
    operator = request.session.get("user")
    if not operator:
        raise HTTPException(status_code=401, detail="No autenticado")
    
    import uuid
    try:
        tenant_uuid = uuid.UUID(id)
        stmt = select(License).where(License.tenant_id == tenant_uuid)
        res = await db.execute(stmt)
        licenses = res.scalars().all()
        return [
            {
                "sku_id": l.sku_id,
                "sku_part_number": l.sku_part_number,
                "friendly_name": get_friendly_name(l.sku_part_number),
                "available": l.total_units - l.consumed_units
            }
            for l in licenses
        ]
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/groups/{group_id}/members")
async def get_group_members_api(group_id: str, tenant_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    operator = request.session.get("user")
    if not operator:
        raise HTTPException(status_code=401, detail="No autenticado")
        
    import uuid
    try:
        tenant_uuid = uuid.UUID(tenant_id)
        stmt = select(Tenant).where(Tenant.id == tenant_uuid)
        res = await db.execute(stmt)
        tenant = res.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant no encontrado")
            
        graph = get_graph_service_for_tenant(tenant)
        members = await graph.fetch_group_members(group_id)
        return members
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/groups/{group_id}/members")
async def add_group_member_api(group_id: str, tenant_id: str, data: GroupMemberSchema, request: Request, db: AsyncSession = Depends(get_db)):
    operator = request.session.get("user")
    if not operator or operator.get("role") not in ["ADMIN", "SUPERADMIN"]:
        raise HTTPException(status_code=403, detail="No autorizado")
        
    import uuid
    try:
        tenant_uuid = uuid.UUID(tenant_id)
        stmt = select(Tenant).where(Tenant.id == tenant_uuid)
        res = await db.execute(stmt)
        tenant = res.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant no encontrado")
            
        graph = get_graph_service_for_tenant(tenant)
        
        success = await graph.add_group_member(group_id, data.user_graph_id)
        if not success:
            raise HTTPException(status_code=400, detail="Error al agregar miembro en Microsoft 365")
            
        log = AuditLog(
            tenant_id=tenant.id,
            operator_email=operator.get("email"),
            action="ADD_GROUP_MEMBER",
            target_upn=f"Grupo: {group_id}",
            status="SUCCESS",
            details=f"Usuario {data.user_graph_id} añadido"
        )
        db.add(log)
        await db.commit()
        return {"message": "Usuario añadido al grupo"}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/groups/{group_id}/members/{user_graph_id}")
async def remove_group_member_api(group_id: str, user_graph_id: str, tenant_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    operator = request.session.get("user")
    if not operator or operator.get("role") not in ["ADMIN", "SUPERADMIN"]:
        raise HTTPException(status_code=403, detail="No autorizado")
        
    import uuid
    try:
        tenant_uuid = uuid.UUID(tenant_id)
        stmt = select(Tenant).where(Tenant.id == tenant_uuid)
        res = await db.execute(stmt)
        tenant = res.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant no encontrado")
            
        graph = get_graph_service_for_tenant(tenant)
        
        success = await graph.remove_group_member(group_id, user_graph_id)
        if not success:
            raise HTTPException(status_code=400, detail="Error al remover miembro en Microsoft 365")
            
        log = AuditLog(
            tenant_id=tenant.id,
            operator_email=operator.get("email"),
            action="REMOVE_GROUP_MEMBER",
            target_upn=f"Grupo: {group_id}",
            status="SUCCESS",
            details=f"Usuario {user_graph_id} removido"
        )
        db.add(log)
        await db.commit()
        return {"message": "Usuario removido del grupo"}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

# --- Shared Mailboxes Endpoints ---

@app.get("/api/tenants/{id}/shared-mailboxes")
async def get_tenant_shared_mailboxes_api(id: str, request: Request, db: AsyncSession = Depends(get_db)):
    operator = request.session.get("user")
    if not operator:
        raise HTTPException(status_code=401, detail="No autenticado")
        
    import uuid
    try:
        tenant_uuid = uuid.UUID(id)
        stmt = select(Tenant).where(Tenant.id == tenant_uuid)
        res = await db.execute(stmt)
        tenant = res.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant no encontrado")
            
        graph = get_graph_service_for_tenant(tenant)
        mailboxes = await graph.fetch_shared_mailboxes()
        return mailboxes
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/tenants/{tenant_id}/shared-mailboxes/{user_id}/delegates")
async def get_shared_mailbox_delegates_api(tenant_id: str, user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    operator = request.session.get("user")
    if not operator:
        raise HTTPException(status_code=401, detail="No autenticado")
        
    import uuid
    try:
        tenant_uuid = uuid.UUID(tenant_id)
        stmt = select(Tenant).where(Tenant.id == tenant_uuid)
        res = await db.execute(stmt)
        tenant = res.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant no encontrado")
            
        graph = get_graph_service_for_tenant(tenant)
        delegates = await graph.fetch_mailbox_delegates(user_id)
        return delegates
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

class AddDelegateSchema(BaseModel):
    delegate_email: str
    delegate_name: str
    role: str = "editor"

@app.post("/api/tenants/{tenant_id}/shared-mailboxes/{user_id}/delegates")
async def add_shared_mailbox_delegate_api(tenant_id: str, user_id: str, data: AddDelegateSchema, request: Request, db: AsyncSession = Depends(get_db)):
    operator = request.session.get("user")
    if not operator or operator.get("role") not in ["ADMIN", "SUPERADMIN"]:
        raise HTTPException(status_code=403, detail="No autorizado")
        
    import uuid
    try:
        tenant_uuid = uuid.UUID(tenant_id)
        stmt = select(Tenant).where(Tenant.id == tenant_uuid)
        res = await db.execute(stmt)
        tenant = res.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant no encontrado")
            
        graph = get_graph_service_for_tenant(tenant)
        
        success = await graph.add_mailbox_delegate(user_id, data.delegate_email, data.delegate_name, data.role)
        if not success:
            raise HTTPException(status_code=400, detail="Error al agregar delegado en Microsoft 365")
            
        log = AuditLog(
            tenant_id=tenant.id,
            operator_email=operator.get("email"),
            action="ADD_DELEGATE",
            target_upn=f"Mailbox UPN/ID: {user_id}",
            status="SUCCESS",
            details=f"Delegado {data.delegate_email} (Rol: {data.role}) añadido"
        )
        db.add(log)
        await db.commit()
        return {"message": "Delegado agregado correctamente"}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/tenants/{tenant_id}/shared-mailboxes/{user_id}/delegates/{delegate_id}")
async def remove_shared_mailbox_delegate_api(tenant_id: str, user_id: str, delegate_id: str, delegate_email: str, request: Request, db: AsyncSession = Depends(get_db)):
    operator = request.session.get("user")
    if not operator or operator.get("role") not in ["ADMIN", "SUPERADMIN"]:
        raise HTTPException(status_code=403, detail="No autorizado")
        
    import uuid
    try:
        tenant_uuid = uuid.UUID(tenant_id)
        stmt = select(Tenant).where(Tenant.id == tenant_uuid)
        res = await db.execute(stmt)
        tenant = res.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant no encontrado")
            
        graph = get_graph_service_for_tenant(tenant)
        
        success = await graph.remove_mailbox_delegate(user_id, delegate_id)
        if not success:
            raise HTTPException(status_code=400, detail="Error al remover delegado en Microsoft 365")
            
        log = AuditLog(
            tenant_id=tenant.id,
            operator_email=operator.get("email"),
            action="REMOVE_DELEGATE",
            target_upn=f"Mailbox UPN/ID: {user_id}",
            status="SUCCESS",
            details=f"Delegado {delegate_email} (ID: {delegate_id}) removido"
        )
        db.add(log)
        await db.commit()
        return {"message": "Delegado removido correctamente"}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

class CreateSharedMailboxSchema(BaseModel):
    display_name: str
    alias: str
    domain: str

@app.post("/api/tenants/{id}/shared-mailboxes/create")
async def create_shared_mailbox_api(id: str, data: CreateSharedMailboxSchema, request: Request, db: AsyncSession = Depends(get_db)):
    operator = request.session.get("user")
    if not operator or operator.get("role") not in ["ADMIN", "SUPERADMIN"]:
        raise HTTPException(status_code=403, detail="No autorizado")
        
    import uuid
    try:
        tenant_uuid = uuid.UUID(id)
        stmt = select(Tenant).where(Tenant.id == tenant_uuid)
        res = await db.execute(stmt)
        tenant = res.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant no encontrado")
            
        graph = get_graph_service_for_tenant(tenant)
        
        success = await graph.create_shared_mailbox(data.display_name, data.alias, data.domain)
        if not success:
            raise HTTPException(status_code=400, detail="Error al crear buzón compartido en Microsoft 365")
            
        log = AuditLog(
            tenant_id=tenant.id,
            operator_email=operator.get("email"),
            action="CREATE_SHARED_MAILBOX",
            target_upn=f"{data.alias}@{data.domain}",
            status="SUCCESS",
            details=f"Buzón compartido '{data.display_name}' creado"
        )
        db.add(log)
        await db.commit()
        return {"message": "Buzón compartido creado correctamente"}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

# --- Compliance Engine Endpoints ---

@app.get("/api/tenants/{id}/compliance-report")
async def get_tenant_compliance_report_api(id: str, request: Request, db: AsyncSession = Depends(get_db)):
    operator = request.session.get("user")
    if not operator:
        raise HTTPException(status_code=401, detail="No autenticado")
        
    import uuid
    try:
        tenant_uuid = uuid.UUID(id)
        stmt = select(Tenant).where(Tenant.id == tenant_uuid)
        res = await db.execute(stmt)
        tenant = res.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant no encontrado")
            
        graph = get_graph_service_for_tenant(tenant)
        metrics = await graph.fetch_security_compliance_metrics()
        return metrics
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# --- Inactive Users & Auto Replies Endpoints ---

@app.get("/api/tenants/{id}/inactive-licenses")
async def get_tenant_inactive_licenses_api(id: str, request: Request, db: AsyncSession = Depends(get_db)):
    operator = request.session.get("user")
    if not operator:
        raise HTTPException(status_code=401, detail="No autenticado")
        
    import uuid
    try:
        tenant_uuid = uuid.UUID(id)
        stmt = select(Tenant).where(Tenant.id == tenant_uuid)
        res = await db.execute(stmt)
        tenant = res.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant no encontrado")
            
        graph = get_graph_service_for_tenant(tenant)
        inactive = await graph.fetch_inactive_users_report(days=30)
        return inactive
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/tenants/{tenant_id}/users/{user_id}/auto-replies")
async def get_user_auto_replies_api(tenant_id: str, user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    operator = request.session.get("user")
    if not operator:
        raise HTTPException(status_code=401, detail="No autenticado")
        
    import uuid
    try:
        tenant_uuid = uuid.UUID(tenant_id)
        stmt = select(Tenant).where(Tenant.id == tenant_uuid)
        res = await db.execute(stmt)
        tenant = res.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant no encontrado")
            
        graph = get_graph_service_for_tenant(tenant)
        
        user_stmt = select(User).where(User.id == uuid.UUID(user_id))
        user_res = await db.execute(user_stmt)
        db_user = user_res.scalar_one_or_none()
        if not db_user:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
            
        replies = await graph.fetch_mailbox_automatic_replies(db_user.graph_id)
        return replies
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

class AutoRepliesSchema(BaseModel):
    status: str
    externalAudience: str = "all"
    internalReplyMessage: str
    externalReplyMessage: str
    scheduledStartDateTime: dict | None = None
    scheduledEndDateTime: dict | None = None

@app.post("/api/tenants/{tenant_id}/users/{user_id}/auto-replies")
async def update_user_auto_replies_api(tenant_id: str, user_id: str, data: AutoRepliesSchema, request: Request, db: AsyncSession = Depends(get_db)):
    operator = request.session.get("user")
    if not operator or operator.get("role") not in ["ADMIN", "SUPERADMIN"]:
        raise HTTPException(status_code=403, detail="No autorizado")
        
    import uuid
    try:
        tenant_uuid = uuid.UUID(tenant_id)
        stmt = select(Tenant).where(Tenant.id == tenant_uuid)
        res = await db.execute(stmt)
        tenant = res.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant no encontrado")
            
        graph = get_graph_service_for_tenant(tenant)
        
        user_stmt = select(User).where(User.id == uuid.UUID(user_id))
        user_res = await db.execute(user_stmt)
        db_user = user_res.scalar_one_or_none()
        if not db_user:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
            
        settings = {
            "status": data.status,
            "externalAudience": data.externalAudience,
            "internalReplyMessage": data.internalReplyMessage,
            "externalReplyMessage": data.externalReplyMessage
        }
        if data.scheduledStartDateTime:
            settings["scheduledStartDateTime"] = data.scheduledStartDateTime
        if data.scheduledEndDateTime:
            settings["scheduledEndDateTime"] = data.scheduledEndDateTime
            
        success = await graph.update_mailbox_automatic_replies(db_user.graph_id, settings)
        if not success:
            raise HTTPException(status_code=400, detail="Error al actualizar respuestas automáticas en Microsoft 365")
            
        log = AuditLog(
            tenant_id=tenant.id,
            operator_email=operator.get("email"),
            action="UPDATE_AUTO_REPLIES",
            target_upn=db_user.upn,
            status="SUCCESS",
            details=f"Fuera de oficina: {data.status}"
        )
        db.add(log)
        await db.commit()
        return {"message": "Respuestas automáticas actualizadas correctamente"}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

# --- Billing Endpoints ---



@app.post("/api/billing")
async def save_billing(data: BillingSchema, request: Request, db: AsyncSession = Depends(get_db)):
    from app.db.models import LicenseBilling
    user = request.session.get("user")
    if not user or user.get("role") not in ["ADMIN", "SUPERADMIN", "FINANCE"]:
        raise HTTPException(status_code=403)

    billing = None
    if data.id:
        stmt = select(LicenseBilling).where(LicenseBilling.id == uuid.UUID(data.id))
        res = await db.execute(stmt)
        billing = res.scalar_one_or_none()
    
    # Parse dates
    exp_date = None
    s_date = None
    e_date = None
    try:
        if data.expiration_date: exp_date = datetime.fromisoformat(data.expiration_date.replace("Z", ""))
        if data.start_date: s_date = datetime.fromisoformat(data.start_date.replace("Z", ""))
        if data.end_date: e_date = datetime.fromisoformat(data.end_date.replace("Z", ""))
    except:
        pass

    if billing:
        billing.sku_id = data.sku_id
        billing.sku_part_number = data.sku_part_number
        billing.unit_cost = data.unit_cost
        billing.quantity = data.quantity
        billing.invoice_number = data.invoice_number
        billing.expiration_date = exp_date
        billing.start_date = s_date
        billing.end_date = e_date
        billing.currency = data.currency
        billing.notes = data.notes
    else:
        billing = LicenseBilling(
            tenant_id=uuid.UUID(data.tenant_id),
            sku_id=data.sku_id,
            sku_part_number=data.sku_part_number,
            unit_cost=data.unit_cost,
            quantity=data.quantity,
            invoice_number=data.invoice_number,
            expiration_date=exp_date,
            start_date=s_date,
            end_date=e_date,
            currency=data.currency,
            notes=data.notes
        )
        db.add(billing)
    
    await db.commit()
    return {"message": "Registro de facturación guardado"}

@app.delete("/api/billing/{id}")
async def delete_billing(id: str, request: Request, db: AsyncSession = Depends(get_db)):
    from app.db.models import LicenseBilling
    user = request.session.get("user")
    if not user or user.get("role") not in ["ADMIN", "SUPERADMIN", "FINANCE"]:
        raise HTTPException(status_code=403)
    
    stmt = select(LicenseBilling).where(LicenseBilling.id == uuid.UUID(id))
    res = await db.execute(stmt)
    billing = res.scalar_one_or_none()
    
    if billing:
        await db.delete(billing)
        await db.commit()
    return {"message": "Registro eliminado"}

# --- Notification Config Endpoints ---

@app.get("/notifications-settings")
async def notifications_settings_page(request: Request, db: AsyncSession = Depends(get_db)):
    from app.db.models import NotificationConfig
    user = request.session.get("user")
    if not user or user.get("role") != "SUPERADMIN":
        return RedirectResponse(url="/")
    
    stmt = select(NotificationConfig).where(NotificationConfig.id == 1)
    res = await db.execute(stmt)
    config = res.scalar_one_or_none()
    
    return templates.TemplateResponse(request=request, name="notifications_settings.html", context={
        "config": config,
        "current_user": user
    })

@app.post("/api/notifications/config")
async def save_notification_config(data: NotificationConfigSchema, request: Request, db: AsyncSession = Depends(get_db)):
    from app.db.models import NotificationConfig
    user = request.session.get("user")
    if not user or user.get("role") != "SUPERADMIN":
        raise HTTPException(status_code=403)

    stmt = select(NotificationConfig).where(NotificationConfig.id == 1)
    res = await db.execute(stmt)
    config = res.scalar_one_or_none()
    
    if not config:
        config = NotificationConfig(id=1)
        db.add(config)
    
    config.telegram_enabled = data.telegram_enabled
    config.telegram_token = data.telegram_token
    config.telegram_chat_id = data.telegram_chat_id
    config.email_enabled = data.email_enabled
    config.smtp_host = data.smtp_host
    config.smtp_port = data.smtp_port
    config.smtp_user = data.smtp_user
    if data.smtp_pass:
        config.smtp_pass_encrypted = crypto.encrypt(data.smtp_pass)
    config.email_from = data.email_from
    config.notify_days_before = data.notify_days_before
    
    await db.commit()
    return {"message": "Configuración guardada"}

@app.post("/api/notifications/test")
async def test_notifications(request: Request, db: AsyncSession = Depends(get_db)):
    user = request.session.get("user")
    if not user or user.get("role") != "SUPERADMIN":
        raise HTTPException(status_code=403)
    
    service = NotificationService(db)
    config = await service.get_config()
    if not config:
        raise HTTPException(status_code=404, detail="Configuración no encontrada")
    
    msg = "<b>✅ PRUEBA DE NOTIFICACIÓN</b>\n\nEste es un mensaje de prueba del sistema 365 License Manager."
    
    if config.telegram_enabled:
        await service.send_telegram(msg, config)
    
    if config.email_enabled:
        await service.send_email("Prueba de Notificación - 365 License Manager", msg, config.smtp_user, config)
        
    return {"message": "Notificaciones de prueba enviadas"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
