FROM python:3.9-slim-buster AS build-deps

# update, upgrade, cleanup
RUN export DEBIAN_FRONTEND=noninteractive && \
    apt-get update && \
    apt-get upgrade --yes && \
    apt-get install --no-install-recommends --yes build-essential curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    POETRY_HOME="/opt/poetry" \
    POETRY_NO_INTERACTION=1 \
    POETRY_NO_ANSI=1

# install poetry
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN curl -sSL https://install.python-poetry.org | python -

# copy source and install deps
COPY . ./
RUN /opt/poetry/bin/poetry install --no-dev

# final image
FROM python:3.9-slim-buster

# update, upgrade, cleanup
RUN export DEBIAN_FRONTEND=noninteractive && \
    apt-get update && \
    apt-get upgrade --yes && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

ARG USER_ID=1000
ARG GROUP_ID=1000
RUN groupadd -r -g $GROUP_ID pancaketrade && \
    useradd --no-log-init -rm -u $USER_ID -g pancaketrade -s /bin/bash pancaketrade && \
    chown pancaketrade:pancaketrade -R /app

USER pancaketrade

ENV PYTHONUNBUFFERED=1 \
    VENV_PATH="/app/.venv" \
    PATH="/app/.venv/bin:$PATH" \
    USER="pancaketrade"

COPY --from=build-deps --chown=pancaketrade:pancaketrade /app .

ENTRYPOINT [ "trade" ]
CMD [ "user_data/config.yml" ]
