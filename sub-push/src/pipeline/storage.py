import datetime
import json
import logging
import os
import shutil
from typing import Dict, Any

from src import settings


logger = logging.getLogger(__name__)


def ensure_dirs() -> None:
    os.makedirs(settings.RESULT_DIR, exist_ok=True)
    os.makedirs(settings.ARCHIVE_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(settings.SEEN_DB_PATH) or ".", exist_ok=True)


def reset_batch_state() -> None:
    """
    每轮任务开始前重置索引文件，避免累积历史导致重复上传/重复通知。
    """
    ensure_dirs()
    for name in ("files.json", "file.json"):
        path = os.path.join(settings.RESULT_DIR, name)
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def archive_and_clear_results() -> None:
    """
    每轮任务结束后，把 search_results 下的文件归档到 old/，并清空当前目录（保留 old/）。
    行为与原脚本一致：下一轮会重新生成 files.json。
    """
    ensure_dirs()
    for name in os.listdir(settings.RESULT_DIR):
        if name == os.path.basename(settings.ARCHIVE_DIR):
            continue
        src = os.path.join(settings.RESULT_DIR, name)
        dst = os.path.join(settings.ARCHIVE_DIR, name)
        try:
            if os.path.isfile(src):
                if os.path.exists(dst):
                    os.remove(dst)
                shutil.move(src, dst)
            elif os.path.isdir(src):
                # 临时目录等，直接删除
                shutil.rmtree(src, ignore_errors=True)
        except Exception as exc:
            logger.warning("归档/清理失败: %s (%s)", name, exc)


def save_search_result(subscription: Dict[str, Any], merged_result: Dict[str, Any]) -> str:
    ensure_dirs()
    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"{subscription['keyword']}_{subscription['sub_user_id']}_{timestamp}.json"
    file_path = os.path.join(settings.RESULT_DIR, filename)

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(merged_result, f, ensure_ascii=False, indent=2)
    logger.info("搜索结果已保存: %s", file_path)

    index_path = os.path.join(settings.RESULT_DIR, "files.json")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            files = json.load(f)
    else:
        files = []
    files.append(filename)
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(files, f, ensure_ascii=False, indent=2)
    return filename

