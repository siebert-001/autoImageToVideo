# ===================== 默认参数（可在代码中改） =====================
VIDEO_URL = "https://chatglm.cn/video?lang=zh"
PROMPT = "物体不变形，镜头缓慢平移"
MAX_TASKS = 4
# 每轮提交后，等待 data-index 0..N-1 槽位全部生成完成的最长时间（秒）；超时则只下载已完成的并刷新进入下一轮。
ROUND_GENERATION_TIMEOUT_S = 600.0
# 同一列表项（按 data-index）带标完成态下，两次触发「下载」的最小间隔（秒），防止多处 burst_drain 连点导致重复下载。
TAGGED_FINISH_DOWNLOAD_DEBOUNCE_S = 3.4
# 参考图上传后出现裁切弹层时，要选的比例（如 16:9、1:1、原图）；界面默认可改
REFERENCE_IMAGE_ASPECT = "16:9"
# 主界面「轮次间隔 / 步骤间隔」默认值（秒，闭区间均匀随机）
DEFAULT_ROUND_INTERVAL_S_RANGE = (60, 300)
DEFAULT_STEP_INTERVAL_S_RANGE = (2.0, 5.5)
# 界面默认「图片文件夹」；不存在时点击「开始」会自动创建
DEFAULT_IMAGE_FOLDER = r"D:\Images"
# 界面默认「视频保存到」
DEFAULT_DOWNLOAD_FOLDER = r"D:\Videos"
# 打标写在 article 与外层 div[data-index] 上（双写防 Vue 重绘剥掉其一）。
# data-chatglm-auto-job 的值为每次任务唯一 ID（hex），避免多任务共用同一取值导致列表更新时标记串扰。
AUTO_JOB_ATTR = "data-chatglm-auto-job"
# 与图片同主名的视频文件名（仅主名，扩展名沿用浏览器建议，如 .mp4）
AUTO_JOB_SRC_STEM_ATTR = "data-chatglm-src-stem"
# 已打标：内层 article 带标（任意非空 job id），或仅外层带标（兼容 Vue 只剥内层）
SEL_AUTO_TAGGED_WRAPPER = (
    'div[data-index]:has(article.card[data-chatglm-auto-job]), '
    'div[data-index][data-chatglm-auto-job]:not(:has(article.card[data-chatglm-auto-job]))'
)
# 主循环里步骤间随机间隔（sleep_jittered(base, spread) → 约 [base−spread, base+spread] 秒）。
# 以下为「偏稳」取值：宁可多等几秒，降低页面未就绪 / 浏览器占文件 / 槽位 DOM 未跟上 导致的失败率。
JITTER_UPLOAD_AFTER_FILES = (4.0, 0.65)  # 选完本地文件后，等页面与裁切层反应
JITTER_AFTER_CROP = (1.55, 0.4)  # 裁切确认后
JITTER_AFTER_PROMPT = (1.85, 0.45)  # 填好提示词后、点生成前
# 本条提交并打标/下载扫完后，再开下一张上传（主要控制「下一条」节奏）
JITTER_AFTER_SUBMIT_TAG = (3.2, 0.6)
# 发送「生成视频」成功后，再移回收站：等 Chrome 释放原图句柄
JITTER_AFTER_SEND_BEFORE_RECYCLE = (9.0, 1.8)
JITTER_DOWNLOAD_GAP = (2.45, 0.55)  # 连续触发下载间隔
JITTER_DOWNLOAD_POLL = (0.88, 0.22)  # 轮询等生成/等下载槽
# burst_drain 多轮 drain 之间略歇，等去标 / DOM 稳定后再扫下一轮
JITTER_DRAIN_BATCH_GAP = (0.52, 0.14)
JITTER_BATCH_COOLDOWN = (1.45, 0.35)  # 批次/收尾循环间略歇
JITTER_RETRY_SHORT = (3.2, 0.5)  # 切换模式失败等短重试前（重试后再占槽）
JITTER_RETRY_LONG = (4.0, 0.65)  # 上传异常等长重试前
JITTER_PAGE_RELOAD = (5.5, 0.95)
JITTER_PAGE_RELOAD_LONG = (11.0, 1.5)
# ===================================================================
