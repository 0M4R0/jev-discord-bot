import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")
TYPESAFE_API_KEY: str = os.getenv("TYPESAFE_API_KEY", "")
DATABASE_PATH: str = os.getenv("DATABASE_PATH", "bot_data.db")
COMMAND_PREFIX: str = os.getenv("COMMAND_PREFIX", "!")

# Default thresholds for Jev confidence
DEFAULT_TIER1_THRESHOLD: float = 0.95  # High confidence threat
DEFAULT_TIER2_THRESHOLD: float = 0.70  # Medium confidence threat

# Default timeout minutes
DEFAULT_FIRST_TIMEOUT_MINS: int = 10
DEFAULT_SUBSEQUENT_TIMEOUT_MINS: int = 60
