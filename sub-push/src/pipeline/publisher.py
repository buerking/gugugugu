import datetime
import json
import logging
import os
import shutil
from ftplib import FTP
from typing import List

try:
    from notifier import Notifier
except Exception:  # pragma: no cover - 兼容本地无 notifier 场景
    class Notifier:  # type: ignore
        @staticmethod
        def send_mail(url: str, user_email: str, num_found: int) -> None:
            logging.getLogger(__name__).warning(
                "Notifier 不可用，跳过真实发信: email=%s num=%s url=%s",
                user_email, num_found, url
            )
from src import settings


logger = logging.getLogger(__name__)


def _collect_user_files(user_id: str) -> List[str]:
    files_json = os.path.join(settings.RESULT_DIR, "files.json")
    if not os.path.exists(files_json):
        return []
    with open(files_json, "r", encoding="utf-8") as f:
        all_files = json.load(f)
    picked: List[str] = []
    for name in all_files:
        # filename: "{keyword}_{user_id}_{timestamp}.json"
        # keyword 里可能包含 "_"，因此从右侧拆分更稳妥
        parts = name.rsplit("_", 2)
        if len(parts) < 3:
            continue
        _keyword, uid, _rest = parts
        if uid == user_id:
            picked.append(name)
    return picked


def _copy_static_files(target_dir: str) -> None:
    # settings 已改为基于 sub-push/config/config.json 加载；项目根目录为 settings.APP_ROOT
    static_dir = os.path.join(settings.APP_ROOT, "src", "web", "static")
    for name in os.listdir(static_dir):
        full = os.path.join(static_dir, name)
        if os.path.isfile(full):
            shutil.copy2(full, target_dir)


def publish_and_notify(user_email: str, user_id: str, num_found: int) -> None:
    user_files = _collect_user_files(user_id)
    if not user_files:
        logger.info("用户 %s 无文件，跳过通知", user_id)
        return

    now = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    folder_name = f"{user_id}_{now}"
    temp_dir = os.path.join(settings.RESULT_DIR, folder_name)
    os.makedirs(temp_dir, exist_ok=True)

    try:
        _copy_static_files(temp_dir)

        file_json = os.path.join(settings.RESULT_DIR, "file.json")
        with open(file_json, "w", encoding="utf-8") as f:
            json.dump(user_files, f, ensure_ascii=False, indent=2)
        shutil.copy2(file_json, temp_dir)

        for name in user_files:
            src = os.path.join(settings.RESULT_DIR, name)
            if os.path.exists(src):
                shutil.copy2(src, temp_dir)

        ftp = FTP()
        ftp.connect(settings.FTP_HOST, settings.FTP_PORT, timeout=20)
        ftp.login(settings.FTP_USER, settings.FTP_PASS)
        try:
            ftp.mkd(folder_name)
        except Exception:
            pass
        ftp.cwd(folder_name)
        for local_name in os.listdir(temp_dir):
            local_path = os.path.join(temp_dir, local_name)
            if os.path.isfile(local_path):
                with open(local_path, "rb") as fp:
                    ftp.storbinary(f"STOR {local_name}", fp)
        ftp.quit()

        url = f"{settings.FTP_WEB_PREFIX}/{folder_name}"
        Notifier.send_mail(url, user_email, num_found)
        logger.info("通知完成: %s -> %s", user_email, url)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

