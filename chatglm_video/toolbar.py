import re
import time

from playwright.sync_api import Error as PlaywrightError

def _detect_toolbar_generation_mode(fr) -> str | None:
    """底部当前模式：reference=「参考图生成」；general=「通用生成」；None=未识别。

    药丸常为 div/span，不一定是 button；已在参考图模式时不得误判为需切换。
    """
    try:
        r = fr.evaluate(
            """() => {
  // 清影底部模式药丸：div.prompt-item.cur（无 role=button，文案在 div 内）
  for (const el of document.querySelectorAll(
    'div.prompt-item.cur, .prompt-item.cur, [class*="prompt-item"].cur'
  )) {
    let t = '';
    try {
      t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
    } catch (e) {
      continue;
    }
    if (!t || t.length > 96) continue;
    if (t.includes('参考图生成')) return 'reference';
    if (t.includes('通用生成')) return 'general';
  }
  const inGenDialog = (el) => {
    let p = el;
    for (let i = 0; i < 26 && p; i++) {
      if (p.getAttribute && p.getAttribute('role') === 'dialog') {
        if ((p.innerText || '').includes('生成类型')) return true;
      }
      p = p.parentElement;
    }
    return false;
  };
  const modeFromText = (t) => {
    if (!t) return null;
    t = t.replace(/\\s+/g, ' ').trim();
    if (t.length > 100) return null;
    if (t === '参考图生成' || /^参考图生成(\\s|$)/.test(t)) return '参考图生成';
    if (t === '通用生成' || /^通用生成(\\s|$)/.test(t)) return '通用生成';
    if (t.includes('参考图生成') && t.length <= 56) return '参考图生成';
    if (t.includes('通用生成') && t.length <= 56) return '通用生成';
    return null;
  };
  const hits = [];
  for (const el of document.querySelectorAll(
    'button, [role="button"], [role="tab"], div, span, a'
  )) {
    if (inGenDialog(el)) continue;
    let t = '';
    try {
      t = (el.innerText || el.getAttribute('aria-label') || '')
        .replace(/\\s+/g, ' ')
        .trim();
    } catch (e) {
      continue;
    }
    const hit = modeFromText(t);
    if (!hit) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 3 || r.height < 3) continue;
    const cy = r.top + r.height / 2;
    if (cy < window.innerHeight * 0.28) continue;
    hits.push({
      label: hit,
      bottom: r.bottom,
      left: r.left,
      len: t.length,
    });
  }
  if (!hits.length) return null;
  hits.sort(
    (a, b) => b.bottom - a.bottom || a.left - b.left || a.len - b.len
  );
  const top = hits[0].label;
  if (top === '参考图生成') return 'reference';
  if (top === '通用生成') return 'general';
  return null;
}"""
        )
        if r in ("reference", "general"):
            return r
    except PlaywrightError:
        pass

    try:
        ref = fr.get_by_text(re.compile(r"^\s*参考图生成\s*$"))
        gen = fr.get_by_text(re.compile(r"^\s*通用生成\s*$"))

        def max_bottom_edge(loc) -> float:
            m = -1.0
            for i in range(min(loc.count(), 22)):
                el = loc.nth(i)
                try:
                    if not el.is_visible():
                        continue
                    b = el.bounding_box()
                    if not b:
                        continue
                    edge = float(b["y"] + b["height"])
                    if edge > m:
                        m = edge
                except PlaywrightError:
                    continue
            return m

        rb = max_bottom_edge(ref)
        gb = max_bottom_edge(gen)
        if rb > 0 and rb >= gb:
            return "reference"
        if gb > 0:
            return "general"
    except PlaywrightError:
        pass
    return None


def wait_toolbar_shows_reference(page, timeout: float = 12.0) -> bool:
    """切换后等待底部工具栏稳定显示为「参考图生成」。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for fr in page.frames:
            try:
                if _detect_toolbar_generation_mode(fr) == "reference":
                    return True
            except PlaywrightError:
                continue
        time.sleep(0.35)
    return False


def _click_toolbar_generation_pill_for_type_menu(fr) -> bool:
    """点击底部「通用生成」或「参考图生成」药丸（div.prompt-item），打开「生成类型」浮层。

    当前模式为参考图时，药丸上无「通用生成」文案，必须用本函数点当前 pill 才能打开同一套菜单。
    """
    try:
        return bool(
            fr.evaluate(
                """() => {
  const candidates = [];
  for (const el of document.querySelectorAll(
    'div.prompt-item, div[class*="prompt-item"]'
  )) {
    let t = '';
    try {
      t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
    } catch (e) {
      continue;
    }
    if (!t || t.length > 48) continue;
    if (t !== '通用生成' && t !== '参考图生成' && !t.startsWith('通用生成')
        && !t.startsWith('参考图生成')) {
      continue;
    }
    let p = el;
    let skip = false;
    for (let i = 0; i < 22 && p; i++) {
      if (p.getAttribute && p.getAttribute('role') === 'dialog') {
        if ((p.innerText || '').includes('生成类型')) {
          skip = true;
          break;
        }
      }
      p = p.parentElement;
    }
    if (skip) continue;
    const r = el.getBoundingClientRect();
    if (r.height < 4 || r.width < 8) continue;
    if (r.top + r.height / 2 < window.innerHeight * 0.28) continue;
    candidates.push({ el, bottom: r.bottom });
  }
  if (!candidates.length) return false;
  candidates.sort((a, b) => b.bottom - a.bottom);
  const el = candidates[0].el;
  el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
  return true;
}"""
            )
        )
    except PlaywrightError:
        return False


def _open_toolbar_general_and_pick_reference(fr, page) -> bool:
    """先点底部「通用生成」打开「生成类型」，再在弹窗中选「参考图生成」。"""

    def _click_universal_button() -> bool:
        try:
            btn = fr.get_by_role("button", name=re.compile(r"^\s*通用生成\s*$"))
            if btn.count() == 0:
                btn = fr.locator("button", has_text=re.compile(r"^\s*通用生成\s*$"))
            if btn.count() == 0:
                btn = fr.locator("[role='button']", has_text=re.compile(r"通用生成"))
            for i in range(min(btn.count(), 12) - 1, -1, -1):
                b = btn.nth(i)
                try:
                    if not b.is_visible():
                        continue
                    b.scroll_into_view_if_needed(timeout=5000)
                    b.click(timeout=10000)
                    return True
                except PlaywrightError:
                    continue
        except PlaywrightError:
            pass
        try:
            return bool(
                fr.evaluate(
                    """() => {
  for (const el of document.querySelectorAll('button, [role="button"], div')) {
    let t = '';
    try {
      t = (el.innerText || el.getAttribute('aria-label') || '')
        .replace(/\\s+/g, ' ').trim();
    } catch (e) { continue; }
    if (t !== '通用生成' && !t.startsWith('通用生成')) continue;
    if (t.length > 28) continue;
    let p = el;
    let skip = false;
    for (let i = 0; i < 22 && p; i++) {
      if (p.getAttribute && p.getAttribute('role') === 'dialog') {
        if ((p.innerText || '').includes('生成类型')) { skip = true; break; }
      }
      p = p.parentElement;
    }
    if (skip) continue;
    const r = el.getBoundingClientRect();
    if (r.height < 4 || r.width < 8) continue;
    if (r.top + r.height / 2 < window.innerHeight * 0.34) continue;
    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    return true;
  }
  return false;
}"""
                )
            )
        except PlaywrightError:
            return False
        return False

    try:
        if not _click_universal_button():
            if not _click_toolbar_generation_pill_for_type_menu(fr):
                return False
        time.sleep(0.85)
        if not _confirm_generation_dialogs_on_page(page, "reference"):
            return False
        time.sleep(0.5)
        if not wait_toolbar_shows_reference(page, 12):
            return False
        time.sleep(0.35)
        return True
    except PlaywrightError:
        pass
    return False


def _pick_generation_option_in_dialog(d, mode: str) -> bool:
    """在「生成类型」弹窗内点选一项。mode: reference | general"""
    if mode == "general":
        seq = (
            d.locator("div.duration-item").filter(has_text=re.compile(r"通用生成")),
            d.get_by_text("文生、图生", exact=False),
            d.get_by_text("通用生成", exact=False),
            d.get_by_role("radio", name=re.compile(r"通用")),
        )
    else:
        # 先点列表中的「参考图生成」行（带副标题），避免点到弹窗外的同名小按钮
        seq = (
            d.locator("div.duration-item").filter(has_text=re.compile(r"参考图生成")),
            d.locator("div, li, section, [role='option'], label, button")
            .filter(has_text=re.compile(r"参考图生成"))
            .filter(has_text=re.compile(r"依据上传")),
            d.get_by_text("依据上传的参考图片进行生成", exact=False),
            d.get_by_text("依据上传的参考图片", exact=False),
            d.get_by_text("依据上传", exact=False),
            d.get_by_role("menuitemradio", name=re.compile(r"参考图")),
            d.get_by_role("radio", name=re.compile(r"参考图")),
            d.locator("label", has_text=re.compile(r"参考图生成")),
        )
    for sel in seq:
        try:
            if sel.count() > 0:
                sel.first.click(timeout=9000)
                return True
        except PlaywrightError:
            continue
    return False


def _confirm_generation_dialogs_on_page(page, mode: str) -> bool:
    """若出现「生成类型」浮层则完成点选。

    清影实际 DOM 多为 Element Plus：`el-popover` / `options_popover` 挂在 body，
    往往没有 role=dialog，仅依赖 [role='dialog'] 会永远匹配不到。
    """
    visible = []

    def _collect_from_frame(fr):
        locators = (
            fr.locator("[role='dialog']").filter(has_text=re.compile(r"生成类型")),
            fr.locator(".el-popover").filter(has_text=re.compile(r"生成类型")),
            fr.locator(".el-dropdown__popper").filter(has_text=re.compile(r"生成类型")),
            fr.locator("div.popover-wrap").filter(has_text=re.compile(r"生成类型")),
            fr.locator("[class*='options_popover']").filter(
                has_text=re.compile(r"生成类型")
            ),
        )
        for dlg in locators:
            try:
                n = dlg.count()
                if n == 0:
                    continue
                for i in range(min(n, 8)):
                    d0 = dlg.nth(i)
                    if d0.is_visible():
                        visible.append(d0)
                        return
            except PlaywrightError:
                continue

    for fr in page.frames:
        try:
            _collect_from_frame(fr)
        except PlaywrightError:
            continue
    if not visible:
        return True
    for d0 in visible:
        if _pick_generation_option_in_dialog(d0, mode):
            return True
    return False


def enter_image2video(page) -> bool:
    """清影：先点「通用生成」→ 弹窗「生成类型」→ 选「参考图生成」；若已是参考图模式则跳过。"""

    def _scroll_for_bottom_toolbar(fr):
        try:
            fr.evaluate(
                "() => window.scrollTo(0, Math.max(document.body?.scrollHeight || 0, document.documentElement?.scrollHeight || 0))"
            )
        except PlaywrightError:
            pass
        try:
            page.keyboard.press("End")
        except PlaywrightError:
            pass
        time.sleep(0.45)

    needles_legacy = ("图片生成视频", "以图生视频", "图生视频")

    def _legacy_playwright(target) -> bool:
        _scroll_for_bottom_toolbar(target)
        try:
            loc = target.get_by_role("button", name=re.compile(r"图片生成视频|以图生视频"))
            if loc.count() > 0:
                for i in range(min(loc.count(), 12)):
                    btn = loc.nth(i)
                    try:
                        if not btn.is_visible():
                            continue
                        btn.scroll_into_view_if_needed(timeout=5000)
                        btn.click(timeout=10000)
                        time.sleep(0.35)
                        return True
                    except PlaywrightError:
                        continue
        except PlaywrightError:
            pass

        for word in needles_legacy:
            try:
                loc = target.get_by_text(word, exact=False)
                n = loc.count()
                if n == 0:
                    continue
                for idx in range(n - 1, -1, -1):
                    el = loc.nth(idx)
                    try:
                        if not el.is_visible():
                            continue
                        el.scroll_into_view_if_needed(timeout=5000)
                        el.click(timeout=10000, force=False)
                        time.sleep(0.35)
                        return True
                    except PlaywrightError:
                        try:
                            el.click(timeout=8000, force=True)
                            time.sleep(0.35)
                            return True
                        except PlaywrightError:
                            continue
            except PlaywrightError:
                continue

        for sel, pat in (
            ("button", re.compile(r"图片生成视频")),
            ("div", re.compile(r"图片生成视频")),
        ):
            loc = target.locator(sel, has_text=pat)
            try:
                if loc.count() == 0:
                    continue
            except PlaywrightError:
                continue
            for idx in range(min(loc.count(), 8) - 1, -1, -1):
                btn = loc.nth(idx)
                try:
                    if not btn.is_visible():
                        continue
                    btn.scroll_into_view_if_needed(timeout=5000)
                    btn.click(timeout=10000)
                    time.sleep(0.35)
                    return True
                except PlaywrightError:
                    continue
        return False

    def _legacy_js(target) -> bool:
        try:
            return bool(
                target.evaluate(
                    """() => {
  const needles = ['通用生成', '图片生成视频', '以图生视频', '图生视频'];
  const matches = (t) => {
    if (!t || t.length > 320) return false;
    const s = t.replace(/\\s+/g, ' ').trim();
    return needles.some((n) => s.includes(n));
  };
  const candidates = [];
  document.querySelectorAll('button, [role="button"], a, div, span').forEach((el) => {
      let t = '';
      try { t = (el.innerText || '').replace(/\\s+/g, ' ').trim(); } catch (e) { return; }
      if (!matches(t)) return;
      let p = el;
      for (let i = 0; i < 18 && p; i++) {
        if (p.getAttribute && p.getAttribute('role') === 'dialog') {
          const tx = (p.innerText || '');
          if (tx.includes('生成类型')) return;
        }
        p = p.parentElement;
      }
      const r = el.getBoundingClientRect();
      if (r.width < 8 || r.height < 4) return;
      const cs = window.getComputedStyle(el);
      if (cs.visibility === 'hidden' || cs.display === 'none') return;
      if (parseFloat(cs.opacity || '1') < 0.05) return;
      const cy = r.top + r.height / 2;
      if (cy < window.innerHeight * 0.35) return;
      candidates.push({ el, bottom: r.bottom, area: r.width * r.height });
    });
  if (!candidates.length) return false;
  candidates.sort((a, b) => b.bottom - a.bottom || a.area - b.area);
  candidates[0].el.click();
  return true;
}"""
                )
            )
        except PlaywrightError:
            return False

    frames = list(page.frames)
    orderings = [frames, list(reversed(frames))]

    for round_idx in range(4):
        try:
            page.wait_for_load_state("domcontentloaded", timeout=8000)
        except PlaywrightError:
            pass
        time.sleep(0.55 if round_idx == 0 else 0.3)

        for ordering in orderings:
            for fr in ordering:
                _scroll_for_bottom_toolbar(fr)
                m = _detect_toolbar_generation_mode(fr)
                if m == "reference":
                    return True
                if m == "general" and _open_toolbar_general_and_pick_reference(fr, page):
                    return True

        for ordering in orderings:
            for fr in ordering:
                _scroll_for_bottom_toolbar(fr)
                if _detect_toolbar_generation_mode(fr) is None and _open_toolbar_general_and_pick_reference(
                    fr, page
                ):
                    return True

        for ordering in orderings:
            for fr in ordering:
                clicked = _legacy_playwright(fr)
                if not clicked:
                    clicked = _legacy_js(fr)
                    if clicked:
                        time.sleep(0.75)
                if not clicked:
                    continue
                time.sleep(0.45)
                if not _confirm_generation_dialogs_on_page(page, "reference"):
                    return False
                time.sleep(0.4)
                return True

        try:
            page.keyboard.press("Home")
            time.sleep(0.2)
            page.keyboard.press("End")
            time.sleep(0.35)
        except PlaywrightError:
            pass

    return False