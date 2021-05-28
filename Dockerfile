FROM python:3.9-buster AS build-deps

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    # poetry will be installed here
    POETRY_HOME="/opt/poetry" \
    POETRY_NO_INTERACTION=1 \
    POETRY_NO_ANSI=1
# add poetry to path
ENV PATH="$POETRY_HOME/bin:$PATH"

# install poetry
RUN curl -sSL https://raw.githubusercontent.com/python-poetry/poetry/master/install-poetry.py | python -

COPY . ./
RUN poetry install --no-dev

FROM python:3.9-slim-buster

WORKDIR /app
# the user needs to be able to write the database file to /app
RUN chown 1000:1000 /app

ENV PYTHONUNBUFFERED=1 \
    VENV_PATH="/app/.venv"
# add virtual environment binaries to path
ENV PATH="$VENV_PATH/bin:$PATH"

COPY --from=build-deps /app .

USER 1000

ENTRYPOINT [ "trade" ]
CMD [ "config.yml" ]
