FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg git libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

ARG TORCH_INSTALL_COMMAND="pip install --index-url https://download.pytorch.org/whl/cu128 torch==2.8.0+cu128 torchaudio==2.8.0+cu128"
ARG OMNIVOICE_GIT_REPO="https://github.com/k2-fsa/OmniVoice.git"
ARG OMNIVOICE_GIT_REF="refs/pull/71/head"

RUN python -m pip install --upgrade pip setuptools wheel \
    && sh -lc "$TORCH_INSTALL_COMMAND" \
    && pip install fastapi==0.136.3 uvicorn==0.48.0 python-multipart==0.0.29 soundfile \
    && git clone --filter=blob:none "$OMNIVOICE_GIT_REPO" /tmp/omnivoice-src \
    && cd /tmp/omnivoice-src \
    && git fetch --depth 1 origin "$OMNIVOICE_GIT_REF" \
    && git checkout FETCH_HEAD \
    && pip install /tmp/omnivoice-src \
    && rm -rf /tmp/omnivoice-src

WORKDIR /workspace

COPY services /workspace/services

EXPOSE 8010

CMD ["uvicorn", "services.omnivoice_api:app", "--host", "0.0.0.0", "--port", "8010"]