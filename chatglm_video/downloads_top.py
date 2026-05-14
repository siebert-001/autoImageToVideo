import re
import threading
import time

from playwright.sync_api import Error as PlaywrightError, Locator

from chatglm_video.frames import frame_with_most_video_cards

def top_n_cards_all_finished(page, n: int) -> bool:
    """列表最前 n 张均为已生成完成态（含 div.cover.finished，且无可见 div.loading）。"""
    if n <= 0:
        return True
    fr, _ = frame_with_most_video_cards(page)
    if fr is None:
        return False
    try:
        cards_all = fr.locator("article.card")
        if cards_all.count() < n:
            return False
    except PlaywrightError:
        return False
    for i in range(n):
        card = cards_all.nth(i)
        try:
            if not card.is_visible():
                return False
            if card.locator("div.cover.finished").count() == 0:
                return False
            ld = card.locator("div.loading")
            if ld.count() > 0:
                try:
                    if ld.first.is_visible():
                        return False
                except PlaywrightError:
                    pass
        except PlaywrightError:
            return False
    return True


def wait_top_cards_all_finished(
    page, n: int, stop_event: threading.Event, timeout: float = 2400.0
) -> bool:
    """阻塞直到前 n 张卡片均生成完成，或超时 / 用户中止。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if stop_event.is_set():
            return False
        if top_n_cards_all_finished(page, n):
            return True
        time.sleep(0.45)
    return False


def click_toolbar_download_on_finished_cards(
    page, dom_slot_count: int, max_clicks: int
) -> int:
    """仅在列表最前若干张卡片（当前并发槽位）上点下载，避免点到创作历史里更早完成的视频。

    清影一般把最新任务排在列表最前，故用「前 N 张卡」对应本次提交的 N 个并发。
    生成完成后下载在 `div.toolbar` 的 `img.btn-icon`（约第 3 个）；生成中含 `div.loading` 则跳过。
    """
    if max_clicks <= 0 or dom_slot_count <= 0:
        return 0
    fr, _ = frame_with_most_video_cards(page)
    if fr is None:
        return 0
    clicked = 0
    try:
        cards_all = fr.locator("article.card")
        n_all = cards_all.count()
    except PlaywrightError:
        return 0
    scan = min(dom_slot_count, n_all, 16)
    for i in range(scan):
        if clicked >= max_clicks:
            break
        card = cards_all.nth(i)
        try:
            if not card.is_visible():
                continue
            if card.locator("div.cover.finished").count() == 0:
                continue
            ld = card.locator("div.loading")
            if ld.count() > 0:
                try:
                    if ld.first.is_visible():
                        continue
                except PlaywrightError:
                    pass
            tb = card.locator("div.toolbar")
            if tb.count() == 0:
                continue
            imgs = tb.locator("img.btn-icon")
            ic = imgs.count()
            if ic < 2:
                continue
            if ic >= 4:
                trial = (2, 1, 3)
            elif ic == 3:
                trial = (2, 1)
            elif ic == 2:
                trial = (1, 0)
            else:
                trial = (0,)
            for idx in trial:
                if idx >= ic:
                    continue
                img = imgs.nth(idx)
                try:
                    if not img.is_visible():
                        continue
                    img.scroll_into_view_if_needed(timeout=5000)
                    img.click(timeout=10000)
                    clicked += 1
                    time.sleep(0.4)
                    break
                except PlaywrightError:
                    continue
        except PlaywrightError:
            continue
    return clicked


def click_text_download_in_top_cards(page, dom_slot_count: int, max_clicks: int) -> int:
    """兜底：仅在列表前若干张卡片内查找带「下载」字样的 button。"""
    if max_clicks <= 0 or dom_slot_count <= 0:
        return 0
    fr, _ = frame_with_most_video_cards(page)
    if fr is None:
        return 0
    clicked = 0
    try:
        cards_all = fr.locator("article.card")
        n_all = cards_all.count()
    except PlaywrightError:
        return 0
    scan = min(dom_slot_count, n_all, 16)
    for i in range(scan):
        if clicked >= max_clicks:
            break
        card = cards_all.nth(i)
        try:
            if not card.is_visible():
                continue
            if card.locator("div.cover.finished").count() == 0:
                continue
            btn = card.locator("button", has_text=re.compile("下载"))
            if btn.count() == 0:
                continue
            b0 = btn.first
            if not b0.is_visible():
                continue
            b0.scroll_into_view_if_needed(timeout=5000)
            b0.click(timeout=10000)
            clicked += 1
            time.sleep(0.4)
        except PlaywrightError:
            continue
    return clicked


def _wrap_for_data_index(fr, data_index: int):
    return fr.locator(f'div[data-index="{data_index}"]').first


def _article_in_slot(wrap):
    """列表槽位 div[data-index] 内用于判态/点击的 article.card（若无则退回 wrap）。"""
    inner = wrap.locator("article.card").first
    if inner.count() > 0:
        return inner
    return wrap


def _article_finished(article: Locator) -> bool:
    try:
        if article.count() == 0:
            return False
        if not article.first.is_visible():
            return False
        a0 = article.first
        if a0.locator("div.cover.finished").count() == 0:
            return False
        ld = a0.locator("div.loading")
        if ld.count() > 0:
            try:
                if ld.first.is_visible():
                    return False
            except PlaywrightError:
                pass
        return True
    except PlaywrightError:
        return False


def _click_toolbar_download_on_article(article: Locator) -> bool:
    """已完成卡片上点工具栏下载图标；成功返回 True。"""
    if not _article_finished(article):
        return False
    try:
        a0 = article.first
        tb = a0.locator("div.toolbar")
        if tb.count() == 0:
            return False
        imgs = tb.locator("img.btn-icon")
        ic = imgs.count()
        if ic < 2:
            return False
        if ic >= 4:
            trial = (2, 1, 3)
        elif ic == 3:
            trial = (2, 1)
        elif ic == 2:
            trial = (1, 0)
        else:
            trial = (0,)
        for idx in trial:
            if idx >= ic:
                continue
            img = imgs.nth(idx)
            try:
                if not img.is_visible():
                    continue
                img.scroll_into_view_if_needed(timeout=5000)
                img.click(timeout=10000)
                return True
            except PlaywrightError:
                continue
    except PlaywrightError:
        pass
    return False


def _click_text_download_on_article(article: Locator) -> bool:
    if not _article_finished(article):
        return False
    try:
        a0 = article.first
        btn = a0.locator("button", has_text=re.compile("下载"))
        if btn.count() == 0:
            return False
        b0 = btn.first
        if not b0.is_visible():
            return False
        b0.scroll_into_view_if_needed(timeout=5000)
        b0.click(timeout=10000)
        return True
    except PlaywrightError:
        return False


def count_finished_in_data_index_slots(page, n: int) -> int:
    """data-index 为 0 … n-1 的槽位中，处于已生成完成态的个数。"""
    if n <= 0:
        return 0
    fr, _ = frame_with_most_video_cards(page)
    if fr is None:
        return 0
    c = 0
    for i in range(n):
        try:
            wrap = _wrap_for_data_index(fr, i)
            if wrap.count() == 0:
                continue
            if _article_finished(_article_in_slot(wrap)):
                c += 1
        except PlaywrightError:
            continue
    return c


def data_index_slots_all_finished(page, n: int) -> bool:
    """前 n 个槽位均存在 DOM，且对应卡片均为完成态。"""
    if n <= 0:
        return True
    fr, _ = frame_with_most_video_cards(page)
    if fr is None:
        return False
    for i in range(n):
        try:
            wrap = _wrap_for_data_index(fr, i)
            if wrap.count() == 0:
                return False
            if not _article_finished(_article_in_slot(wrap)):
                return False
        except PlaywrightError:
            return False
    return True


def wait_data_index_slots_all_finished(
    page, n: int, stop_event: threading.Event, timeout: float = 600.0
) -> bool:
    """阻塞直到 data-index 0…n-1 均完成，或超时 / 用户中止。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if stop_event.is_set():
            return False
        if data_index_slots_all_finished(page, n):
            return True
        time.sleep(0.45)
    return False


def click_toolbar_download_finished_in_data_index_slots(
    page, n_scan: int, max_clicks: int
) -> int:
    """仅在 div[data-index="0"]…["n_scan-1"] 内对已完成卡片点工具栏下载。"""
    if max_clicks <= 0 or n_scan <= 0:
        return 0
    fr, _ = frame_with_most_video_cards(page)
    if fr is None:
        return 0
    clicked = 0
    scan = min(n_scan, 16)
    for i in range(scan):
        if clicked >= max_clicks:
            break
        try:
            wrap = _wrap_for_data_index(fr, i)
            if wrap.count() == 0:
                continue
            art = _article_in_slot(wrap)
            if _click_toolbar_download_on_article(art):
                clicked += 1
                time.sleep(0.4)
        except PlaywrightError:
            continue
    return clicked


def click_text_download_finished_in_data_index_slots(
    page, n_scan: int, max_clicks: int
) -> int:
    """同上，兜底用带「下载」字样的 button。"""
    if max_clicks <= 0 or n_scan <= 0:
        return 0
    fr, _ = frame_with_most_video_cards(page)
    if fr is None:
        return 0
    clicked = 0
    scan = min(n_scan, 16)
    for i in range(scan):
        if clicked >= max_clicks:
            break
        try:
            wrap = _wrap_for_data_index(fr, i)
            if wrap.count() == 0:
                continue
            art = _article_in_slot(wrap)
            if _click_text_download_on_article(art):
                clicked += 1
                time.sleep(0.4)
        except PlaywrightError:
            continue
    return clicked