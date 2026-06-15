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

    async def update_user_status(self, user_id: str, enabled: bool) -> bool:
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        url = f"{self.base_url}/users/{user_id}"
        payload = {"accountEnabled": enabled}
        async with aiohttp.ClientSession() as session:
            async with session.patch(url, headers=headers, json=payload) as response:
                if response.status in [200, 204]:
                    return True
                logger.error(f"Error updating user status: {await response.text()}")
                return False

    async def reset_password(self, user_id: str, new_password: str, force_change: bool = True) -> bool:
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        url = f"{self.base_url}/users/{user_id}"
        payload = {
            "passwordProfile": {
                "forceChangePasswordNextSignIn": force_change,
                "password": new_password
            }
        }
        async with aiohttp.ClientSession() as session:
            async with session.patch(url, headers=headers, json=payload) as response:
                if response.status in [200, 204]:
                    return True
                logger.error(f"Error resetting password: {await response.text()}")
                return False

    async def revoke_sessions(self, user_id: str) -> bool:
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        url = f"{self.base_url}/users/{user_id}/revokeSignInSessions"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers) as response:
                if response.status in [200, 204]:
                    return True
                logger.error(f"Error revoking sessions: {await response.text()}")
                return False

    async def remove_licenses(self, user_id: str, sku_ids: List[str]) -> bool:
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        url = f"{self.base_url}/users/{user_id}/assignLicense"
        payload = {
            "addLicenses": [],
            "removeLicenses": sku_ids
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status in [200, 204]:
                    return True
                logger.error(f"Error removing licenses: {await response.text()}")
                return False

    async def assign_user_licenses(self, user_id: str, add_licenses: List[Dict[str, Any]], remove_licenses: List[str]) -> bool:
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        url = f"{self.base_url}/users/{user_id}/assignLicense"
        payload = {
            "addLicenses": add_licenses,
            "removeLicenses": remove_licenses
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status in [200, 204]:
                    return True
                logger.error(f"Error assigning user licenses: {await response.text()}")
                return False

    async def fetch_groups(self) -> List[Dict[str, Any]]:
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{self.base_url}/groups?$select=id,displayName,description,groupTypes,securityEnabled"
        groups = []
        async with aiohttp.ClientSession() as session:
            while url:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        groups.extend(data.get("value", []))
                        url = data.get("@odata.nextLink")
                    else:
                        logger.error(f"Error fetching groups: {await response.text()}")
                        break
        return groups

    async def fetch_group_members(self, group_id: str) -> List[Dict[str, Any]]:
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{self.base_url}/groups/{group_id}/members?$select=id,displayName,userPrincipalName"
        members = []
        async with aiohttp.ClientSession() as session:
            while url:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        members.extend(data.get("value", []))
                        url = data.get("@odata.nextLink")
                    else:
                        logger.error(f"Error fetching group members: {await response.text()}")
                        break
        return members

    async def add_group_member(self, group_id: str, user_graph_id: str) -> bool:
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        url = f"{self.base_url}/groups/{group_id}/members/$ref"
        payload = {"@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{user_graph_id}"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status in [200, 204, 201]:
                    return True
                logger.error(f"Error adding group member: {await response.text()}")
                return False

    async def remove_group_member(self, group_id: str, user_graph_id: str) -> bool:
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{self.base_url}/groups/{group_id}/members/{user_graph_id}/$ref"
        async with aiohttp.ClientSession() as session:
            async with session.delete(url, headers=headers) as response:
                if response.status in [200, 204]:
                    return True
                logger.error(f"Error removing group member: {await response.text()}")
                return False

    async def fetch_user_extended_profile(self, user_graph_id: str) -> Dict[str, Any]:
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}"}
        fields = (
            "id,displayName,userPrincipalName,accountEnabled,createdDateTime,userType,"
            "jobTitle,department,officeLocation,mobilePhone,usageLocation,"
            "preferredLanguage,mailNickname,mail,streetAddress,city,state,country"
        )
        url = f"{self.base_url}/users/{user_graph_id}?$select={fields}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                logger.error(f"Error fetching user extended profile: {await response.text()}")
                return {}

    async def fetch_user_member_of(self, user_graph_id: str) -> List[Dict[str, Any]]:
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{self.base_url}/users/{user_graph_id}/memberOf"
        memberships = []

        async with aiohttp.ClientSession() as session:
            while url:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        memberships.extend(data.get("value", []))
                        url = data.get("@odata.nextLink")
                    else:
                        logger.error(f"Error fetching user memberships: {await response.text()}")
                        break
        return memberships

    async def fetch_user_manager(self, user_graph_id: str) -> Any:
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{self.base_url}/users/{user_graph_id}/manager"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                return None

    async def fetch_shared_mailboxes(self) -> List[Dict[str, Any]]:
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{self.base_url}/users?$select=id,displayName,userPrincipalName,mailboxSettings&$top=999"
        shared = []
        async with aiohttp.ClientSession() as session:
            while url:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        for user in data.get("value", []):
                            m_settings = user.get("mailboxSettings") or {}
                            if m_settings.get("userPurpose") == "shared":
                                shared.append({
                                    "id": user.get("id"),
                                    "displayName": user.get("displayName"),
                                    "userPrincipalName": user.get("userPrincipalName")
                                })
                        url = data.get("@odata.nextLink")
                    else:
                        logger.error(f"Error fetching shared mailboxes: {await response.text()}")
                        break
        return shared

    async def fetch_mailbox_delegates(self, user_id: str) -> List[Dict[str, Any]]:
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{self.base_url}/users/{user_id}/mailboxSettings/delegates"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("value", [])
                logger.error(f"Error fetching mailbox delegates: {await response.text()}")
                return []

    async def add_mailbox_delegate(self, user_id: str, delegate_email: str, delegate_name: str, role: str) -> bool:
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        url = f"{self.base_url}/users/{user_id}/mailboxSettings/delegates"
        payload = {
            "value": [
                {
                    "user": {
                        "emailAddress": {
                            "address": delegate_email,
                            "name": delegate_name
                        }
                    },
                    "role": role
                }
            ]
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status in [200, 201, 204]:
                    return True
                logger.error(f"Error adding delegate: {await response.text()}")
                return False

    async def remove_mailbox_delegate(self, user_id: str, delegate_id: str) -> bool:
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{self.base_url}/users/{user_id}/mailboxSettings/delegates/{delegate_id}"
        async with aiohttp.ClientSession() as session:
            async with session.delete(url, headers=headers) as response:
                if response.status in [200, 204]:
                    return True
                logger.error(f"Error removing delegate: {await response.text()}")
                return False

    async def create_shared_mailbox(self, display_name: str, alias: str, domain: str) -> bool:
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        url = f"{self.base_url}/users"
        upn = f"{alias}@{domain}"
        
        import secrets
        import string
        alphabet = string.ascii_letters + string.digits + "!@#$"
        temp_password = "".join(secrets.choice(alphabet) for _ in range(16))
        
        payload = {
            "accountEnabled": False,
            "displayName": display_name,
            "mailNickname": alias,
            "userPrincipalName": upn,
            "passwordProfile": {
                "forceChangePasswordNextSignIn": False,
                "password": temp_password
            },
            "mailboxSettings": {
                "userPurpose": "shared"
            }
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status in [200, 201]:
                    return True
                logger.error(f"Error creating shared mailbox: {await response.text()}")
                return False

    async def fetch_security_compliance_metrics(self) -> Dict[str, Any]:
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}"}
        
        metrics = {
            "security_defaults": "UNKNOWN",
            "conditional_access_mfa": "UNKNOWN",
            "block_legacy_auth": "UNKNOWN",
            "password_never_expires": "UNKNOWN"
        }
        
        async with aiohttp.ClientSession() as session:
            # 1. Security Defaults
            sd_url = f"{self.base_url}/policies/identitySecurityDefaultsEnforcementPolicy"
            async with session.get(sd_url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    metrics["security_defaults"] = "ENABLED" if data.get("isEnabled") else "DISABLED"
                elif response.status == 404:
                    metrics["security_defaults"] = "DISABLED"
                else:
                    logger.error(f"Error fetching security defaults: {await response.text()}")
                    metrics["security_defaults"] = "ERROR"

            # 2. Conditional Access Policies
            ca_url = f"{self.base_url}/identity/conditionalAccess/policies"
            async with session.get(ca_url, headers=headers) as response:
                if response.status == 200:
                    policies = (await response.json()).get("value", [])
                    has_mfa_policy = False
                    has_block_legacy = False
                    
                    for policy in policies:
                        if policy.get("state") != "enabled":
                            continue
                        
                        controls = policy.get("grantControls") or {}
                        built_in = controls.get("builtInControls") or []
                        
                        if "mfa" in built_in or "requireMfa" in built_in:
                            has_mfa_policy = True
                            
                        conditions = policy.get("conditions") or {}
                        client_apps = conditions.get("clientAppTypes") or []
                        legacy_clients = {"exchangeActiveSync", "other"}
                        is_legacy_target = any(c in legacy_clients for c in client_apps)
                        
                        is_block = "block" in built_in or controls.get("operator") == "OR" and "block" in built_in
                        if is_legacy_target and is_block:
                            has_block_legacy = True
                            
                    metrics["conditional_access_mfa"] = "ENABLED" if has_mfa_policy else "DISABLED"
                    metrics["block_legacy_auth"] = "ENABLED" if has_block_legacy else "DISABLED"
                else:
                    logger.error(f"Error fetching CA policies: {await response.text()}")
                    metrics["conditional_access_mfa"] = "ERROR"
                    metrics["block_legacy_auth"] = "ERROR"

            # 3. Password Policy (Organization settings)
            org_url = f"{self.base_url}/organization"
            async with session.get(org_url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    orgs = data.get("value", [])
                    if orgs:
                        policies = orgs[0].get("passwordPolicies", "") or ""
                        metrics["password_never_expires"] = "ENABLED" if "DisablePasswordExpiration" in policies else "DISABLED"
                else:
                    logger.error(f"Error fetching organization info: {await response.text()}")
                    metrics["password_never_expires"] = "ERROR"
                    
        return metrics

    async def fetch_inactive_users_report(self, days: int = 30) -> List[Dict[str, Any]]:
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{self.base_url}/users?$select=id,displayName,userPrincipalName,signInActivity,assignedLicenses&$top=999"
        inactive_users = []
        
        from datetime import datetime, timezone, timedelta
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
        
        async with aiohttp.ClientSession() as session:
            while url:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        for user in data.get("value", []):
                            if not user.get("assignedLicenses"):
                                continue
                                
                            sign_in = user.get("signInActivity") or {}
                            last_sign_in_str = sign_in.get("lastSignInDateTime")
                            
                            is_inactive = False
                            last_login = None
                            
                            if last_sign_in_str:
                                try:
                                    last_login = datetime.fromisoformat(last_sign_in_str.replace("Z", "+00:00"))
                                    if last_login < cutoff_date:
                                        is_inactive = True
                                except:
                                    pass
                            else:
                                is_inactive = True
                                
                            if is_inactive:
                                inactive_users.append({
                                    "id": user.get("id"),
                                    "displayName": user.get("displayName"),
                                    "userPrincipalName": user.get("userPrincipalName"),
                                    "lastSignIn": last_login.isoformat() if last_login else None,
                                    "licenses": [lic.get("skuId") for lic in user.get("assignedLicenses")]
                                })
                        url = data.get("@odata.nextLink")
                    else:
                        logger.error(f"Error fetching sign in activity: {await response.text()}")
                        break
        return inactive_users

    async def fetch_mailbox_automatic_replies(self, user_graph_id: str) -> Dict[str, Any]:
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{self.base_url}/users/{user_graph_id}/mailboxSettings/automaticRepliesSetting"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                err_text = await response.text()
                logger.error(f"Error fetching automatic replies: {err_text}")
                raise Exception(f"Microsoft Graph Error ({response.status}): {err_text}")

    async def update_mailbox_automatic_replies(self, user_graph_id: str, settings: dict) -> bool:
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        url = f"{self.base_url}/users/{user_graph_id}/mailboxSettings"
        payload = {
            "automaticRepliesSetting": settings
        }
        async with aiohttp.ClientSession() as session:
            async with session.patch(url, headers=headers, json=payload) as response:
                if response.status in [200, 204]:
                    return True
                err_text = await response.text()
                logger.error(f"Error updating automatic replies: {err_text}")
                raise Exception(f"Microsoft Graph Error ({response.status}): {err_text}")

    async def fetch_global_administrators(self) -> List[Dict[str, Any]]:
        token = await self.get_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{self.base_url}/directoryRoles"
        
        global_admin_role_id = None
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    roles = (await response.json()).get("value", [])
                    for r in roles:
                        if r.get("displayName") == "Global Administrator":
                            global_admin_role_id = r.get("id")
                            break
                            
            if global_admin_role_id:
                members_url = f"{self.base_url}/directoryRoles/{global_admin_role_id}/members?$select=id,displayName,userPrincipalName"
                async with session.get(members_url, headers=headers) as response:
                    if response.status == 200:
                        return (await response.json()).get("value", [])
        return []


