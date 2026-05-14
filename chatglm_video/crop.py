import re
import time

from playwright.sync_api import Error as PlaywrightError

from chatglm_video.constants import REFERENCE_IMAGE_ASPECT


def aspect_text_pattern(aspect: str) -> re.Pattern[str]:
    s = (aspect or "16:9").strip().replace("：", ":")
    if ":" in s:
        a, b = s.split(":", 1)
        return re.compile(rf"^\s*{re.escape(a)}\s*[:：]\s*{re.escape(b)}\s*$")
    return re.compile(rf"^\s*{re.escape(s)}\s*$")



def confirm_reference_image_crop_modal(page, aspect: str | None = None) -> bool:
    """
    参考图已写入 input 后，站点常弹出裁切/比例层。
    在此层中选比例并点「上传」，避免再去点「首帧/替换图片」从而弹出系统文件框。
    """
    aspect = aspect or REFERENCE_IMAGE_ASPECT
    pat = aspect_text_pattern(aspect)
    time.sleep(0.7)

    def _try_in(root) -> bool:
        try:
            if root.count() == 0 or not root.first.is_visible():
                return False
        except PlaywrightError:
            return False
        r = root.first
        try:
            chip = r.get_by_text(pat)
            if chip.count() > 0:
                chip.first.click(timeout=6000)
                time.sleep(0.4)
        except PlaywrightError:
            pass
        try:
            upl = r.get_by_role("button", name=re.compile(r"^\s*上传\s*$"))
            if upl.count() == 0:
                upl = r.get_by_text("上传", exact=True)
            if upl.count() > 0:
                upl.first.click(timeout=9000)
                time.sleep(1.0)
                return True
        except PlaywrightError:
            pass
        return False

    scopes = [
        page.locator("[role='dialog']").filter(
            has_text=re.compile(r"上传|替换图片|原图|比例")
        ),
        page.locator("div").filter(has_text=re.compile(r"替换图片")),
        page.locator("div").filter(has_text=re.compile(r"原图")).filter(
            has_text=re.compile(r"上传")
        ),
    ]
    for sc in scopes:
        if _try_in(sc):
            return True

    try:
        if page.get_by_text(pat).count() > 0 and page.get_by_text(
            "上传", exact=True
        ).count() > 0:
            page.get_by_text(pat).first.click(timeout=5000)
            time.sleep(0.4)
            page.get_by_text("上传", exact=True).first.click(timeout=9000)
            time.sleep(1.0)
            return True
    except PlaywrightError:
        pass
    return False