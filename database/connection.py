import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Load environment variables from .env (if present)
load_dotenv()


@dataclass(frozen=True)
class DatabaseConfig:
    url: str


def get_database_config() -> DatabaseConfig:
    """
    Reads database configuration from environment variables.

    Required:
      - DATABASE_URL: postgresql://USER:PASSWORD@HOST:PORT/DBNAME
    """
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise ValueError(
            "DATABASE_URL is not set. Set it in your environment or .env file."
        )
    return DatabaseConfig(url=url)


def connect(*, connect_timeout_seconds: int = 10):
    """
    Returns a psycopg connection using DATABASE_URL.

    Note: This repo does not currently vendor a DB driver. Install psycopg v3:
      pip install 'psycopg[binary]'
    """
    try:
        import psycopg  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "psycopg is required for database connectivity. "
            "Install it with: pip install 'psycopg[binary]'"
        ) from e

    cfg = get_database_config()
    try:
        # Avoid hanging at startup on DNS/TCP issues.
        return psycopg.connect(cfg.url, connect_timeout=connect_timeout_seconds)
    except psycopg.OperationalError as e:
        err = str(e).lower()
        if "resolve" in err or "getaddrinfo" in err or "name or service not known" in err:
            raise RuntimeError(
                "Could not resolve the database host (DNS failure).\n\n"
                "Common cause: Supabase direct host `db.<project>.supabase.co` often has "
                "only an IPv6 address. If your network or Windows has no working IPv6, "
                "lookups fail with getaddrinfo / Errno 11001.\n\n"
                "Fix: In Supabase Dashboard → Project Settings → Database, copy the "
                "connection string for the Session pooler or Transaction pooler "
                "(host like `aws-0-<region>.pooler.supabase.com`, usually IPv4). "
                "Put that URL in DATABASE_URL (use port 5432 for session, 6543 for transaction "
                "as shown in the dashboard).\n\n"
                "Other checks: correct DATABASE_URL host (no typo), VPN/DNS issues, "
                "or try `nslookup <host>` in PowerShell.\n\n"
                f"Original error: {e}"
            ) from e
        raise

