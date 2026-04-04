"""
数据库模块 - SQLite 存储视频元数据
字段：标题、上传时间、向量索引路径
"""
import sqlite3
from pathlib import Path
from datetime import datetime

APP_ROOT = Path(__file__).resolve().parent
DB_PATH = APP_ROOT / "data" / "videos.db"


def _get_conn():
    """获取数据库连接"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(DB_PATH), check_same_thread=False)


def init_db():
    """初始化数据库表"""
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS video_metadata (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                upload_time TEXT NOT NULL,
                vector_index_path TEXT,
                saved_path TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    finally:
        conn.close()


def insert_video_metadata(
    title: str,
    upload_time: str,
    vector_index_path: str | None = None,
    saved_path: str | None = None,
) -> int:
    """
    插入视频元数据
    :return: 插入行的 id
    """
    conn = _get_conn()
    try:
        cur = conn.execute(
            """
            INSERT INTO video_metadata (title, upload_time, vector_index_path, saved_path)
            VALUES (?, ?, ?, ?)
            """,
            (title, upload_time, vector_index_path or "", saved_path or ""),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_video_metadata(video_id: int) -> dict | None:
    """根据 id 获取视频元数据"""
    conn = _get_conn()
    try:
        cur = conn.execute(
            "SELECT id, title, upload_time, vector_index_path, saved_path FROM video_metadata WHERE id = ?",
            (video_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "title": row[1],
            "upload_time": row[2],
            "vector_index_path": row[3] or "",
            "saved_path": row[4] or "",
        }
    finally:
        conn.close()


def get_all_videos() -> list[dict]:
    """获取所有视频元数据"""
    conn = _get_conn()
    try:
        cur = conn.execute(
            "SELECT id, title, upload_time, vector_index_path, saved_path FROM video_metadata ORDER BY id DESC"
        )
        return [
            {
                "id": row[0],
                "title": row[1],
                "upload_time": row[2],
                "vector_index_path": row[3] or "",
                "saved_path": row[4] or "",
            }
            for row in cur.fetchall()
        ]
    finally:
        conn.close()


def update_vector_index_path(video_id: int, vector_index_path: str) -> bool:
    """更新视频的向量索引路径（由 NLP/CV 处理后写入）"""
    conn = _get_conn()
    try:
        cur = conn.execute(
            "UPDATE video_metadata SET vector_index_path = ? WHERE id = ?",
            (vector_index_path, video_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
