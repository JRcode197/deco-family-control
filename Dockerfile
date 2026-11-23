FROM python:3.14-slim
ARG BUILD_ARCH
ARG BUILD_VERSION
LABEL io.hass.type="app" io.hass.arch="${BUILD_ARCH}" io.hass.version="${BUILD_VERSION}"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/app
COPY requirements-deco.txt requirements.txt ./
RUN pip install --no-cache-dir -r requirements-deco.txt && useradd --create-home --uid 10001 family && mkdir /data && chown family:family /data
COPY --chown=family:family app ./app
EXPOSE 8080
CMD ["python", "-m", "app.server"]
