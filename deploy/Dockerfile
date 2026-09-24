FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 ESTUDIO_MODELOS=/opt/models \
    VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH
RUN sed -i s,http://deb.debian.org,https://deb.debian.org,g /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=3 update && apt-get -o Acquire::Retries=3 install -y --no-install-recommends \
    ffmpeg fonts-noto-color-emoji fonts-dejavu-core libgomp1 libglib2.0-0 \
    ca-certificates git tini && rm -rf /var/lib/apt/lists/*
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
RUN python -m venv /opt/venv
COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt
RUN useradd --uid 1000 --create-home --shell /bin/bash atlas
WORKDIR /app
COPY lib/comum.py lib/matte.py lib/pose.py /app/lib/
RUN python lib/matte.py --instalar mobilenetv3 && python lib/pose.py --instalar
RUN mkdir -p /opt/models/whisper && python -c "from faster_whisper import WhisperModel; WhisperModel('large-v3-turbo', device='cpu', compute_type='int8', download_root='/opt/models/whisper')"
COPY . /app
RUN ln -s /var/lib/editor-ia/atlas/claude-runtime /home/atlas/.claude && chown -R atlas:atlas /app /opt/models /home/atlas
ENV PORTA=4123 ESTUDIO_HOST=0.0.0.0 ESTUDIO_NUVEM=1 HF_HUB_OFFLINE=1 \
    ESTUDIO_CHAT_CWD=/var/lib/editor-ia/atlas \
    ESTUDIO_RAIZ=/var/lib/editor-ia/atlas/Edições \
    ESTUDIO_BROLLS=/var/lib/editor-ia/atlas/B-rolls \
    ESTUDIO_BIBLIOTECA=/var/lib/editor-ia/atlas/B-rolls/biblioteca \
    ESTUDIO_CHAVES=/var/lib/editor-ia/atlas/config/chaves.json \
    ESTUDIO_USERS_FILE=/run/atlas/users.json \
    OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1
USER atlas
EXPOSE 4123
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "app/servidor.py"]
