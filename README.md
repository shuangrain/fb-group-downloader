# Facebook 社團圖片、影片與檔案定期下載器 (FB Group Downloader)

一個專為 Facebook 社團設計的自動化下載與備份工具。基於 **Python + Playwright + SQLite + yt-dlp + APScheduler** 開發，支援以排程方式定期增量同步特定 Facebook 社團的 **圖片**、**影片** 與 **附件檔案**（PDF、Word、ZIP 等）。

---

## 🌟 主要功能與特色

- 🔄 **定期增量同步**：只下載最新發布或未曾下載過的內容，遇到已同步的舊貼文時自動停止向下滾動，節省網路流量與時間。
- 🖼️ **多媒體完整支援**：
  - **圖片**：抓取最高解析度原圖，自動計算 SHA-256 雜湊去重。
  - **影片**：支援直鏈串流下載與 `yt-dlp` 解析引擎，自動帶入 Session Cookies 確保高畫質串流正常下載。
  - **社團檔案/文件**：自動爬取社團「檔案專區 (`/files`)」與貼文附件，支援 PDF, DOCX, XLSX, ZIP 等各類文件，自動保留原始檔名。
- 🛡️ **SQLite 去重與歷史資料庫**：
  - 完整記錄發文者、貼文內文、發文時間、原始網址、本機路徑、檔案大小與 SHA-256 Checksum。
  - 避免重複下載相同檔案。
- 🔐 **靈活的登入驗證機制**：
  - **互動式登入**：自動啟動可視化瀏覽器視窗，登入成功（含 2FA）後自動截取保存 Session (`session.json`)。
  - **Cookie 匯入**：支援由瀏覽器套件（如 Cookie-Editor）導出的 JSON 檔案直接匯入。
- ⏰ **定時排程守護服務 (Daemon)**：
  - 支援固定分鐘/小時間隔（例如每 60 分鐘）或標準 Cron 表達式（例如 `0 */2 * * *` 每兩小時）。
- 📊 **CLI 終端儀表板**：內建美觀的 Rich 終端介面，隨時查看下載統計與歷史明細。

---

## 📁 下載資料夾與檔案結構 (`yyyyMMdd <name>`)

程式會以**貼文**或**相簿**為獨立單位，自動將圖片、影片與附件存入專屬資料夾，資料夾格式為 `yyyyMMdd <name>`，並在資料夾內產生內文詳細資訊檔（`post_info.txt` 與 `post_info.json`）：

```
downloads/
├── downloads.db           # SQLite 去重與下載狀態資料庫
└── <group_id>/
    ├── posts/             # 貼文專屬資料夾
    │   └── 20260831 王小明 - 社團聚會公告/
    │       ├── photo_01_abc123.jpg
    │       ├── photo_02_def456.jpg
    │       ├── video_01_vid001.mp4
    │       ├── post_info.txt       # 發文作者、時間、貼文網址與完整文字內容
    │       └── post_info.json      # 結構化中繼資料
    ├── albums/            # 相簿專屬資料夾
    │   └── 20260831 2026年度夏季團聚相簿/
    │       ├── photo_01_xxx.jpg
    │       ├── photo_02_yyy.jpg
    │       └── album_info.json     # 相簿中繼資料
    └── files/             # 社團檔案專區文件 (PDF, DOCX, ZIP 等)
```

---

## 🚀 快速開始指南

### 1. 安裝環境與依賴

本專案建議使用 [`uv`](https://github.com/astral-sh/uv)（或標準 pip/venv）：

```bash
# 使用 uv 安裝依賴
uv sync

# 安裝 Playwright Chromium 瀏覽器核心
uv run playwright install chromium
```

> 若使用傳統 pip：
> ```bash
> python3 -m venv .venv
> source .venv/bin/activate
> pip install -e .
> playwright install chromium
> ```

---

### 2. Facebook 登入認證

爬取社團前，需要先進行一次 Facebook 登入以產生 `session.json`。

#### 方式 A：互動式可視化登入（推薦）
執行以下指令會開啟瀏覽器視窗：
```bash
uv run fb-downloader login
```
在跳出的瀏覽器視窗中輸入您的 Facebook 帳號密碼並完成二步驟驗證（2FA）。程式偵測到登入成功後，會自動將 Cookies 保存為 `session.json` 並關閉瀏覽器。

#### 方式 B：從瀏覽器擴充套件匯入 Cookies
如果您在無圖形介面的遠端伺服器 (Headless Server) 上運行，可以使用瀏覽器擴充套件（例如 [Cookie-Editor](https://cookie-editor.cgagnier.ca/)）將 Facebook 登入狀態匯出為 `cookies.json`，然後執行：
```bash
uv run fb-downloader import-cookies /path/to/cookies.json
```

---

### 3. 設定目標社團與排程

產生預設設定檔：
```bash
uv run fb-downloader init-config
```
編輯專案根目錄下的 [`config.yaml`](file:///home/user/git/fb-group-downloader/config.yaml)：

```yaml
storage_dir: "./downloads"
session_file: "./session.json"
headless: true # 爬蟲執行時隱藏瀏覽器視窗
browser_timeout_sec: 60
scroll_delay_range:
  - 1.5
  - 3.5

# 排程設定
schedule:
  enabled: true
  interval_minutes: 60 # 每 60 分鐘執行一次同步
  # cron: "0 */2 * * *" # 也可使用標準 Cron 表達式

# 日誌輸出與檔案輪替 (Log Rotation) 設定
logging:
  enabled: true
  file_path: "./logs/fb_downloader.log" # 日誌輸出路徑
  level: "INFO" # 記錄等級 (DEBUG, INFO, WARNING, ERROR)
  rotation_mode: "size" # 輪替模式: 'size' (依大小) 或 'daily' (每日午夜)
  max_bytes: 10485760 # 10MB 單檔大小上限
  backup_count: 5 # 保留 5 份歷史備份檔

# 社團清單（支援多個社團同時監控）
groups:
  - group_id: "123456789012345" # 社團數字 ID 或短網址名稱
    name: "我的目標社團"
    download_images: true # 下載圖片
    download_videos: true # 下載影片
    download_files: true # 下載檔案/文件
    scan_feed: true # 掃描動態貼文
    scan_files_tab: true # 掃描「檔案」分頁
    max_posts_per_scan: 30 # 每次最多掃描貼文數
```

> **提示：如何取得 Facebook 社團 ID？**
> 開啟社團首頁網址，例如 `https://www.facebook.com/groups/1234567890/`，其中的 `1234567890` 即為 `group_id`。若是自訂網址如 `https://www.facebook.com/groups/my_group_name/`，填入 `my_group_name` 即可。

---

### 4. 執行下載

#### 🔹 手動執行單次同步 (One-time Sync)
```bash
# 同步設定檔中所有社團
uv run fb-downloader sync

# 或者僅同步特定社團
uv run fb-downloader sync --group-id 1234567890

# 啟用除錯模式（印出完整的 Raw HTTP Request / Raw Response 與瀏覽器請求）
uv run fb-downloader sync --debug
```

#### 🔹 啟動定期背景排程守護服務 (Daemon)
```bash
uv run fb-downloader daemon

# 排程守護服務亦支援 --debug
uv run fb-downloader daemon --debug
```
程式將常駐執行，依照 `config.yaml` 中設定的間隔或 Cron 表達式，自動定期喚醒進行增量下載。

#### 🔹 查看下載統計與歷史紀錄
```bash
uv run fb-downloader status
```
輸出範例：
```
╭───────────────── Facebook 社團下載統計 ─────────────────╮
│ • 總下載數量：128 個                                    │
│ • 總檔案大小：342.15 MB                                 │
│ • 圖片數量：95                                          │
│ • 影片數量：18                                          │
│ • 文件/檔案數量：15                                     │
╰─────────────────────────────────────────────────────────╯
```

---

## 🛠️ 命令列指令一覽

| 指令 | 說明 |
| :--- | :--- |
| `fb-downloader init-config` | 建立 `config.yaml` 設定檔範本 |
| `fb-downloader login` | 開啟可視化視窗登入 Facebook 並儲存 Session |
| `fb-downloader import-cookies <file>` | 從 Cookie-Editor JSON 檔案匯入 Session Cookies |
| `fb-downloader sync [--debug]` | 執行單次增量抓取與下載（可加 `--group-id` 與 `--debug` 印出原始 HTTP 封包） |
| `fb-downloader daemon [--debug]` | 啟動常駐排程守護程式，自動定時同步 |
| `fb-downloader status` | 顯示目前已下載檔案統計與最新下載列表 |

---

## 🐳 Docker / Docker Compose 容器化部署

本專案已完整配置包含 **Playwright 瀏覽器核心**、**ffmpeg**、**時區設定 (Asia/Taipei)** 與所有相依套件的 Dockerfile。

### 1. 先在本機完成登入以取得 `session.json`
```bash
# 在本機執行登入（會開啟瀏覽器視窗）
uv run fb-downloader login
```

### 2. 使用 Docker Compose 快速啟動常駐背景服務
```bash
# 建置映像檔並在背景啟動守護程式（自動使用當前使用者的 UID/GID，避免檔案寫入權限問題）
docker compose up -d

# 查看即時同步紀錄
docker compose logs -f

# 停止服務
docker compose down
```

### 3. 使用 Docker 執行單次指令
```bash
# 執行單次同步
docker compose run --rm fb-downloader sync

# 查看下載統計
docker compose run --rm fb-downloader status
```

---

## 🧪 測試與程式碼風格檢查 (Ruff)

```bash
# 執行單元測試
uv run pytest

# 執行 Ruff 程式碼風格與靜態分析檢查
uv run ruff check .

# 自動修復可修正的 Lint 錯誤
uv run ruff check --fix .

# 自動排版格式化所有 Python 程式碼
uv run ruff format .
```
