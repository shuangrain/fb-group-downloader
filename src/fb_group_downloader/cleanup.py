"""找出（並可選擇清除）因日期 fallback 造成的重複相簿/貼文資料夾。

規則：同一社團、同一類型 (albums/posts) 下，info json 內 ID 相同者視為重複，
保留媒體檔最多的一個（並列時保留最早的）。
為避免遺失已下載的相片，apply 時只刪除「沒有任何媒體檔」的重複資料夾；
含有檔案的重複資料夾只會回報為 manual，請人工確認/合併。
ID 以 album_ 開頭的偽相簿同樣只在空資料夾時才會被刪除。
"""

import json
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

KINDS = {"albums": ("album_info.json", "album_id"), "posts": ("post_info.json", "post_id")}


@dataclass
class CleanupResult:
    empty: list[Path] = field(default_factory=list)  # 空的重複資料夾（可自動刪除）
    manual: list[tuple[Path, int]] = field(default_factory=list)  # (資料夾, 檔案數) 需人工處理


def count_media_files(d: Path) -> int:
    return sum(1 for f in d.iterdir() if f.is_file() and f.suffix not in (".json", ".txt"))


def find_duplicates(root: Path) -> CleanupResult:
    result = CleanupResult()
    if not root.is_dir():
        return result
    dups: list[Path] = []
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
                if kind == "albums" and ident.startswith("album_"):
                    dups += dirs
                elif len(dirs) > 1:
                    keep = max(dirs, key=lambda d: (count_media_files(d), -dirs.index(d)))
                    dups += [d for d in dirs if d != keep]
    for d in dups:
        n = count_media_files(d)
        if n == 0:
            result.empty.append(d)
        else:
            result.manual.append((d, n))
    return result


def delete_empty_duplicates(root: Path, result: CleanupResult) -> None:
    """刪除空的重複資料夾，並一併移除 downloads.db 內對應的記錄"""
    db_path = root / "downloads.db"
    conn = sqlite3.connect(db_path) if db_path.exists() else None
    try:
        for d in result.empty:
            if conn:
                conn.execute(
                    "DELETE FROM downloads WHERE folder_path LIKE ?",
                    (f"%/{d.parent.parent.name}/{d.parent.name}/{d.name}",),
                )
            shutil.rmtree(d, ignore_errors=True)
        if conn:
            conn.commit()
    finally:
        if conn:
            conn.close()
