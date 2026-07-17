FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 1000 codemedic \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin codemedic

RUN python -m pip install --no-cache-dir "pytest>=9,<10"

USER codemedic
WORKDIR /workspace
