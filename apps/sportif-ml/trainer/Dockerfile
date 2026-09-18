FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/sportif-ml
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY sportif_ml ./sportif_ml

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /work \
    && chown -R app:app /opt/sportif-ml /work
USER 10001
ENV ML_WORK_DIR=/work