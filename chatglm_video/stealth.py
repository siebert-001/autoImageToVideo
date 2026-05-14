# 在页面脚本执行前注入，降低常见「自动化环境」检测（无法保证与真人 100% 一致）
STEALTH_INIT_JS = r"""
(() => {
  try {
    Object.defineProperty(navigator, "webdriver", {
      get: () => undefined,
      configurable: true,
    });
  } catch (e) {}

  try {
    if (!window.chrome) window.chrome = {};
    if (!window.chrome.runtime) window.chrome.runtime = {};
  } catch (e) {}

  try {
    Object.defineProperty(navigator, "languages", {
      get: () => Object.freeze(["zh-CN", "zh", "en-US", "en"]),
      configurable: true,
    });
  } catch (e) {}

  try {
    const perms = navigator.permissions;
    if (perms && typeof perms.query === "function") {
      const orig = perms.query.bind(perms);
      perms.query = (parameters) => {
        const name = parameters && parameters.name;
        if (name === "notifications") {
          return Promise.resolve({
            state: Notification.permission,
            onchange: null,
          });
        }
        return orig(parameters);
      };
    }
  } catch (e) {}

  try {
    delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
    delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
    delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
  } catch (e) {}
})();
"""


def apply_stealth_to_context(context) -> None:
    context.add_init_script(STEALTH_INIT_JS)
