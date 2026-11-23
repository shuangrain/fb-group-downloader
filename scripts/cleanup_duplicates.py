"""找出（並可選擇清除）因日期 fallback 造成的重複相簿/貼文資料夾。

用法：
  python scripts/cleanup_duplicates.py [downloads_dir]          # 僅列出 (dry-run)
  python scripts/cleanup_duplicates.py [downloads_dir] --apply  # 實際刪除重複資料夾與對應 DB 記錄

規則：同一社團、同一類型 (albums/posts) 下，info json 內 ID 相同者視為重複，
保留檔案數最多的一個（並列時保留最早的）。
為避免遺失已下載的相片，--apply 只會刪除「沒有任何媒體檔」的重複資料夾；
含有檔案的重複資料夾只會列出，請人工確認/合併。
偽相簿 (album_id 以 album_ 開頭) 同樣只在空資料夾時才會被刪除。
"""

import json
import shutil
import sqlite3
import sys
from pathlib import Path

KINDS = {"albums": ("album_info.json", "album_id"), "posts": ("post_info.json", "post_id")}


def n_files(d: Path) -> int:
    return sum(1 for f in d.iterdir() if f.is_file() and not f.name.endswith(".json") and f.suffix != ".txt")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply = "--apply" in sys.argv
    root = Path(args[0] if args else "downloads")
    db_path = root / "downloads.db"
    to_delete: list[tuple[Path, str]] = []

    for group in sorted(p for p in root.iterdir() if p.is_dir()):
        for kind, (info_name, key) in KINDS.items():
            base = group / kind
            if not base.is_dir():
                continue
            by_id: dict[str, list[Path]] = {}
            for d in sorted(base.iterdir()):
                info = d / info_name
                if not info.is_file():
                    continue
                try:
                    ident = json.loads(info.read_text(encoding="utf-8")).get(key)
                except Exception:
                    continue
                if ident:
                    by_id.setdefault(ident, []).append(d)
            for ident, dirs in by_id.items():
                if ident.startswith("album_") and kind == "albums":
                    to_delete += [(d, kind) for d in dirs]
                elif len(dirs) > 1:
                    keep = max(dirs, key=lambda d: (n_files(d), -dirs.index(d)))
                    to_delete += [(d, kind) for d in dirs if d != keep]

    manual = [(d, k) for d, k in to_delete if n_files(d) > 0]
    to_delete = [(d, k) for d, k in to_delete if n_files(d) == 0]
    for d, _kind in to_delete:
        print(f"{'刪除' if apply else '[dry-run] 將刪除'}（空資料夾）: {d}")
    for d, _kind in manual:
        print(f"[需人工處理] 含 {n_files(d)} 個檔案，不會自動刪除: {d}")
    if not to_delete:
        print("沒有可自動刪除的空重複資料夾。")
        return
    if not apply:
        print("\n加上 --apply 才會實際刪除。建議先備份 downloads/。")
        return

    conn = sqlite3.connect(db_path) if db_path.exists() else None
    for d, kind in to_delete:
        if conn:
            conn.execute("DELETE FROM downloads WHERE folder_path LIKE ?", (f"%/{d.parent.parent.name}/{kind}/{d.name}",))
        shutil.rmtree(d, ignore_errors=True)
    if conn:
        conn.commit()
        conn.close()


if __name__ == "__main__":
    main()
