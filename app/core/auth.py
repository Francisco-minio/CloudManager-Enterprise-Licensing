from fastapi import Request, HTTPException
from msal import ConfidentialClientApplication
from app.core.config import settings

# Esta configuración es para el LOGIN del Admin al Dashboard
msal_app = ConfidentialClientApplication(
    settings.AZURE_AD_CLIENT_ID,
    authority=f"https://login.microsoftonline.com/{settings.AZURE_AD_TENANT_ID}",
    client_credential=settings.AZURE_AD_CLIENT_SECRET,
)

def get_auth_url(redirect_uri: str):
    return msal_app.get_authorization_request_url(
        scopes=["User.Read"],
        redirect_uri=redirect_uri
    )

async def get_token_from_code(code: str, redirect_uri: str):
    result = msal_app.acquire_token_by_authorization_code(
        code,
        scopes=["User.Read"],
        redirect_uri=redirect_uri
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result.get("error_description"))
    return result.get("id_token_claims")
