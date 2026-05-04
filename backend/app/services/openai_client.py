import os
import logging
from functools import lru_cache
from openai import OpenAI

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_openai_client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY environment variable is not set. "
            "Add it to your .env file."
        )
    logger.info("OpenAI client initialized")
    return OpenAI(api_key=api_key)