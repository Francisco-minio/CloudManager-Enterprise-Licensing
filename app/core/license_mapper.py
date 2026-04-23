
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
    "POWER_BI_INDIVIDUAL": "Power BI (Gratis)",
    "FLOW_FREE": "Microsoft Power Automate Free",
    "POWER_AUTOMATE_PER_USER": "Power Automate Premium",
    "POWERAUTOMATE_ATTENDED_RPA": "Power Automate con RPA asistido",
    "POWERAPPS_DEVELOPER": "Microsoft Power Apps for Developer",
    "FABRIC_FREE": "Microsoft Fabric (Gratis)",
    "STREAM": "Microsoft Stream",
    "WINDOWS_STORE": "Microsoft Store for Business",
    "WIN10_PRO_ENT_SUB": "Windows 10/11 Enterprise",
    "VISIOCLIENT": "Visio Plan 2",
    "PROJECTCLIENT": "Project Plan 3",
    "PROJECTPROFESSIONAL": "Project Plan 5",
    "COPILOT_STUDIO_VIRAL": "Prueba de Microsoft Copilot Studio",
    "DYN365_CS_INSIGHTS_PROMO": "Prueba de Dynamics 365 Customer Service Insights",
    "POWERPAGES_VTRIAL_FOR_MAKERS": "Power Pages vTrial",
    "DYN365_IOM_VIRAL": "Prueba Dynamics 365 Intelligent Order Management"
}

def get_friendly_name(sku_part_number: str | None) -> str:
    """Devuelve el nombre legible si existe, de lo contrario el original"""
    if not sku_part_number:
        return "Licencia Desconocida"
    return LICENSE_MAP.get(sku_part_number.upper(), sku_part_number)

def is_free_license(sku_part_number: str | None) -> bool:
    """Detecta si una licencia es probablemente gratuita o viral"""
    if not sku_part_number:
        return False
    sku = sku_part_number.upper()
    free_keywords = [
        "FREE", "TRIAL", "INDIVIDUAL", "VIRAL", "GRATIS", "DEVELOPER", 
        "ADHOC", "STANDARD_FREE", "STREAM", "SEARCH", "BI_STANDARD", 
        "AI_SERVICE", "POWERAPPS", "WINDOWS_STORE", "STORE"
    ]
    return any(kw in sku for kw in free_keywords)
