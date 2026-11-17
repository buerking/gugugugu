"""小规模验证：只跑前 N 个关键词任务的一轮 first_run。"""

from __future__ import annotations

import logging
import os
import sys

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)

from src.pipeline import search_engine  # noqa: E402


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 3

    orig = search_engine.load_search_jobs

    def limited(**kwargs):
        jobs, users, meta = orig(**kwargs)
        jobs = [j for j in jobs if j.job_type == "keyword"][:limit]
        users = {j.sub_user_id: users[j.sub_user_id] for j in jobs if j.sub_user_id in users}
        return jobs, users, meta

    search_engine.load_search_jobs = limited  # type: ignore
    summary = search_engine.run_search_round(first_run=True)
    print(summary)


if __name__ == "__main__":
    main()
