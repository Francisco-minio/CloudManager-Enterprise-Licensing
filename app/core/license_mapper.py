from __future__ import annotations

LICENSE_MAP = {
    "O365_BUSINESS_ESSENTIALS": "Microsoft 365 Empresa Básico",
    "O365_BUSINESS_PREMIUM": "Microsoft 365 Empresa Estándar",
    "SPB": "Microsoft 365 Empresa Premium",
    "SMB_BUSINESS": "Aplicaciones de Microsoft 365 para negocios",
    "O365_BUSINESS": "Microsoft 365 Aplicaciones para negocios",
    "SPE_E3": "Microsoft 365 E3",
    "SPE_E5": "Microsoft 365 E5",
    "ENTERPRISEPACK": "Office 365 E3",
    "ENTERPRISEPREMIUM": "Office 365 E5",
    "STANDARDPACK": "Office 365 E1",
    "DEVELOPERPACK_E5": "Microsoft 365 E5 Developer (Sin Windows)",
    "M365_APPS_FOR_ENTERPRISE": "Aplicaciones de Microsoft 365 para grandes empresas",
    "EMS": "Enterprise Mobility + Security E3",
    "EMSPREMIUM": "Enterprise Mobility + Security E5",
    "EXCHANGESTANDARD": "Exchange Online (Plan 1)",
    "EXCHANGEENTERPRISE": "Exchange Online (Plan 2)",
    "EXCHANSEARCH": "Exchange Online Archiving",
    "POWER_BI_PRO": "Power BI Pro",
    "POWER_BI_PRO_DEPT": "Power BI Pro (Dept)",
    "POWER_BI_INDIVIDUAL": "Power BI (Gratis)",
    "POWER_BI_STANDARD": "Power BI / Fabric (Gratis)",
    "FLOW_FREE": "Microsoft Power Automate Free",
    "POWER_AUTOMATE_PER_USER": "Power Automate Premium",
    "POWERAUTOMATE_ATTENDED_RPA": "Power Automate con RPA asistido",
    "POWERAPPS_DEV": "Microsoft Power Apps for Developer",
    "POWERAPPS_DEVELOPER": "Microsoft Power Apps for Developer",
    "FABRIC_FREE": "Microsoft Fabric (Gratis)",
    "STREAM": "Microsoft Stream",
    "WINDOWS_STORE": "Microsoft Store for Business",
    "WIN10_PRO_ENT_SUB": "Windows 10/11 Enterprise",
    "VISIOCLIENT": "Visio Plan 2",
    "PROJECTCLIENT": "Project Plan 3",
    "PROJECTPROFESSIONAL": "Project Plan 5",
    "MICROSOFT_365_COPILOT_FOR_BUSINESS": "Microsoft 365 Copilot para empresas",
    "TEAMS_PREMIUM_(FOR_DEPARTMENTS)": "Teams Premium",
    "MICROSOFT_TEAMS_ROOMS_BASIC": "Microsoft Teams Rooms Basic",
    "TEAMS_ROOMS_BASIC": "Microsoft Teams Rooms Basic",
    "NONPROFIT_PORTAL": "Portal sin fines de lucro",
    "RIGHTSMANAGEMENT_ADHOC": "Rights Management Adhoc",
    "COPILOT_STUDIO_VIRAL": "Prueba de Microsoft Copilot Studio",
    "CCIBOTS_PRIVPREV_VIRAL": "Copilot Studio (Trial/Viral)",
    "DYN365_CS_INSIGHTS_PROMO": "Prueba de Dynamics 365 Customer Service Insights",
    "POWERPAGES_VTRIAL_FOR_MAKERS": "Power Pages vTrial",
    "POWER_PAGES_VTRIAL_FOR_MAKERS": "Power Pages vTrial",
    "DYN365_IOM_VIRAL": "Prueba Dynamics 365 Intelligent Order Management",
    "DYNAMICS_365_INTELLIGENT_ORDER_MANAGEMENT_VTR": "Dynamics 365 IOM (Trial)",
    "DYN365_AI_SERVICE_INSIGHTS": "Dynamics 365 AI Insights (Trial)",
}

def get_friendly_name(sku_part_number: str | None) -> str:
    """Devuelve el nombre legible si existe, de lo contrario el original"""
    if not sku_part_number:
        return "Licencia Desconocida"
    return LICENSE_MAP.get(sku_part_number.upper().strip(), sku_part_number)

def is_free_license(sku_part_number: str | None) -> bool:
    """Detecta si una licencia es gratuita, viral o de prueba (trial)"""
    if not sku_part_number:
        return False
    sku = sku_part_number.upper().strip()

    # Coincidencias exactas comunes de licencias gratuitas o de aprovisionamiento
    exact_free = {
        "STREAM", "WINDOWS_STORE", "NONPROFIT_PORTAL", 
        "RIGHTSMANAGEMENT_ADHOC", "POWER_BI_STANDARD", "FLOW_FREE",
        "FABRIC_FREE", "POWER_BI_INDIVIDUAL", "POWERAPPS_DEV"
    }
    if sku in exact_free:
        return True

    # Palabras clave que indican licencia gratuita, trial, promo o viral
    free_keywords = [
        "FREE", "TRIAL", "INDIVIDUAL", "VIRAL", "GRATIS", "DEVELOPER",
        "ADHOC", "STANDARD_FREE", "BI_STANDARD", "AI_SERVICE",
        "STORE", "VTR", "VTRIAL", "PRIVPREV", "ROOMS_BASIC", "PROMO",
        "_DEV"
    ]

    # Proteger planes estándar pagados que no deben ser filtrados
    if sku.startswith(("SPE_", "SPB", "O365_BUSINESS", "ENTERPRISE", "STANDARDPACK", "EXCHANGE", "VISIO", "PROJECT")):
        if not ("TRIAL" in sku or "FREE" in sku or "_DEV" in sku or "VIRAL" in sku):
            return False

    return any(kw in sku for kw in free_keywords)
