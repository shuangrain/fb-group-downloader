import asyncio
from pathlib import Path

import typer
from rich.panel import Panel
from rich.table import Table

from fb_group_downloader.auth.session import SessionManager
from fb_group_downloader.config import load_or_create_config
from fb_group_downloader.db import Database
from fb_group_downloader.scheduler.runner import SchedulerDaemon
from fb_group_downloader.scraper.coordinator import SyncCoordinator
from fb_group_downloader.utils.logger import console, setup_logger

app = typer.Typer(
    name="fb-downloader",
    help="Facebook 社團圖片、影片與檔案定期下載與備份工具",
    add_completion=False,
)


@app.command()
def init_config(
    output: Path = typer.Option(Path("./config.yaml"), "--output", "-o", help="輸出設定檔路徑"),
    force: bool = typer.Option(False, "--force", "-f", help="若已存在是否強制覆蓋"),
):
    """建立預設的 config.yaml 設定檔範本"""
    if output.exists() and not force:
        console.print(f"[bold yellow]設定檔已存在：{output}[/bold yellow]（若要覆蓋請加上 --force）")
        raise typer.Exit(code=1)

    cfg = load_or_create_config()
    setup_logger(log_config=cfg.logging)
    cfg.save_to_yaml(output)
    console.print(f"[bold green]✓ 成功建立設定檔範本於：{output.resolve()}[/bold green]")
    console.print("請編輯此設定檔填入您的 Facebook 社團 ID (group_id) 及自訂下載參數。")


@app.command()
def login(
    config_path: Path | None = typer.Option(None, "--config", "-c", help="設定檔路徑 (預設: ./config.yaml)"),
    session_file: Path | None = typer.Option(None, "--session", "-s", help="自訂 Session 儲存路徑"),
    timeout: int = typer.Option(300, "--timeout", "-t", help="登入逾時時間（秒）"),
):
    """開啟瀏覽器視窗進行 Facebook 登入並保存 Session Cookies"""
    cfg = load_or_create_config(config_path)
    setup_logger(log_config=cfg.logging)
    target_session = session_file or cfg.session_file

    session_mgr = SessionManager(target_session)
    success = session_mgr.interactive_login(timeout_sec=timeout)
    if not success:
        raise typer.Exit(code=1)


@app.command()
def import_cookies(
    cookie_json: Path = typer.Argument(..., help="由 Cookie-Editor 等瀏覽器套件導出的 JSON 檔案路徑"),
    config_path: Path | None = typer.Option(None, "--config", "-c", help="設定檔路徑 (預設: ./config.yaml)"),
    session_file: Path | None = typer.Option(None, "--session", "-s", help="自訂 Session 儲存路徑"),
):
    """從 Cookie-Editor JSON 檔案匯入 Facebook Cookies"""
    cfg = load_or_create_config(config_path)
    setup_logger(log_config=cfg.logging)
    target_session = session_file or cfg.session_file

    session_mgr = SessionManager(target_session)
    try:
        session_mgr.import_from_cookie_editor_json(cookie_json)
        console.print(f"[bold green]✓ 成功匯入 Cookie 至 {target_session}[/bold green]")
    except Exception as e:
        console.print(f"[bold red]匯入失敗：{e}[/bold red]")
        raise typer.Exit(code=1) from e


@app.command()
def sync(
    group_id: str | None = typer.Option(
        None, "--group-id", "-g", help="指定單一社團 ID 進行同步（未指定則同步設定檔中所有社團）"
    ),
    config_path: Path | None = typer.Option(None, "--config", "-c", help="設定檔路徑"),
    debug: bool = typer.Option(False, "--debug", "-d", help="啟用除錯紀錄"),
):
    """手動執行一次社團增量抓取與下載"""
    cfg = load_or_create_config(config_path)
    setup_logger(log_config=cfg.logging, debug=debug)
    db_path = cfg.storage_dir / "downloads.db"
    db = Database(db_path)

    coordinator = SyncCoordinator(cfg, db)
    asyncio.run(coordinator.run_all(target_group_id=group_id))


@app.command()
def daemon(
    config_path: Path | None = typer.Option(None, "--config", "-c", help="設定檔路徑"),
    debug: bool = typer.Option(False, "--debug", "-d", help="啟用除錯紀錄"),
    no_initial_run: bool = typer.Option(False, "--no-initial-run", help="啟動時不安裝立刻執行首次同步，僅等待排程觸發"),
):
    """啟動背景排程守護服務，定期自動同步與下載社團檔案"""
    cfg = load_or_create_config(config_path)
    setup_logger(log_config=cfg.logging, debug=debug)
    db_path = cfg.storage_dir / "downloads.db"
    db = Database(db_path)

    runner = SchedulerDaemon(cfg, db)
    asyncio.run(runner.start(run_immediately=not no_initial_run))


@app.command()
def status(
    config_path: Path | None = typer.Option(None, "--config", "-c", help="設定檔路徑"),
    limit: int = typer.Option(20, "--limit", "-n", help="顯示最近下載筆數"),
):
    """查看目前下載資料庫統計與歷史紀錄"""
    cfg = load_or_create_config(config_path)
    setup_logger(log_config=cfg.logging)
    db_path = cfg.storage_dir / "downloads.db"
    if not db_path.exists():
        console.print(f"[yellow]資料庫尚未建立（路徑：{db_path}），請先執行 sync 同步。[/yellow]")
        return

    db = Database(db_path)
    stats = db.get_stats()

    total_mb = stats["total_bytes"] / (1024 * 1024)
    summary_text = (
        f"• 總下載數量：[bold cyan]{stats['total_count']}[/bold cyan] 個\n"
        f"• 總檔案大小：[bold cyan]{total_mb:.2f} MB[/bold cyan]\n"
        f"• 圖片數量：[bold green]{stats['images']}[/bold green]\n"
        f"• 影片數量：[bold magenta]{stats['videos']}[/bold magenta]\n"
        f"• 文件/檔案數量：[bold yellow]{stats['files']}[/bold yellow]"
    )
    console.print(Panel(summary_text, title="Facebook 社團下載統計", border_style="cyan"))

    # 最近記錄
    recent = db.get_recent_downloads(limit=limit)
    if recent:
        table = Table(title=f"最近 {len(recent)} 筆下載記錄", show_header=True, header_style="bold magenta")
        table.add_column("ID", style="dim", width=6)
        table.add_column("類型", style="cyan", width=8)
        table.add_column("社團 ID", style="dim", width=15)
        table.add_column("作者", width=15)
        table.add_column("大小", justify="right", width=10)
        table.add_column("檔案路徑")

        for r in recent:
            size_str = (
                f"{r.file_size / 1024:.1f} KB" if r.file_size < 1024 * 1024 else f"{r.file_size / (1024 * 1024):.2f} MB"
            )
            table.add_row(
                str(r.id),
                r.media_type.value,
                r.group_id,
                r.post_author or "-",
                size_str,
                r.local_filepath,
            )
        console.print(table)


def main():
    app()


if __name__ == "__main__":
    main()
