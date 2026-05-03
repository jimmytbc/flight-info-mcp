import asyncio
import logging
import sys

from dotenv import find_dotenv, load_dotenv

from . import obs
from .config import ConfigError, load
from .server import run


def main() -> None:
    # Populate os.environ from a .env file in the cwd (or its parents).
    # ``usecwd=True`` is important: without it, ``find_dotenv`` walks up from
    # this file's package location and would always find the project-root
    # ``.env`` even when the operator (or a test harness) launched from a
    # different cwd. Idempotent and safe in production - by default it does
    # NOT override variables already in the environment, so an orchestrator's
    # injected secrets always win over a stray local file.
    load_dotenv(find_dotenv(usecwd=True))
    obs.configure()
    try:
        config = load()
    except ConfigError as exc:
        logging.getLogger("flight_info_mcp").error(str(exc))
        sys.exit(2)
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
