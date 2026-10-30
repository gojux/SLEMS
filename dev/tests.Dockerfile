FROM ghcr.io/home-assistant/home-assistant:2026.10.4

RUN pip install --no-cache-dir pytest pytest-asyncio
