from pydantic_settings import BaseSettings, SettingsConfigDict
from cryptography.fernet import Fernet
import os

class Settings(BaseSettings):
    PROJECT_NAME: str = "365 License Manager"
    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost/dbname"
    SECRET_KEY: str = "your-secret-key-for-jwt"
    ENCRYPTION_KEY: str = Fernet.generate_key().decode()  # Debería guardarse en .env una vez generado
    
    # Azure AD configuration for Dashboard Login
    AZURE_AD_CLIENT_ID: str = ""
    AZURE_AD_TENANT_ID: str = ""
    AZURE_AD_CLIENT_SECRET: str = ""
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()

# Manager para encriptación de Client Secrets de los tenants
class CryptoManager:
    def __init__(self):
        self.fernet = Fernet(settings.ENCRYPTION_KEY.encode())

    def encrypt(self, data: str) -> str:
        return self.fernet.encrypt(data.encode()).decode()

    def decrypt(self, encrypted_data: str) -> str:
        return self.fernet.decrypt(encrypted_data.encode()).decode()

crypto = CryptoManager()
