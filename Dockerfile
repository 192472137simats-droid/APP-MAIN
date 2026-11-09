FROM python:3.12-slim-bookworm AS cpp-build

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        libcurl4-openssl-dev \
        libjsoncpp-dev \
        libpoppler-cpp-dev \
        libsqlite3-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY cpp ./cpp
RUN cmake -S cpp -B /tmp/cpp-build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /tmp/cpp-build --parallel

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libcurl4 \
        libjsoncpp25 \
        libpoppler-cpp0v5 \
        libsqlite3-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY cpp/web ./cpp/web
COPY --from=cpp-build /tmp/cpp-build/research-advisor-server ./research-advisor-server
COPY scripts/start-railway.sh ./scripts/start-railway.sh
RUN chmod +x ./scripts/start-railway.sh

CMD ["./scripts/start-railway.sh"]
