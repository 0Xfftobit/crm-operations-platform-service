# SINGLE unified image — one project, one container, one port. All agents + the gateway share this
# image and one venv (requirements.all.txt). A process supervisor runs each agent in its own
# process/working-dir (so their identical `app` packages don't collide) on 127.0.0.1:8001+, and the
# gateway on the public :8080 proxying /<slug>/* to each. Agents default to in-memory/sqlite storage.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.all.txt ./
RUN pip install --no-cache-dir -r requirements.all.txt
COPY src ./src
COPY deploy ./deploy
COPY deploy/supervisord.conf /etc/supervisord.conf
RUN chmod +x /app/deploy/entrypoint.sh
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
  CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://localhost:8080/health',timeout=4).status==200 else 1)"
CMD ["/app/deploy/entrypoint.sh"]
