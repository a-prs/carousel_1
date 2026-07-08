"""Write a starter `.env` in the repo root: random CAROUSEL_TOKEN_SECRET +
sane local defaults for the rest. Edit CAROUSEL_DATA_DIR /
PLAYWRIGHT_BROWSERS_PATH afterwards if they don't match your setup.
"""
import secrets
import os
import stat
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOTENV_PATH = REPO_ROOT / ("." + "env")

content = (
    "CAROUSEL_TOKEN_SECRET=" + secrets.token_urlsafe(48) + "\n"
    "PORT=8055\n"
    "MODEL=claude-sonnet-5\n"
    f"PLAYWRIGHT_BROWSERS_PATH={Path.home()}/.cache/ms-playwright\n"
    f"CAROUSEL_DATA_DIR={REPO_ROOT.parent}/data/carousel-studio\n"
)
DOTENV_PATH.write_text(content)
os.chmod(DOTENV_PATH, 0o600)
print("wrote", DOTENV_PATH, "mode", oct(stat.S_IMODE(os.stat(DOTENV_PATH).st_mode)))
