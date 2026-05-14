import os
import re
import secrets
import threading
import time

from playwright.sync_api import Error as PlaywrightError

from chatglm_video.constants import (
    AUTO_JOB_SRC_STEM_ATTR,
    MAX_TASKS,
    SEL_AUTO_TAGGED_WRAPPER,
    TAGGED_FINISH_DOWNLOAD_DEBOUNCE_S,
)
from chatglm_video.frames import frame_with_most_video_cards

# 防止短时间内对同一列表项重复点「下载」（多处 burst_drain 连扫）。
_tagged_finish_download_last: dict[str, float] = {}

# Vue 列表更新时可能剥掉 data-chatglm-*；打标后记录 job_id + data-index，便于按行恢复。
_active_jobs_lock = threading.Lock()
_active_jobs: list[dict] = []


def new_auto_job_id() -> str:
    """每条自动化任务唯一 ID，写入 data-chatglm-auto-job（与站点其它 data-* 区分）。"""
    return secrets.token_hex(8)


def _job_marker_present_in_frame(fr, job_id: str) -> bool:
    try:
        return fr.locator(f'[data-chatglm-auto-job="{job_id}"]').count() > 0
    except PlaywrightError:
        return False


def _read_data_index_for_job(fr, job_id: str) -> str | None:
    """当前承载该 job_id 的列表行外层 div[data-index]（若可读到）。"""
    try:
        w = fr.locator(
            f'div[data-index]:has(article.card[data-chatglm-auto-job="{job_id}"])'
        ).first
        if w.count() == 0:
            w = fr.locator(f'div[data-index][data-chatglm-auto-job="{job_id}"]').first
        if w.count() == 0:
            return None
        return w.get_attribute("data-index")
    except PlaywrightError:
        return None


def _register_job_after_tag(fr, job_id: str, stem: str | None) -> None:
    idx: str | None = None
    for _ in range(6):
        idx = _read_data_index_for_job(fr, job_id)
        if idx is not None and str(idx).strip() != "":
            break
        time.sleep(0.06)
    stem_v = (stem or "").strip() or None
    with _active_jobs_lock:
        for e in _active_jobs:
            if e["job_id"] == job_id:
                if idx is not None:
                    e["data_index"] = str(idx)
                if stem_v is not None:
                    e["stem"] = stem_v
                return
        _active_jobs.append(
            {"job_id": job_id, "stem": stem_v, "data_index": str(idx) if idx else None}
        )
        while len(_active_jobs) > 48:
            _active_jobs.pop(0)


def active_job_registry_remove(job_id: str | None) -> None:
    if not job_id:
        return
    with _active_jobs_lock:
        _active_jobs[:] = [e for e in _active_jobs if e["job_id"] != job_id]


def _article_row_trackable(art) -> bool:
    try:
        if not art.is_visible():
            return False
        if art.locator("div.cover.finished").count() > 0:
            return True
        ld = art.locator("div.loading")
        if ld.count() > 0:
            try:
                if ld.first.is_visible():
                    return True
            except PlaywrightError:
                pass
        t = (art.inner_text() or "").replace("\n", " ")
        return bool(re.search(r"生成中|视频生成中", t))
    except PlaywrightError:
        return False


def _apply_job_tag_to_wrap(wrap, job_id: str, stem: str | None) -> None:
    stem_s = stem or ""
    wrap.evaluate(
        """(w, params) => {
  const jobId = params.jobId;
  const stem = params.stem || '';
  const a = w.querySelector('article.card');
  w.setAttribute('data-chatglm-auto-job', jobId);
  if (a) a.setAttribute('data-chatglm-auto-job', jobId);
  if (stem) {
    w.setAttribute('data-chatglm-src-stem', stem);
    if (a) a.setAttribute('data-chatglm-src-stem', stem);
  } else {
    w.removeAttribute('data-chatglm-src-stem');
    if (a) a.removeAttribute('data-chatglm-src-stem');
  }
}""",
        {"jobId": job_id, "stem": stem_s},
    )


def restore_job_tags_after_vue_patch(page) -> int:
    """若 Vue 剥标但列表行 data-index 未变，则按注册表写回同一 job_id / stem。返回本轮恢复条数。"""
    fr, _ = frame_with_most_video_cards(page)
    if fr is None:
        return 0
    with _active_jobs_lock:
        entries = list(_active_jobs)
    fixed = 0
    for e in entries:
        jid = e.get("job_id")
        if not jid:
            continue
        if _job_marker_present_in_frame(fr, jid):
            continue
        idx = e.get("data_index")
        if not idx:
            continue
        try:
            wrap = fr.locator(f'div[data-index="{idx}"]').first
            if wrap.count() == 0:
                continue
            art = wrap.locator("article.card").first
            if art.count() == 0 or not _article_row_trackable(art):
                continue
            wj = wrap.get_attribute("data-chatglm-auto-job")
            aj = art.get_attribute("data-chatglm-auto-job")
            if (wj and str(wj).strip()) or (aj and str(aj).strip()):
                continue
            _apply_job_tag_to_wrap(wrap, jid, e.get("stem"))
            fixed += 1
            ni = wrap.get_attribute("data-index")
            if ni and ni != idx:
                with _active_jobs_lock:
                    for u in _active_jobs:
                        if u.get("job_id") == jid:
                            u["data_index"] = ni
                            break
        except PlaywrightError:
            continue
    return fixed


def _tagged_finish_debounce_key(wrap, list_index: int) -> str:
    try:
        idx = wrap.get_attribute("data-index")
        if idx is not None and str(idx).strip() != "":
            return f"di:{idx}"
    except PlaywrightError:
        pass
    return f"li:{list_index}"


def _tagged_finish_should_skip_debounce(key: str) -> bool:
    if TAGGED_FINISH_DOWNLOAD_DEBOUNCE_S <= 0:
        return False
    t = time.monotonic()
    last = _tagged_finish_download_last.get(key, 0.0)
    return (t - last) < TAGGED_FINISH_DOWNLOAD_DEBOUNCE_S


def _tagged_finish_record_download(key: str) -> None:
    _tagged_finish_download_last[key] = time.monotonic()
    if len(_tagged_finish_download_last) > 64:
        cutoff = time.monotonic() - TAGGED_FINISH_DOWNLOAD_DEBOUNCE_S * 4
        dead = [k for k, v in _tagged_finish_download_last.items() if v < cutoff]
        for k in dead[:32]:
            _tagged_finish_download_last.pop(k, None)


def clear_all_job_tags_on_page(page) -> None:
    """清除本脚本写入的任务标记（外层 div 或历史版本写在 article 上的残留）。"""
    for fr in page.frames:
        try:
            fr.evaluate(
                """() => {
  document.querySelectorAll('[data-chatglm-auto-job]').forEach((el) => {
    el.removeAttribute('data-chatglm-auto-job');
  });
  document.querySelectorAll('[data-chatglm-src-stem]').forEach((el) => {
    el.removeAttribute('data-chatglm-src-stem');
  });
}"""
            )
        except PlaywrightError:
            continue
    _tagged_finish_download_last.clear()
    with _active_jobs_lock:
        _active_jobs.clear()


def tag_next_loading_card_in_frame(
    fr,
    source_stem: str | None = None,
    job_id: str | None = None,
) -> bool:
    """在列表中找第一张「生成中且尚未打标」的项，在外层 div[data-index] 与 article 上双写唯一 job id。

    source_stem 若提供，会同时写入 data-chatglm-src-stem，供下载时命名为与图片相同主文件名。
    """
    jid = job_id or new_auto_job_id()
    try:
        ok = bool(
            fr.evaluate(
                """(params) => {
  const stem = (params && params.stem) ? String(params.stem) : '';
  const jobId = (params && params.jobId) ? String(params.jobId) : '';
  if (!jobId) return false;
  const articles = document.querySelectorAll('article.card');
  for (const el of articles) {
    let wrap = el.closest('div[data-index]');
    if (!wrap) wrap = el.parentElement;
    if (!wrap) continue;
    const wj = wrap.getAttribute('data-chatglm-auto-job');
    const ej = el.getAttribute('data-chatglm-auto-job');
    if ((wj != null && wj !== '') || (ej != null && ej !== '')) {
      continue;
    }
    const loading = el.querySelector('div.loading');
    let ok = false;
    if (loading) {
      const r = loading.getBoundingClientRect();
      const cs = window.getComputedStyle(loading);
      if (r.width >= 0.5 && r.height >= 0.5 && cs.visibility !== 'hidden' && cs.display !== 'none'
          && parseFloat(cs.opacity || '1') >= 0.05) {
        ok = true;
      }
    }
    if (!ok) {
      const t = (el.innerText || '').replace(/\\s+/g, ' ');
      if (/生成中|视频生成中/.test(t)) ok = true;
    }
    if (!ok) continue;
    wrap.setAttribute('data-chatglm-auto-job', jobId);
    el.setAttribute('data-chatglm-auto-job', jobId);
    if (stem.length > 0) {
      wrap.setAttribute('data-chatglm-src-stem', stem);
      el.setAttribute('data-chatglm-src-stem', stem);
    }
    return true;
  }
  return false;
}""",
                {"stem": source_stem or "", "jobId": jid},
            )
        )
        if ok:
            _register_job_after_tag(fr, jid, source_stem)
        return ok
    except PlaywrightError:
        return False


def tag_untagged_loading_cards(page, limit: int) -> int:
    """在当前视频列表 frame 内，连续给最多 limit 条「生成中且未打标」的卡片打标；返回本轮新打标条数。"""
    if limit <= 0:
        return 0
    fr, _ = frame_with_most_video_cards(page)
    if fr is None:
        return 0
    done = 0
    for _ in range(limit):
        if not tag_next_loading_card_in_frame(fr, job_id=new_auto_job_id()):
            break
        done += 1
    return done


def try_tag_after_send(
    page,
    source_stem: str | None = None,
    attempts: int = 40,
    delay_s: float = 0.34,
) -> bool:
    """发送成功后轮询打标。

    多条任务几乎同时「生成中」时，DOM 会陆续出现 loading；旧逻辑每轮只打一条且立刻 return，
    容易只标到最新一条。此处每轮最多连续打 MAX_TASKS 条，并在连续若干轮无新标后再结束。

    source_stem 若提供，会优先打标到「本轮第一张新出现的未打标生成中项」上，用于下载文件名与图片主名一致。
    """
    any_ok = False
    idle_rounds = 0
    stem_applied = False
    for _ in range(attempts):
        restore_job_tags_after_vue_patch(page)
        fr, _ = frame_with_most_video_cards(page)
        n = 0
        if source_stem and not stem_applied and fr is not None:
            if tag_next_loading_card_in_frame(
                fr, source_stem=source_stem, job_id=new_auto_job_id()
            ):
                n += 1
                stem_applied = True
        n += tag_untagged_loading_cards(page, MAX_TASKS)
        if n > 0:
            any_ok = True
            idle_rounds = 0
            time.sleep(min(0.22, delay_s * 0.45))
            continue
        idle_rounds += 1
        if any_ok and idle_rounds >= 5:
            break
        time.sleep(delay_s)
    return any_ok


def count_tagged_cards(page) -> int:
    fr, _ = frame_with_most_video_cards(page)
    if fr is None:
        return 0
    try:
        return fr.locator(SEL_AUTO_TAGGED_WRAPPER).count()
    except PlaywrightError:
        return 0


def count_tagged_generating_cards(page) -> int:
    """带标任务中仍处于「生成中」的条数（不含已出片等待下载的完成态）。"""
    fr, _ = frame_with_most_video_cards(page)
    if fr is None:
        return 0
    try:
        wraps = fr.locator(SEL_AUTO_TAGGED_WRAPPER)
        m = wraps.count()
    except PlaywrightError:
        return 0
    n = 0
    for i in range(min(m, 24)):
        wrap = wraps.nth(i)
        try:
            if not wrap.is_visible():
                continue
            art = wrap.locator("article.card").first
            if art.count() == 0:
                continue
            if art.locator("div.cover.finished").count() > 0:
                continue
            ld = art.locator("div.loading")
            if ld.count() > 0:
                try:
                    if ld.first.is_visible():
                        n += 1
                        continue
                except PlaywrightError:
                    pass
            try:
                t = (art.inner_text() or "").replace("\n", " ")
            except PlaywrightError:
                t = ""
            if re.search(r"生成中|视频生成中", t):
                n += 1
        except PlaywrightError:
            continue
    return n


def count_generating_cards_in_list(page) -> int:
    """创作列表中仍处于「生成中」的卡片数（不论是否本脚本打标）。

    用于并发槽位：页面上已有几条在跑，就应计入占用，避免仅靠带标计数在「未打标 / DOM 未就绪」
    时误判有空槽而继续上传。
    """
    fr, _ = frame_with_most_video_cards(page)
    if fr is None:
        return 0
    cards = fr.locator("article.card")
    try:
        m = min(cards.count(), 32)
    except PlaywrightError:
        return 0
    n = 0
    for i in range(m):
        card = cards.nth(i)
        try:
            if not card.is_visible():
                continue
            if card.locator("div.cover.finished").count() > 0:
                continue
            ld = card.locator("div.loading")
            if ld.count() > 0:
                try:
                    if ld.first.is_visible():
                        n += 1
                        continue
                except PlaywrightError:
                    pass
            try:
                t = (card.inner_text() or "").replace("\n", " ")
            except PlaywrightError:
                t = ""
            if re.search(r"生成中|视频生成中", t):
                n += 1
        except PlaywrightError:
            continue
    return n


def all_tagged_cards_finished(page) -> bool:
    """所有带外层标记的列表项内，视频均已生成完成。"""
    fr, _ = frame_with_most_video_cards(page)
    if fr is None:
        return True
    try:
        wraps = fr.locator(SEL_AUTO_TAGGED_WRAPPER)
        m = wraps.count()
        if m == 0:
            return True
    except PlaywrightError:
        return True
    for i in range(min(m, 24)):
        wrap = wraps.nth(i)
        try:
            if not wrap.is_visible():
                return False
            art = wrap.locator("article.card").first
            if art.count() == 0:
                return False
            if art.locator("div.cover.finished").count() == 0:
                return False
            ld = art.locator("div.loading")
            if ld.count() > 0:
                try:
                    if ld.first.is_visible():
                        return False
                except PlaywrightError:
                    pass
        except PlaywrightError:
            return False
    return True


def wait_tagged_cards_finished(
    page, stop_event: threading.Event, timeout: float = 3600.0
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if stop_event.is_set():
            return False
        if all_tagged_cards_finished(page):
            return True
        time.sleep(0.45)
    return False


def untag_job_wrapper(wrap) -> None:
    snippet = """(w) => {
  try { w.removeAttribute('data-chatglm-auto-job'); } catch (e) {}
  try { w.removeAttribute('data-chatglm-src-stem'); } catch (e) {}
  const a = w.querySelector('article.card');
  if (a) {
    try { a.removeAttribute('data-chatglm-auto-job'); } catch (e) {}
    try { a.removeAttribute('data-chatglm-src-stem'); } catch (e) {}
  }
}"""
    for _ in range(3):
        try:
            wrap.evaluate(snippet)
        except PlaywrightError:
            return
        time.sleep(0.1)


def read_wrap_src_stem(wrap) -> str | None:
    try:
        v = wrap.get_attribute(AUTO_JOB_SRC_STEM_ATTR)
        if v and str(v).strip():
            return str(v).strip()
    except PlaywrightError:
        pass
    try:
        art = wrap.locator("article.card").first
        if art.count() > 0:
            v = art.get_attribute(AUTO_JOB_SRC_STEM_ATTR)
            if v and str(v).strip():
                return str(v).strip()
    except PlaywrightError:
        pass
    return None


def finalize_download_save_path(download_dir: str, src_stem: str | None, download) -> str:
    suggested = download.suggested_filename
    base_sug = os.path.basename(suggested)
    _, ext = os.path.splitext(base_sug)
    if not ext:
        ext = ".mp4"
    final_name = f"{src_stem}{ext}" if (src_stem and src_stem.strip()) else base_sug
    path = os.path.join(download_dir, final_name)
    if not os.path.isfile(path):
        return path
    root, e = os.path.splitext(final_name)
    for i in range(1, 1000):
        path = os.path.join(download_dir, f"{root} ({i}){e}")
        if not os.path.isfile(path):
            return path
    return os.path.join(download_dir, f"{root}_dup{e}")


def click_download_img_in_card(
    page, wrap, card, download_dir: str | None
) -> bool:
    """在单个卡片内点工具栏下载图标；若提供 download_dir 则用 expect_download 另存为与图片主名一致。"""
    try:
        if not card.is_visible():
            return False
        tb = card.locator("div.toolbar")
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
                if download_dir and os.path.isdir(download_dir):
                    stem = read_wrap_src_stem(wrap)
                    try:
                        with page.expect_download(timeout=180_000) as dl_info:
                            img.click(timeout=10000)
                        dl = dl_info.value
                        dest = finalize_download_save_path(download_dir, stem, dl)
                        dl.save_as(dest)
                        try:
                            dl.delete()
                        except PlaywrightError:
                            pass
                        return True
                    except Exception:
                        img.click(timeout=10000)
                        return True
                img.click(timeout=10000)
                return True
            except PlaywrightError:
                continue
    except PlaywrightError:
        pass
    return False


def click_download_on_tagged_finished_cards(
    page, max_clicks: int, download_dir: str | None = None
) -> int:
    """仅对外层已打标且内层视频已完成的项点下载，成功后去掉外层标记。"""
    if max_clicks <= 0:
        return 0
    fr, _ = frame_with_most_video_cards(page)
    if fr is None:
        return 0
    clicked = 0
    for _ in range(48):
        if clicked >= max_clicks:
            break
        try:
            wraps = fr.locator(SEL_AUTO_TAGGED_WRAPPER)
            m = wraps.count()
            if m == 0:
                break
        except PlaywrightError:
            break
        progressed = False
        for j in range(min(m, 24)):
            wrap = wraps.nth(j)
            try:
                if not wrap.is_visible():
                    continue
                art = wrap.locator("article.card").first
                if art.count() == 0:
                    continue
                if art.locator("div.cover.finished").count() == 0:
                    continue
                dk = _tagged_finish_debounce_key(wrap, j)
                if _tagged_finish_should_skip_debounce(dk):
                    continue
                ld = art.locator("div.loading")
                if ld.count() > 0:
                    try:
                        if ld.first.is_visible():
                            continue
                    except PlaywrightError:
                        pass
                if click_download_img_in_card(page, wrap, art, download_dir):
                    jid_rm = None
                    try:
                        jid_rm = wrap.get_attribute("data-chatglm-auto-job")
                    except PlaywrightError:
                        pass
                    untag_job_wrapper(wrap)
                    active_job_registry_remove(jid_rm)
                    _tagged_finish_record_download(dk)
                    clicked += 1
                    progressed = True
                    time.sleep(0.68)
                    break
            except PlaywrightError:
                continue
        if not progressed:
            break
    return clicked


def click_text_download_on_tagged_finished_cards(
    page, max_clicks: int, download_dir: str | None = None
) -> int:
    """兜底：外层已打标且已完成项内的「下载」按钮。"""
    if max_clicks <= 0:
        return 0
    fr, _ = frame_with_most_video_cards(page)
    if fr is None:
        return 0
    clicked = 0
    for _ in range(48):
        if clicked >= max_clicks:
            break
        try:
            wraps = fr.locator(SEL_AUTO_TAGGED_WRAPPER)
            m = wraps.count()
            if m == 0:
                break
        except PlaywrightError:
            break
        progressed = False
        for j in range(min(m, 24)):
            wrap = wraps.nth(j)
            try:
                if not wrap.is_visible():
                    continue
                art = wrap.locator("article.card").first
                if art.count() == 0:
                    continue
                if art.locator("div.cover.finished").count() == 0:
                    continue
                dk = _tagged_finish_debounce_key(wrap, j)
                if _tagged_finish_should_skip_debounce(dk):
                    continue
                btn = art.locator("button", has_text=re.compile("下载"))
                if btn.count() == 0:
                    continue
                b0 = btn.first
                if not b0.is_visible():
                    continue
                b0.scroll_into_view_if_needed(timeout=5000)
                if download_dir and os.path.isdir(download_dir):
                    stem = read_wrap_src_stem(wrap)
                    try:
                        with page.expect_download(timeout=180_000) as dl_info:
                            b0.click(timeout=10000)
                        dl = dl_info.value
                        dest = finalize_download_save_path(download_dir, stem, dl)
                        dl.save_as(dest)
                        try:
                            dl.delete()
                        except PlaywrightError:
                            pass
                    except Exception:
                        b0.click(timeout=10000)
                else:
                    b0.click(timeout=10000)
                jid_rm = None
                try:
                    jid_rm = wrap.get_attribute("data-chatglm-auto-job")
                except PlaywrightError:
                    pass
                untag_job_wrapper(wrap)
                active_job_registry_remove(jid_rm)
                _tagged_finish_record_download(dk)
                clicked += 1
                progressed = True
                time.sleep(0.68)
                break
            except PlaywrightError:
                continue
        if not progressed:
            break
    return clicked


def drain_tagged_finished_downloads(
    page, max_clicks: int, download_dir: str | None = None
) -> int:
    """对已打标且已完成的卡片触发下载（图标或「下载」文案）；返回本次成功点击次数。"""
    k = click_download_on_tagged_finished_cards(page, max_clicks, download_dir)
    if k == 0:
        k = click_text_download_on_tagged_finished_cards(
            page, max_clicks, download_dir
        )
    return k