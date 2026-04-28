from sqlalchemy import Column, String, Integer, DateTime, Boolean, ForeignKey, Table, Text, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, declarative_base
import uuid
from datetime import datetime

Base = declarative_base()

class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    tenant_id = Column(String(255), unique=True, nullable=False)
    client_id = Column(String(255), nullable=False)
    client_secret_encrypted = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True)
    legal_name = Column(String(255), nullable=True) # Razón Social
    tax_id = Column(String(50), nullable=True)      # RUT
    contact_email = Column(String(255), nullable=True) # Email para notificaciones
    send_notifications = Column(Boolean, default=True) # Activar/Desactivar alertas
    created_at = Column(DateTime, default=datetime.utcnow)
    last_sync = Column(DateTime, nullable=True)

    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan")
    licenses = relationship("License", back_populates="tenant", cascade="all, delete-orphan")
    sync_logs = relationship("SyncLog", back_populates="tenant", cascade="all, delete-orphan")
    billing_records = relationship("LicenseBilling", back_populates="tenant", cascade="all, delete-orphan")

class PlatformUser(Base):
    __tablename__ = "platform_users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False) # Correo UPN de M365 para login
    name = Column(String(255))
    role = Column(String(50), default="VIEWER") # SUPERADMIN, ADMIN, FINANCE, VIEWER
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    graph_id = Column(String(255), unique=True, nullable=False)  # Object ID in Azure
    upn = Column(String(255), nullable=False)
    display_name = Column(String(255))
    is_active = Column(Boolean, default=True)
    last_seen = Column(DateTime, default=datetime.utcnow)

    tenant = relationship("Tenant", back_populates="users")
    licenses = relationship("UserLicense", back_populates="user", cascade="all, delete-orphan")

class License(Base):
    __tablename__ = "licenses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    sku_id = Column(String(255), nullable=False)
    sku_part_number = Column(String(255), nullable=False)
    total_units = Column(Integer, default=0)
    consumed_units = Column(Integer, default=0)
    
    tenant = relationship("Tenant", back_populates="licenses")
    user_assignments = relationship("UserLicense", back_populates="license")

class UserLicense(Base):
    __tablename__ = "user_licenses"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    license_id = Column(UUID(as_uuid=True), ForeignKey("licenses.id"), primary_key=True)
    assigned_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

    user = relationship("User", back_populates="licenses")
    license = relationship("License", back_populates="user_assignments")

class SyncLog(Base):
    __tablename__ = "sync_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    start_time = Column(DateTime, default=datetime.utcnow)
    end_time = Column(DateTime, nullable=True)
    status = Column(String(50))  # SUCCESS, FAILED, RUNNING
    details = Column(Text, nullable=True)
    users_processed = Column(Integer, default=0)
    errors_count = Column(Integer, default=0)

    tenant = relationship("Tenant", back_populates="sync_logs")

class LicenseBilling(Base):
    __tablename__ = "license_billing"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    sku_id = Column(String(255), nullable=False) # ID de Microsoft
    sku_part_number = Column(String(255)) # Nombre técnico (ej: O365_BUSINESS_PREMIUM)
    unit_cost = Column(Numeric(10, 2), default=0.0)
    quantity = Column(Integer, default=0)
    invoice_number = Column(String(100))
    expiration_date = Column(DateTime, nullable=True)
    start_date = Column(DateTime, nullable=True)
    end_date = Column(DateTime, nullable=True)
    currency = Column(String(10), default="USD")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    tenant = relationship("Tenant", back_populates="billing_records")

class NotificationConfig(Base):
    __tablename__ = "notification_config"

    id = Column(Integer, primary_key=True)
    # Telegram
    telegram_enabled = Column(Boolean, default=False)
    telegram_token = Column(Text, nullable=True)
    telegram_chat_id = Column(String(100), nullable=True)
    
    # Email (SMTP)
    email_enabled = Column(Boolean, default=False)
    smtp_host = Column(String(255), nullable=True)
    smtp_port = Column(Integer, default=587)
    smtp_user = Column(String(255), nullable=True)
    smtp_pass_encrypted = Column(Text, nullable=True)
    email_from = Column(String(255), nullable=True)

    # Frecuencia
    notify_days_before = Column(String(100), default="30,15,5") # Días antes del vencimiento
    
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
