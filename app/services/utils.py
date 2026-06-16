import os
from app.core.config import crypto
from app.services.graph_service import GraphService

def get_graph_service_for_tenant(tenant) -> GraphService:
    """
    Retorna una instancia de GraphService inicializada con las credenciales específicas
    del tenant si existen, o con las credenciales globales de la aplicación multi-tenant.
    """
    if tenant.client_id and tenant.client_secret_encrypted:
        # Modelo anterior: Credenciales específicas del tenant
        secret = crypto.decrypt(tenant.client_secret_encrypted)
        return GraphService(tenant.tenant_id, tenant.client_id, secret)
    else:
        # Modelo nuevo: Credenciales globales de la aplicación multi-tenant
        msp_client_id = os.getenv("MSP_CLIENT_ID") or os.getenv("AZURE_AD_CLIENT_ID")
        msp_client_secret = os.getenv("MSP_CLIENT_SECRET") or os.getenv("AZURE_AD_CLIENT_SECRET")
        if not msp_client_id or not msp_client_secret:
            raise ValueError(
                "Credenciales globales de la aplicación multi-tenant no configuradas en el entorno (.env)"
            )
        return GraphService(tenant.tenant_id, msp_client_id, msp_client_secret)
