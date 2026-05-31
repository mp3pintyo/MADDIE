FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg git libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

ARG TORCH_INSTALL_COMMAND="pip install torch==2.8.0 torchaudio==2.8.0"

RUN python -m pip install --upgrade pip setuptools wheel \
    && sh -lc "$TORCH_INSTALL_COMMAND" \
    && pip install fastapi==0.136.3 uvicorn==0.48.0 python-multipart==0.0.29 soundfile omnivoice

WORKDIR /workspace

COPY services /workspace/services

EXPOSE 8010

CMD ["uvicorn", "services.omnivoice_api:app", "--host", "0.0.0.0", "--port", "8010"]