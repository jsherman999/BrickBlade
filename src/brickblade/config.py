from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    rebrickable_key: str = ""
    brickset_key: str = ""
    brickset_username: str = ""
    brickset_password: str = ""

    bl_consumer_key: str = ""
    bl_consumer_secret: str = ""
    bl_token: str = ""
    bl_token_secret: str = ""

    brickblade_bearer_token: str = "change-me"
    brickblade_db_url: str = "sqlite:///./var/brickblade.db"
    brickblade_data_dir: Path = Field(default=Path("./var"))
    brickblade_price_ttl_hours: int = 48
    brickblade_log_level: str = "INFO"

    @property
    def data_dir(self) -> Path:
        self.brickblade_data_dir.mkdir(parents=True, exist_ok=True)
        return self.brickblade_data_dir


@lru_cache
def get_settings() -> Settings:
    return Settings()
