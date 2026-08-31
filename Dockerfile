FROM python:3.13-slim AS builder

WORKDIR /build
COPY pyproject.toml requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY src ./src
RUN pip wheel --no-deps --no-build-isolation . -w /wheels

FROM python:3.13-slim

ARG BUILD_GIT_SHA=unknown
ENV KALSHI_BUILD_GIT_SHA=${BUILD_GIT_SHA} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KALSHI_DATA_DIR=/data

WORKDIR /app
COPY requirements.runtime.lock ./
RUN pip install --no-cache-dir -r requirements.runtime.lock \
    && useradd --create-home --uid 10001 kalshi-edge \
    && mkdir -p /data \
    && chown -R kalshi-edge:kalshi-edge /data
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-deps /wheels/kalshi_edge-*.whl \
    && rm -rf /wheels
USER kalshi-edge
ENTRYPOINT ["kalshi-edge"]
CMD ["--help"]
