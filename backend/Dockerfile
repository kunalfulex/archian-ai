# Backend API with MSSQL ODBC driver (build context = backend/ for Render rootDir)
FROM python:3.11-slim-bookworm

# Install Microsoft ODBC Driver 18 + build tools (pyodbc / native deps).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl git apt-transport-https gnupg2 ca-certificates unixodbc unixodbc-dev \
    && curl -fsSL -o /tmp/packages-microsoft-prod.deb https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb \
    && dpkg -i /tmp/packages-microsoft-prod.deb \
    && rm /tmp/packages-microsoft-prod.deb \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
COPY . .
RUN pip install --no-cache-dir -r requirements.txt

# Optional private add-ons (cloud edition): Render secret files "extra-requirements.txt"
# (plain https://github.com/... URLs, no credentials) and "github_token".
# Secrets are mounted only for this step so they never land in an image layer. The token is
# injected through git's env config rather than the URL, because pip prints full requirement
# URLs (credentials included) when it logs dependencies.
# Secret contents are not part of Docker's cache key; changing ARCHIAN_CLOUD_VERSION reruns
# this step, and pip expands ${ARCHIAN_CLOUD_VERSION} inside the requirements file.
ARG ARCHIAN_CLOUD_VERSION=""
RUN --mount=type=secret,id=extra-requirements_txt,dst=/etc/secrets/extra-requirements.txt \
    --mount=type=secret,id=github_token,dst=/etc/secrets/github_token \
    if [ -f /etc/secrets/extra-requirements.txt ]; then \
      if [ -f /etc/secrets/github_token ]; then \
        export GIT_CONFIG_COUNT=1 \
          GIT_CONFIG_KEY_0="url.https://x-access-token:$(tr -d '[:space:]' < /etc/secrets/github_token)@github.com/.insteadOf" \
          GIT_CONFIG_VALUE_0="https://github.com/"; \
      fi; \
      pip install --no-cache-dir -r /etc/secrets/extra-requirements.txt; \
    fi

EXPOSE 8000
# Render sets PORT; default 8000 for local
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
