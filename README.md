# 🚀 365 License Manager

![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![FastAPI](https://img.shields.io/badge/Framework-FastAPI-green)

**365 License Manager** es una plataforma profesional y centralizada diseñada para la gestión eficiente de licencias de Microsoft 365 en entornos Multi-Tenant. Permite auditar, sincronizar y visualizar el uso de licencias a través de múltiples organizaciones desde un único panel administrativo.

---

## ✨ Características Principales

-   **🌐 Gestión Multi-Tenant**: Administra múltiples clientes/tenants de Microsoft 365 de forma independiente.
-   **🔄 Motor de Sincronización**: Integración directa con **Microsoft Graph API** para extraer usuarios, licencias y asignaciones en tiempo real.
-   **🔐 Seguridad Avanzada**: 
    -   Autenticación **OAuth2** con Azure AD para el acceso al dashboard.
    -   Almacenamiento encriptado de secretos de clientes mediante **AES (Fernet)**.
-   **📊 Dashboard Visual**: Interfaz moderna para visualizar métricas rápidas de usuarios y licencias.
-   **🏷️ Mapeo Inteligente**: Conversión automática de nombres técnicos de SKUs de Microsoft (ej: `ENTERPRISEPACK`) a nombres amigables (ej: `Office 365 E3`).
-   **🐳 Docker Ready**: Completamente contenedorizado para despliegues rápidos y consistentes.

---

## 🛠️ Stack Tecnológico

-   **Backend**: [FastAPI](https://fastapi.tiangolo.com/) (Asincrónico)
-   **Base de Datos**: [PostgreSQL](https://www.postgresql.org/) con [SQLAlchemy (Async)](https://docs.sqlalchemy.org/)
-   **Integración**: [MSAL Python](https://github.com/AzureAD/microsoft-authentication-library-for-python) & Microsoft Graph API
-   **Frontend**: Jinja2 Templates, Vanilla CSS & JavaScript.

---

## 🚦 Primeros Pasos

### 📋 Prerrequisitos

-   Python 3.10 o superior.
-   Docker y Docker Compose (opcional para despliegue local).
-   Una **Azure AD App Registration** para el login del dashboard.
-   Una **Azure AD App Registration** por cada tenant que desees monitorear (con permisos `User.Read.All` y `Organization.Read.All`).

### ⚙️ Configuración (.env)

Crea un archivo `.env` en el directorio raíz basándote en el siguiente ejemplo:

```env
# Base de Datos
DATABASE_URL=postgresql+asyncpg://user:password@db:5432/365_licenses

# Seguridad
SECRET_KEY=tu_secret_key_para_sesiones
ENCRYPTION_KEY=tu_llave_fernet_generada

# Azure AD Login (Dashboard)
AZURE_AD_CLIENT_ID=id_de_tu_app_principal
AZURE_AD_TENANT_ID=id_de_tu_tenant_principal
AZURE_AD_CLIENT_SECRET=secreto_de_tu_app_principal
```

---

## 🚀 Despliegue

### Opción 1: Docker Compose (Recomendado)

```bash
docker-compose up -d --build
```
La aplicación estará disponible en `http://localhost:8000`.

### Opción 2: Ejecución Local

1.  **Crear entorno virtual:**
    ```bash
    python -m venv venv
    source venv/bin/activate  # En Windows: venv\Scripts\activate
    ```
2.  **Instalar dependencias:**
    ```bash
    pip install -r requirements.txt
    ```
3.  **Ejecutar servidor:**
    ```bash
    python main.py
    ```

---

## 🤝 Contribución

1.  Haz un Fork del proyecto.
2.  Crea una rama para tu feature (`git checkout -b feature/AmazingFeature`).
3.  Haz commit de tus cambios (`git commit -m 'Add some AmazingFeature'`).
4.  Push a la rama (`git push origin feature/AmazingFeature`).
5.  Abre un Pull Request.

---

## 📄 Licencia

Este proyecto está bajo la licencia MIT. Consulta el archivo `LICENSE` para más detalles.

---
Desarrollado con ❤️ por Francisco Minio.
