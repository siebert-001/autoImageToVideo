import os
import time

try:
    import send2trash
except ImportError:
    send2trash = None  # type: ignore


def default_chrome_profile_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ChatGLMVideoAutomation", "ChromeUserData")


def get_sorted_images(folder):
    exts = (".jpg", ".jpeg", ".png", ".webp")
    files = [f for f in os.listdir(folder) if f.lower().endswith(exts)]
    return [os.path.join(folder, f) for f in sorted(files)]


def safe_filename_stem(path_or_name: str) -> str:
    """用于下载视频命名：取与图片相同的主文件名，去掉扩展并清理 Windows 非法字符。"""
    base = os.path.basename(path_or_name)
    stem, _ = os.path.splitext(base)
    stem = stem.strip()
    if not stem:
        stem = "video"
    bad = '<>:"/\\|?*'
    out = []
    for c in stem:
        if c in bad or ord(c) < 32:
            out.append("_")
        else:
            out.append(c)
    s = "".join(out).rstrip(" .")
    return s or "video"


def move_uploaded_image_to_recycle_bin(path: str) -> bool:
    """站点提交成功后，将本地原图移入系统回收站，避免下次再次入队。

    Chrome 在 set_input_files 后常会短暂占用源文件；主流程在调用本函数前也会先等待。
    此处仍做多轮退避重试，失败则返回 False（若文件已不存在则视为已处理）。
    """
    if not path:
        return False
    if not os.path.isfile(path):
        return True
    if send2trash is None:
        return False
    # 每次失败后多等一会再试，总覆盖约十余秒量级
    delays = (0.45, 0.85, 1.35, 1.9, 2.6, 3.4, 4.2, 5.0)
    for delay_s in delays:
        try:
            send2trash.send2trash(path)
            return True
        except OSError:
            if not os.path.isfile(path):
                return True
            time.sleep(delay_s)
    return False
