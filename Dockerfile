# 使用官方 Python 3.11 Slim 基礎映像檔
FROM python:3.11-slim-bookworm

# 接收主機端使用者的 UID / GID（預設 1000:1000）
ARG UID=1000
ARG GID=1000

# 設定環境變數
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Taipei \
    UV_SYSTEM_PYTHON=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    HOME=/home/appuser

# 安裝系統相依套件（含 ffmpeg、時區資料庫、憑證等）
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    tzdata \
    curl \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# 建立專屬的非 root 使用者與群組 (UID:GID)
RUN (groupadd -g ${GID} appuser 2>/dev/null || true) \
    && (useradd -u ${UID} -g ${GID} -m -s /bin/bash appuser 2>/dev/null || true)

# 從官方 Astral 映像檔複製 uv（超高速套件管理）
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 設定工作目錄
WORKDIR /app

# 先複製專案定義檔以利用 Docker 快取層
COPY pyproject.toml README.md ./
COPY src/ ./src/

# 安裝 Python 依賴套件並安裝專案 CLI
RUN uv pip install --no-cache -e .

# 安裝 Playwright Chromium 瀏覽器及其所需的 Linux 系統函式庫
RUN playwright install --with-deps chromium

# 建立下載與資料儲存目錄，並將所有權指派給當前使用者 (UID:GID)
RUN mkdir -p /app/downloads /app/logs /ms-playwright /home/appuser \
    && chown -R ${UID}:${GID} /app /ms-playwright /home/appuser

# 切換為非 root 使用者執行
USER ${UID}:${GID}

# 宣告掛載點
VOLUME ["/app/downloads", "/app/logs"]

# 設定 Entrypoint 為 fb-downloader 命令
ENTRYPOINT ["fb-downloader"]

# 預設指令：啟動背景定時排程守護程式
CMD ["daemon"]
