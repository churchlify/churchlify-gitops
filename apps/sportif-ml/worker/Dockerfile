FROM python:3.12.7-slim-bookworm@sha256:60d9996b6a8a3689d36db740b49f4327be3be09a21122bd02fb8895abb38b50d

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ML_WORK_DIR=/work

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg=7:5.1.9-0+deb12u1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/sportif-ml

COPY requirements.txt .
RUN pip install --disable-pip-version-check --no-cache-dir --require-hashes \
    --requirement requirements.txt

COPY sportif_ml_worker ./sportif_ml_worker

RUN useradd --create-home --uid 10001 worker \
    && mkdir -p /work \
    && chown -R worker:worker /opt/sportif-ml /work

USER 10001:10001

ENTRYPOINT ["python", "-m", "sportif_ml_worker"]
