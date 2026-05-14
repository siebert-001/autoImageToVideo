import time

from playwright.sync_api import Error as PlaywrightError

def click_video_send_button(page, timeout: float = 75.0) -> bool:
    """清影提交：底部为 div.btn-group + 纸飞机 svg，无「生成视频」文案；未就绪时带 .disabled。

    在提示词与参考图就绪后，需等到 :not(.disabled) 再点。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        for fr in page.frames:
            bases = (
                fr.locator("div.config-wrapper div.btn-group"),
                fr.locator("div.wrapper div.btn-group"),
                fr.locator("div.btn-group"),
            )
            for base in bases:
                try:
                    row = base.filter(has=fr.locator("svg"))
                    n = row.count()
                    if n == 0:
                        continue
                    for i in range(min(n, 12)):
                        el = row.nth(i)
                        try:
                            if not el.is_visible():
                                continue
                            cls = el.get_attribute("class") or ""
                            if "disabled" in cls.split():
                                continue
                            tx = el.inner_text(timeout=2000).replace("\n", " ").strip()
                            if any(
                                k in tx
                                for k in ("特效", "参数", "水印", "音效", "去水印")
                            ):
                                continue
                            if len(tx) > 14:
                                continue
                            el.scroll_into_view_if_needed(timeout=5000)
                            el.click(timeout=12000)
                            return True
                        except PlaywrightError:
                            continue
                except PlaywrightError:
                    continue
        time.sleep(0.35)
    return False