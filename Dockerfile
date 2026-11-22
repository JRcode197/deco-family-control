FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/app
COPY requirements-deco.txt requirements.txt ./
RUN pip install --no-cache-dir -r requirements-deco.txt && useradd --create-home --uid 10001 family && mkdir /data && chown family:family /data
COPY app ./app
USER family
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-server-header"]
