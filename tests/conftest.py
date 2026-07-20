from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root if present
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
