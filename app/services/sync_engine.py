from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import update, delete
from datetime import datetime
from sqlalchemy.orm import selectinload, joinedload
from app.db.models import Tenant, User, License, UserLicense, SyncLog
from app.services.graph_service import GraphService
from app.services.notification_service import NotificationService
from app.core.config import crypto
import logging

logger = logging.getLogger(__name__)

class SyncEngine:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def sync_tenant(self, tenant_id_uuid):
        # 1. Obtener una instancia fresca del tenant en esta sesión
        stmt_t = select(Tenant).where(Tenant.id == tenant_id_uuid)
        res_t = await self.db.execute(stmt_t)
        tenant = res_t.scalar_one()

        # 2. Start Logging
        log = SyncLog(tenant_id=tenant.id, status="RUNNING")
        self.db.add(log)
        await self.db.commit()

        try:
            # Decrypt secret
            secret = crypto.decrypt(tenant.client_secret_encrypted)
            graph = GraphService(tenant.tenant_id, tenant.client_id, secret)

            # 2. Sync Licenses (Subscribed SKUs)
            ms_licenses = await graph.fetch_licenses()
            curr_license_map = {}
            for item in ms_licenses:
                sku_id = item["skuId"]
                stmt = select(License).where(License.tenant_id == tenant.id, License.sku_id == sku_id)
                res = await self.db.execute(stmt)
                db_license = res.scalar_one_or_none()

                if not db_license:
                    db_license = License(
                        tenant_id=tenant.id,
                        sku_id=sku_id,
                        sku_part_number=item["skuPartNumber"]
                    )
                    self.db.add(db_license)
                
                db_license.total_units = item.get("prepaidUnits", {}).get("enabled", 0)
                db_license.consumed_units = item.get("consumedUnits", 0)
                await self.db.flush()
                curr_license_map[sku_id] = db_license.id

            # Fetch Global Admins to check for security alerts
            try:
                ms_admins = await graph.fetch_global_administrators()
                ms_admin_ids = {adm["id"] for adm in ms_admins}
            except Exception as admin_err:
                logger.error(f"Error fetching global admins during sync: {admin_err}")
                ms_admin_ids = set()

            # 3. Sync Users
            ms_users = await graph.fetch_users()
            processed_users = 0
            for u in ms_users:
                # Cargamos al usuario por su ID de Graph
                stmt = select(User).where(User.graph_id == u["id"])
                res = await self.db.execute(stmt)
                db_user = res.scalar_one_or_none()

                is_now_admin = u["id"] in ms_admin_ids

                if not db_user:
                    db_user = User(
                        tenant_id=tenant.id,
                        graph_id=u["id"],
                        upn=u["userPrincipalName"],
                        display_name=u["displayName"],
                        is_global_admin=is_now_admin
                    )
                    self.db.add(db_user)
                    await self.db.flush()
                else:
                    # Alertas de seguridad si se asignó rol de Admin Global
                    if is_now_admin and not db_user.is_global_admin:
                        try:
                            notification_service = NotificationService(self.db)
                            config = await notification_service.get_config()
                            if config:
                                alert_msg = (
                                    f"<b>⚠️ ALERTA DE SEGURIDAD CRÍTICA</b>\n\n"
                                    f"Se ha detectado un nuevo <b>Administrador Global</b> en el cliente <b>{tenant.name}</b>:\n\n"
                                    f"• Nombre: {db_user.display_name}\n"
                                    f"• Correo/UPN: <code>{db_user.upn}</code>\n"
                                    f"• Fecha/Hora: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
                                )
                                if config.telegram_enabled:
                                    await notification_service.send_telegram(alert_msg, config)
                                if config.email_enabled:
                                    await notification_service.send_email(
                                        f"ALERTA SEGURIDAD: Nuevo Global Admin en {tenant.name}",
                                        alert_msg,
                                        config.smtp_user,
                                        config
                                    )
                        except Exception as alert_err:
                            logger.error(f"Error sending security alert: {alert_err}")

                    db_user.upn = u["userPrincipalName"]
                    db_user.display_name = u["displayName"]
                    db_user.is_active = u.get("accountEnabled", True)
                    db_user.is_global_admin = is_now_admin
                
                db_user.last_seen = datetime.utcnow()
                
                # 4. Sync User Licenses (Consultamos manualmente para evitar errores de relación)
                user_lic_details = await graph.fetch_user_license_details(db_user.graph_id)
                ms_sku_ids = [l["skuId"] for l in user_lic_details]
                
                # Obtener licencias actuales en DB para este usuario
                stmt_ul = select(UserLicense).where(UserLicense.user_id == db_user.id).options(joinedload(UserLicense.license))
                res_ul = await self.db.execute(stmt_ul)
                db_user_licenses = res_ul.scalars().all()
                
                # Eliminar las que ya no están
                for db_ul in db_user_licenses:
                    if db_ul.license.sku_id not in ms_sku_ids:
                        self.db.delete(db_ul)

                # Añadir nuevas
                existing_sku_ids = [ul.license.sku_id for ul in db_user_licenses]
                for sku_id in ms_sku_ids:
                    if sku_id in curr_license_map and sku_id not in existing_sku_ids:
                        new_ul = UserLicense(user_id=db_user.id, license_id=curr_license_map[sku_id])
                        self.db.add(new_ul)
                
                processed_users += 1
                if processed_users % 10 == 0:
                    await self.db.flush() # Flush intermedio para estabilidad

            # 5. Success
            tenant.last_sync = datetime.utcnow()
            log.status = "SUCCESS"
            log.users_processed = processed_users
            
        except Exception as e:
            logger.exception(f"Error syncing tenant {tenant.name}")
            log.status = "FAILED"
            log.details = str(e)
        
        log.end_time = datetime.utcnow()
        await self.db.commit()
