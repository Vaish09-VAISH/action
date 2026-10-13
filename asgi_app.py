"""ASGI entry point for the Streamlit UI and live-audio API route."""

from pathlib import Path

import streamlit as st
from starlette.routing import Route

from live_stream import (
    receive_live_stream_chunk,
    serve_audio_file,
    serve_live_audio,
)

app = st.App(
    Path(__file__).with_name("app.py"),
    routes=[
        Route("/api/audio/{filename:str}", serve_audio_file, methods=["GET"]),
        Route(
            "/api/live-audio/{recording_id:str}",
            serve_live_audio,
            methods=["GET"],
        ),
        Route(
            "/api/live-stream-chunk",
            receive_live_stream_chunk,
            methods=["POST"],
        )
    ],
)
