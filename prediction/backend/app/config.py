# 配置模块：从环境变量读取，兼容现有 POSTGRES_* 命名
import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # 优先 DATABASE_*，回退 POSTGRES_*（现有 .env 命名）
    DATABASE_HOST: str = "football-postgres"
    DATABASE_PORT: int = 5432
    DATABASE_NAME: str = "football_prediction"
    DATABASE_USER: str = "football_app"
    DATABASE_PASSWORD: str = ""

    POSTGRES_DB: str | None = None
    POSTGRES_USER: str | None = None
    POSTGRES_PASSWORD: str | None = None

    RAW_DATA_DIR: str = "/app/data/raw"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    # 上传限制
    MAX_INGEST_BYTES: int = 20 * 1024 * 1024  # 20MB

    class Config:
        env_file = ".env"
        extra = "ignore"

    def effective_db_config(self) -> dict:
        host = self.DATABASE_HOST
        port = self.DATABASE_PORT
        name = self.DATABASE_NAME
        user = self.DATABASE_USER
        password = self.DATABASE_PASSWORD
        if self.POSTGRES_DB and not os.getenv("DATABASE_NAME"):
            name = self.POSTGRES_DB
        if self.POSTGRES_USER and not os.getenv("DATABASE_USER"):
            user = self.POSTGRES_USER
        if self.POSTGRES_PASSWORD and not os.getenv("DATABASE_PASSWORD"):
            password = self.POSTGRES_PASSWORD
        return {
            "host": host,
            "port": port,
            "dbname": name,
            "user": user,
            "password": password,
        }


settings = Settings()
