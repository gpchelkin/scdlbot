"""All settings come from environment variables, parsed once at startup."""

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path


def _str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int) -> int:
    value = _str(name)
    return int(value) if value else default  # int() accepts "45_000_000"


def _float(name: str, default: float) -> float:
    value = _str(name)
    return float(value) if value else default


def _bool(name: str, default: bool) -> bool:
    value = _str(name).lower()
    return value in ("1", "true", "yes", "on") if value else default


def _list(name: str) -> list[str]:
    return [x for x in re.split(r"[,\s]+", _str(name)) if x]


def _ints(name: str) -> frozenset[int]:
    try:
        return frozenset(int(x) for x in _list(name))
    except ValueError as exc:
        raise ValueError(f"{name} must be a comma-separated list of integers") from exc


@dataclass(frozen=True)
class Config:
    token: str = ""
    tg_api: str = "https://api.telegram.org"
    local_mode: bool = False
    owner_id: int = 0

    db_path: Path = Path("scdlbot.sqlite3")
    dl_dir: Path = Path("/tmp/scdlbot")
    workers: int = 4
    youtube_workers: int = 2  # YouTube downloads run deno (~250 MB each) to solve JS challenges
    dl_timeout: int = 300  # seconds per downloaded item
    job_timeout: int = 1800  # seconds per whole job (e.g. an album)
    max_tg_file_size: int = 45_000_000
    max_convert_file_size: int = 350_000_000  # bigger downloads are skipped
    max_playlist_items: int = 50
    max_queue: int = 500
    max_user_jobs: int = 5
    ffmpeg_threads: int = 1
    ytdlp_args: tuple[str, ...] = ()

    no_flood_chat_ids: frozenset[int] = frozenset()
    cookies_file: str = ""
    cookies_refresh: int = 600
    proxies: tuple[str | None, ...] = ()  # None means a direct connection
    source_ips: tuple[str, ...] = ()
    whitelist_domains: frozenset[str] = frozenset()
    blacklist_domains: frozenset[str] = frozenset()
    whitelist_chats: frozenset[int] = frozenset()
    blacklist_chats: frozenset[int] = frozenset()

    webhook: bool = False
    host: str = "127.0.0.1"
    port: int = 5000
    webhook_url_root: str = ""
    webhook_url_path: str = ""
    webhook_secret: str | None = None
    webhook_cert: str | None = None
    webhook_key: str | None = None
    drop_pending_updates: bool = True

    metrics_host: str = "127.0.0.1"
    metrics_port: int = 8000
    loglevel: str = "INFO"
    sentry_dsn: str = ""
    jobs_retention_days: int = 90
    # Tell the owner when a site starts failing (logged out, IP blocked...): at least this many downloads
    # in the window, and at least this share of them failed for reasons that aren't the link's fault:
    alert_window: int = 1800
    alert_min_jobs: int = 20
    alert_failure_ratio: float = 0.2  # normal is under 5%; a SoundCloud 403 wave on 2026-10-19 reached 22%

    @classmethod
    def from_env(cls) -> Config:
        token = _str("TG_BOT_TOKEN")
        tg_api = _str("TG_BOT_API", "https://api.telegram.org").rstrip("/")
        if "TG_BOT_API_LOCAL_MODE" in os.environ:
            local_mode = _bool("TG_BOT_API_LOCAL_MODE", False)
        else:
            local_mode = "127.0.0.1" in tg_api or "localhost" in tg_api
        proxies = os.getenv("PROXIES")
        return cls(
            token=token,
            tg_api=tg_api,
            local_mode=local_mode,
            owner_id=_int("TG_BOT_OWNER_CHAT_ID", 0),
            db_path=Path(_str("DB_PATH", "scdlbot.sqlite3")).expanduser(),
            dl_dir=Path(_str("DL_DIR", "/tmp/scdlbot")).expanduser(),
            workers=max(1, _int("WORKERS", 4)),
            youtube_workers=max(1, _int("YOUTUBE_WORKERS", 2)),
            dl_timeout=_int("DL_TIMEOUT", 300),
            job_timeout=_int("JOB_TIMEOUT", 1800),
            # Bot API limit is 50 MB; a local Bot API server allows 2000 MB:
            max_tg_file_size=_int("MAX_TG_FILE_SIZE", 1_900_000_000 if local_mode else 45_000_000),
            max_convert_file_size=_int("MAX_CONVERT_FILE_SIZE", 350_000_000),
            max_playlist_items=max(1, _int("MAX_PLAYLIST_ITEMS", 50)),
            max_queue=_int("MAX_QUEUE", 500),
            max_user_jobs=max(1, _int("MAX_USER_JOBS", 5)),
            ffmpeg_threads=_int("FFMPEG_THREADS", 1),
            ytdlp_args=tuple(shlex.split(_str("YTDLP_ARGS"))),
            no_flood_chat_ids=_ints("NO_FLOOD_CHAT_IDS"),
            cookies_file=_str("COOKIES_FILE"),
            cookies_refresh=_int("COOKIES_REFRESH", 600),
            # "PROXIES=,http://a,http://b" means: direct, a or b (empty item or "direct" is a direct connection)
            proxies=tuple(None if x.strip() in ("", "direct") else x.strip() for x in proxies.split(",")) if proxies else (),
            source_ips=tuple(_list("SOURCE_IPS")),
            whitelist_domains=frozenset(_list("WHITELIST_DOMAINS")),
            blacklist_domains=frozenset(_list("BLACKLIST_DOMAINS")),
            whitelist_chats=_ints("WHITELIST_CHATS"),
            blacklist_chats=_ints("BLACKLIST_CHATS"),
            webhook=_bool("WEBHOOK_ENABLE", False),
            host=_str("HOST", "127.0.0.1"),
            port=_int("PORT", 5000),
            webhook_url_root=_str("WEBHOOK_APP_URL_ROOT"),
            webhook_url_path=_str("WEBHOOK_APP_URL_PATH") or token.replace(":", ""),
            webhook_secret=_str("WEBHOOK_SECRET_TOKEN") or None,
            webhook_cert=_str("WEBHOOK_CERT_FILE") or None,
            webhook_key=_str("WEBHOOK_KEY_FILE") or None,
            drop_pending_updates=_bool("DROP_PENDING_UPDATES", True),
            metrics_host=_str("METRICS_HOST", "127.0.0.1"),
            metrics_port=_int("METRICS_PORT", 8000),
            loglevel=_str("LOGLEVEL", "INFO").upper(),
            sentry_dsn=_str("SENTRY_DSN"),
            jobs_retention_days=_int("JOBS_RETENTION_DAYS", 90),
            alert_window=_int("ALERT_WINDOW", 1800),
            alert_min_jobs=_int("ALERT_MIN_JOBS", 20),
            alert_failure_ratio=_float("ALERT_FAILURE_RATIO", 0.2),
        )

    def chat_allowed(self, chat_id: int) -> bool:
        if self.whitelist_chats and chat_id not in self.whitelist_chats:
            return False
        return chat_id not in self.blacklist_chats
