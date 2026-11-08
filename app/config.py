from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["development", "test", "production"] = Field("development", alias="NODE_ENV")
    port: int = 4000
    database_url: str
    app_url: str = "http://localhost:5173"
    trust_proxy: bool = False

    jwt_access_secret: str = Field(min_length=32)
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 7
    verify_email_ttl_hours: int = 24
    reset_password_ttl_minutes: int = 30

    rate_limit_window_minutes: int = 15
    rate_limit_max: int = 300
    auth_rate_limit_max: int = 20

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    mail_from: str = "Employee Review <no-reply@example.test>"

    @field_validator("database_url")
    @classmethod
    def _use_pymysql_driver(cls, value: str) -> str:
        return "mysql+pymysql://" + value.removeprefix("mysql://") if value.startswith("mysql://") else value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
