import msal
import aiohttp
import asyncio
import logging
from typing import List, Dict, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class GraphService:
    def __init__(self, tenant_id: str, client_id: str, client_secret: str):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.authority = f"https://login.microsoftonline.com/{tenant_id}"
        self.scope = ["https://graph.microsoft.com/.default"]
        self.base_url = "https://graph.microsoft.com/v1.0"
        
        self.app = msal.ConfidentialClientApplication(
            client_id,
            authority=self.authority,
            client_credential=client_secret
        )

    async def get_token(self) -> str:
        result = self.app.acquire_token_for_client(scopes=self.scope)
        if "access_token" in result:
            return result["access_token"]
        else:
            logger.error(f"Error acquired token: {result.get('error_description')}")
            raise Exception("Could not acquire token from Microsoft Graph")

    async def fetch_users(self) -> List[Dict[str, Any]]:
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}"}
        users = []
        url = f"{self.base_url}/users?$select=id,displayName,userPrincipalName,accountEnabled"

        async with aiohttp.ClientSession() as session:
            while url:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        users.extend(data.get("value", []))
                        url = data.get("@odata.nextLink")
                    elif response.status == 429:
                        retry_after = int(response.headers.get("Retry-After", 5))
                        await asyncio.sleep(retry_after)
                        continue
                    else:
                        logger.error(f"Error fetching users: {await response.text()}")
                        break
        return users

    async def fetch_licenses(self) -> List[Dict[str, Any]]:
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{self.base_url}/subscribedSkus"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("value", [])
                else:
                    logger.error(f"Error fetching licenses: {await response.text()}")
                    return []

    async def fetch_user_license_details(self, user_id: str) -> List[Dict[str, Any]]:
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{self.base_url}/users/{user_id}/licenseDetails"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("value", [])
                return []
