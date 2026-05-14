"""判断是否已在清影「生成视频」页：按你的约定，进入该路径即视为已登录，不再弹确认窗。"""

import time
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError


def video_studio_appears_logged_in(page, settle_s: float = 0.5) -> bool:
    """当前 URL 为 chatglm 且路径含 /video，且未落在明显登录/授权页，则视为已在生成视频页。"""
    time.sleep(settle_s)
    try:
        u = page.url or ""
        parsed = urlparse(u)
    except PlaywrightError:
        return False

    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    full = u.lower()

    if "chatglm.cn" not in host:
        return False
    if any(
        x in full
        for x in (
            "/login",
            "passport",
            "signin",
            "sign-in",
            "authorize",
            "oauth",
        )
    ):
        return False
    return "/video" in path
