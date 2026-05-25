FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pokemon_api.py ./
COPY web ./web

RUN mkdir -p /cache

ENV POKEAPI_GENERATION=generation-i
ENV POKEAPI_VERSION_GROUP=red-blue
ENV POKEAPI_LANGUAGE=de
ENV POKEAPI_CACHE_PATH=/cache/pokeapi_cache.json
ENV UVICORN_LOG_LEVEL=info
ENV APP_LOG_LEVEL=INFO

EXPOSE 8000

CMD uvicorn web.app:app --host 0.0.0.0 --port 8000 --log-level ${UVICORN_LOG_LEVEL} --access-log
