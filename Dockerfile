# Use an official Python runtime based on Debian 12 "bookworm" as a parent image.
FROM python:3.12-slim-bookworm

# Add user that will be used in the container.
RUN useradd wagtail

# Port used by this container to serve HTTP.
EXPOSE 8000

# Set environment variables.
# 1. Force Python stdout and stderr streams to be unbuffered.
# 2. Set PORT variable that is used by Gunicorn. This should match "EXPOSE"
#    command.
ENV PYTHONUNBUFFERED=1 \
    PORT=8000 \
    HOME=/tmp \
    PADDLE_HOME=/tmp/.paddle \
    XDG_CACHE_HOME=/tmp/.cache \
    PDX_CACHE_DIR=/tmp/.paddlex \
    TMPDIR=/tmp

# Install system packages required by Wagtail and Django.
RUN apt-get update --yes --quiet && \
    apt-get install --yes --quiet --no-install-recommends \
        build-essential \
        libpq-dev \
        libmariadb-dev \
        libjpeg62-turbo-dev \
        zlib1g-dev \
        libwebp-dev \
        libgl1 \
        libglib2.0-0 \
        poppler-utils \
        tesseract-ocr \
        libtesseract-dev \
        libleptonica-dev \
    && rm -rf /var/lib/apt/lists/*

# Install the application server.
RUN pip install "gunicorn==20.0.4"

# Install the project requirements.
COPY requirements.txt /
RUN pip install -r /requirements.txt

# Use /app folder as a directory where the source code is stored.
WORKDIR /app

# Set this directory to be owned by the "wagtail" user. This Wagtail project
# uses SQLite, the folder needs to be owned by the user that
# will be writing to the database file.
RUN chown wagtail:wagtail /app

# Copy the source code of the project into the container.
COPY --chown=wagtail:wagtail . .

# Pre-create PaddleX/PaddleOCR cache directories and give user access
RUN mkdir -p /tmp/.paddle /tmp/.cache && \
    chown -R wagtail:wagtail /tmp/.paddle /tmp/.cache

# Use user "wagtail" to run the build commands below and the server itself.
USER wagtail

# Run the PaddleOCR initialization and model download as the wagtail user
# RUN python -c "from paddleocr import PaddleOCR; ocr = PaddleOCR(use_angle_cls=True); print('PaddleOCR successfully')"

# Collect static files.

# Runtime command that executes when "docker run" is called, it does the
# following:
#   1. Migrate the database.
#   2. Start the application server.
# WARNING:
#   Migrating database at the same time as starting the server IS NOT THE BEST
#   PRACTICE. The database should be migrated manually or using the release
#   phase facilities of your hosting platform. This is used only so the
#   Wagtail instance can be started with a simple "docker run" command.
CMD set -xe; gunicorn AutoGrader.wsgi:application --bind 0.0.0.0:${PORT} --timeout 300
#--worker 17 --threads 4 --max-requests 1000 --worker-class gthread
