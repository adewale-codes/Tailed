#!/bin/sh
# Start command for the API container:  sh scripts/start.sh  (the Dockerfile's default command)
# Applies database migrations, then serves on $PORT (8000 if unset; Coolify: "Ports Exposes" 8000).
#
# --no-proxy-headers: uvicorn must never take the client address from X-Forwarded-For / Forwarded.
# By default it does for connections from 127.0.0.1, which would let anyone reaching the API through
# a local proxy pick their own IP and dodge the daily limit. Visitors' real IPs reach the API only
# from the web app, with WEB_PROXY_SECRET (see app/services/limits.py).
set -e
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --no-server-header --no-proxy-headers
