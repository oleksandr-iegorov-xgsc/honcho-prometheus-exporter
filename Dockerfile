# syntax=docker/dockerfile:1
FROM python:3.12-slim AS build
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir build && python -m build --wheel --outdir /dist

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin exporter
COPY --from=build /dist/ /tmp/dist/
RUN python -m pip install --no-cache-dir /tmp/dist/*.whl && rm -rf /tmp/dist
USER 10001:10001
EXPOSE 9477
ENTRYPOINT ["honcho-prometheus-exporter"]
