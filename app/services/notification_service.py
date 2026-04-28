import logging
import aiohttp
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from sqlalchemy.future import select
from app.db.models import NotificationConfig, Tenant, LicenseBilling
from app.core.config import crypto
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

class NotificationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_config(self) -> NotificationConfig:
        stmt = select(NotificationConfig).where(NotificationConfig.id == 1)
        res = await self.db.execute(stmt)
        return res.scalar_one_or_none()

    async def send_telegram(self, message: str, config: NotificationConfig):
        if not config or not config.telegram_enabled or not config.telegram_token or not config.telegram_chat_id:
            return

        url = f"https://api.telegram.org/bot{config.telegram_token}/sendMessage"
        payload = {
            "chat_id": config.telegram_chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        logger.error(f"Error enviando Telegram: {await resp.text()}")
        except Exception as e:
            logger.exception("Error en send_telegram")

    async def send_email(self, subject: str, body: str, to_email: str, config: NotificationConfig):
        if not config or not config.email_enabled or not config.smtp_host:
            return

        try:
            msg = MIMEMultipart()
            msg['From'] = config.email_from
            msg['To'] = to_email
            msg['Subject'] = subject

            msg.attach(MIMEText(body, 'html'))

            password = crypto.decrypt(config.smtp_pass_encrypted) if config.smtp_pass_encrypted else ""

            # Ejecutamos el envío de mail (bloqueante) en un thread si fuera necesario, 
            # pero para simplificar aquí lo haremos directo. 
            # NOTA: En producción real se recomienda usar un aiosmtplib.
            with smtplib.SMTP(config.smtp_host, config.smtp_port) as server:
                server.starttls()
                server.login(config.smtp_user, password)
                server.send_message(msg)
                
        except Exception as e:
            logger.exception(f"Error enviando Email a {to_email}")

    async def check_expirations_and_notify(self):
        config = await self.get_config()
        if not config:
            return

        # Días a notificar
        try:
            days_to_notify = [int(d.strip()) for d in config.notify_days_before.split(",")]
        except:
            days_to_notify = [30, 15, 5]

        # Obtener todos los registros de billing con sus tenants
        from sqlalchemy.orm import joinedload
        stmt = select(LicenseBilling).options(joinedload(LicenseBilling.tenant))
        res = await self.db.execute(stmt)
        billings = res.scalars().all()

        today = datetime.utcnow().date()

        for billing in billings:
            if not billing.end_date:
                continue
            
            days_left = (billing.end_date.date() - today).days
            
            if days_left in days_to_notify or days_left == 0:
                # Preparar mensaje
                status = "VENCIDO" if days_left <= 0 else f"vence en {days_left} días"
                msg_html = f"""
                <b>🚨 ALERTA DE VENCIMIENTO DE LICENCIA</b>
                <pre>
                Cliente: {billing.tenant.name}
                Licencia: {billing.sku_part_number}
                Cantidad: {billing.quantity}
                Factura: {billing.invoice_number}
                Fecha Fin: {billing.end_date.strftime('%d/%m/%Y')}
                Estado: {status}
                </pre>
                """
                
                # 1. Notificar por Telegram
                if config.telegram_enabled:
                    await self.send_telegram(msg_html, config)
                
                # 2. Notificar por Email
                if config.email_enabled:
                    # Email al administrador (global)
                    if config.email_from:
                        await self.send_email(
                            f"Alerta: Licencia de {billing.tenant.name} {status}",
                            msg_html,
                            config.smtp_user, # Por defecto al mismo user SMTP
                            config)
                    
                    # Email al contacto del cliente (si tiene habilitado)
                    if billing.tenant.send_notifications and billing.tenant.contact_email:
                        await self.send_email(
                            f"Aviso de Vencimiento: {billing.sku_part_number}",
                            msg_html,
                            billing.tenant.contact_email,
                            config)
