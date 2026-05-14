import time

from playwright.sync_api import Error as PlaywrightError

def frame_with_most_video_cards(page):
    """创作列表通常在主文档或某一 iframe 中；取 article.card 最多的 frame，避免点到侧栏。"""
    best_fr, best_n = None, 0
    for fr in page.frames:
        try:
            n = fr.locator("article.card").count()
            if n > best_n:
                best_n = n
                best_fr = fr
        except PlaywrightError:
            continue
    return best_fr, best_n