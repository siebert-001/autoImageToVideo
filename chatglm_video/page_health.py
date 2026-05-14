from playwright.sync_api import Error as PlaywrightError

def is_page_crashed(page) -> bool:
    """仅识别明显整页故障，避免把正常页面里的「错误」「超时」等文案当成崩溃。"""
    try:
        u = page.url.lower()
        if "chrome-error" in u or "chromewebdata" in u or u.startswith("chrome://crash"):
            return True
        content = page.content().lower()
    except PlaywrightError:
        return True
    # 断网/浏览器错误页、典型网关错误（短词如「超时」会误伤业务页，已移除）
    markers = (
        "无法访问此网站",
        "err_connection",
        "err_name_not_resolved",
        "err_timed_out",
        "502 bad gateway",
        "503 service unavailable",
        "504 gateway time-out",
        "nginx 502",
        "应用崩溃了",
        "页面崩溃了",
        "系统繁忙，请稍后再试",
    )
    return any(m.lower() in content for m in markers)