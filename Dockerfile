# Imagen de la aplicacion web (Flask + gunicorn) para Azure Container Apps.
#
#   docker build -t rutalytics .
#   docker run --env-file .env -e ENTORNO=produccion -p 8000:8000 rutalytics
#
# La base de datos no va en la imagen: en local es el MySQL de
# docker-compose.yml y en Azure, Azure Database for MySQL. Las claves llegan
# por variables de entorno (nunca se copia el .env; ver .dockerignore).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ENTORNO=produccion \
    PORT=8000

WORKDIR /app

# Dependencias primero: esta capa solo se reconstruye si cambia requirements.txt.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Usuario sin privilegios; solo escribe en instance/ (registros e importaciones).
RUN useradd --create-home --uid 1000 sgds \
    && mkdir -p instance \
    && chown -R sgds:sgds instance \
    && sed -i 's/\r$//' docker/*.sh \
    && chmod +x docker/*.sh
USER sgds

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/salud',timeout=4)" || exit 1

CMD ["docker/arrancar.sh"]
