from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Telegram (leave empty to disable the bot; alerts are then only logged)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Polling
    poll_interval: float = 5.0
    request_timeout: float = 4.0
    ads_per_fetch: int = 20
    top_n: int = 5
    fail_alert_after: int = 12  # consecutive failed polls before an "exchange down" alert

    # Storage
    db_path: str = "data/p2ptracker.db"
    raw_retention_days: int = 30

    # Dashboard basic auth (both empty = no auth; set them when exposed on a VPS)
    dashboard_user: str = ""
    dashboard_pass: str = ""

    # Display timezone for alert messages
    display_tz: str = "Asia/Yangon"

    # Which exchanges to poll. Binance is supported but off by default: it only shows MMK ads to
    # logged-in Myanmar-KYC accounts, so anonymous polling returns no ads.
    exchanges: str = "bybit,bitget"

    @property
    def exchange_list(self) -> list[str]:
        return [e.strip().lower() for e in self.exchanges.split(",") if e.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
