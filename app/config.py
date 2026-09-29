import os
from pathlib import Path

from dotenv import load_dotenv

# locally the values come from .env, on a server they're normal env vars
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")


def check():
    missing = [name for name in ("GITHUB_TOKEN", "GITHUB_WEBHOOK_SECRET", "ANTHROPIC_API_KEY") if not os.getenv(name)]
    if missing:
        # an empty webhook secret would let anyone fake a valid signature, so refuse to start
        raise SystemExit("missing settings (set them in .env or as environment variables): " + ", ".join(missing))
