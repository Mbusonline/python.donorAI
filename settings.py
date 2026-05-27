"""
Settings and configuration for NGO Report Generator
"""

import os

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# API Keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Redis (for background queue)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# If true, FastAPI startup does not ping Postgres (use when fixing DATABASE_URL or no local DB).
SKIP_STARTUP_DB_CHECK = os.getenv("SKIP_STARTUP_DB_CHECK", "").strip().lower() in (
    "1",
    "true",
    "yes",
)

# File Paths
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = "templates"
OUTPUT_DIR = "output"
LOGS_DIR = os.path.join(_PROJECT_ROOT, "logs")
# Global JSONL log for all Gemini/OpenAI calls (see services/api_logger.py)
PROVIDER_API_LOG_PATH = os.getenv(
    "PROVIDER_API_LOG_PATH",
    os.path.join(LOGS_DIR, "provider_api_calls.jsonl"),
)

# S3 key prefix for generated report PDFs (no leading slash required)
S3_REPORTS_PREFIX = (os.getenv("S3_REPORTS_PREFIX") or "generated-reports/").strip()
if S3_REPORTS_PREFIX and not S3_REPORTS_PREFIX.endswith("/"):
    S3_REPORTS_PREFIX += "/"

# API Configuration
GEMINI_MODEL = "gemini-2.5-flash-lite"
OPENAI_MODEL = "gpt-4o-mini"  # Small, fast model for content assembly

# Text Processing Limits
MAX_TEXT_LENGTH = 70000  # Max characters to send to LLM

# Rate Limiting (Gemini Free Tier: 15 requests/minute)
GEMINI_REQUESTS_PER_MINUTE = 15
GEMINI_MIN_DELAY_BETWEEN_REQUESTS = 4  # seconds (60/15 = 4)
GEMINI_MAX_RETRIES = 3
GEMINI_RETRY_BASE_DELAY = 2  # seconds for exponential backoff

# Content assembly (large prompts / long markdown): 504 and timeouts are common; retry here.
GEMINI_ASSEMBLY_MAX_RETRIES = max(
    1, int(os.getenv("GEMINI_ASSEMBLY_MAX_RETRIES", "4"))
)
GEMINI_ASSEMBLY_RETRY_BASE_DELAY = float(
    os.getenv("GEMINI_ASSEMBLY_RETRY_BASE_DELAY", "8")
)

# Image Processing Limits
MAX_IMAGES_TO_PROCESS = (
    20  # Maximum number of images to analyze (to avoid processing too many)
)

# Report image borders (PIL ImageOps.expand — solid pad around photos in PDF)
IMAGE_BORDER_WIDTH = max(0, int(os.getenv("IMAGE_BORDER_WIDTH", "12")))
IMAGE_BORDER_COLOR = (os.getenv("IMAGE_BORDER_COLOR") or "#000000").strip()
