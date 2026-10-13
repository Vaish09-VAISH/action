import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from streamlit.errors import StreamlitSecretNotFoundError


load_dotenv(
    dotenv_path=Path(__file__).resolve().with_name(".env"),
    override=False,
)


def _read_secret(name: str) -> str | None:
    value = os.getenv(name)
    if value:
        return value

    try:
        value = st.secrets.get(name)
    except StreamlitSecretNotFoundError:
        return None
    return str(value) if value else None


def get_secret(name: str) -> str:
    value = _read_secret(name)
    if value is None:
        raise RuntimeError(
            f"{name} is not configured. Set it in the local .env file or "
            "in Streamlit Community Cloud app Secrets."
        )
    return value


def require_secrets(*names: str) -> None:
    missing = [name for name in names if _read_secret(name) is None]
    if missing:
        raise RuntimeError(
            "Missing required API secrets: "
            + ", ".join(missing)
            + ". Set them in the local .env file or Streamlit Community Cloud "
            "app Secrets."
        )
