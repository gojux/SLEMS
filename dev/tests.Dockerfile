FROM ghcr.io/home-assistant/home-assistant:2026.9.3

RUN pip install --no-cache-dir pytest pytest-asyncio
