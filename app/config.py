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

    # Pattern agent: checks 1-minute price behaviour and posts sudden moves to the Telegram chat
    pattern_enabled: bool = True
    pattern_interval: int = 60          # seconds between checks
    pattern_spike_pct: float = 0.4      # min % move within 1/5/15 min to count as a spike/drop
    pattern_spike_z: float = 3.0        # ...and it must be this many std-devs beyond normal 1-min noise
    pattern_trend_minutes: int = 10     # min length of a one-direction run
    pattern_trend_pct: float = 0.3      # min total % move over that run
    pattern_cooldown_min: int = 30      # don't repeat the same pattern for the same market within this
    # AI summary of each detection (leave the key empty to send rule-based messages only)
    anthropic_api_key: str = ""
    pattern_model: str = "claude-opus-5-5"

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
