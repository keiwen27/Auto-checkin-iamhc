#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, sys, time, shutil, tempfile
from urllib.parse import quote

import requests

EMAIL         = os.environ.get("EMAIL") or ""
PASSWORD      = os.environ.get("PASSWORD") or ""
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or ""

BASE_URL      = "https://api.hcnsec.cn"
QUOTA_PER_UNIT = 500000 # new-api 默认额度换算比例：500000 quota = 1$

# 站点已开启 Turnstile 人机验证（登录和签到接口都校验，token 单次有效），
# 用真实 Chrome（经 DrissionPage 接管）打开登录页，托管模式挑战会自动通过并产出 token。
TOKEN_WAIT_TIMEOUT = 120  # 单次等待 Turnstile 自动通过的秒数
MAX_SOLVE_RETRIES  = 3    # 获取 token 的最大尝试次数
SHOT_DIR           = "shots"  # 失败时截图保存目录（CI 中作为 artifact 上传）


def find_chrome():
    """按优先级寻找本机 Chrome/Chromium 可执行文件。"""
    candidates = [
        os.environ.get("CHROME_PATH"),
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def click_turnstile(page, tag):
    """在 Turnstile 复选框位置模拟真实鼠标点击。
    挑战 iframe 藏在 shadow root 里，常规元素查询不可见，
    用 CDP pierce 穿透拿到其位置，再派发原始鼠标事件。"""
    try:
        doc = page.run_cdp("DOM.getDocument", depth=-1, pierce=True)
        target = None
        stack = [doc["root"]]
        while stack:
            n = stack.pop()
            if n.get("nodeName", "").lower() == "iframe":
                attrs = n.get("attributes", [])
                ad = {attrs[i]: attrs[i + 1] for i in range(0, len(attrs) - 1, 2)}
                if "challenges.cloudflare.com" in ad.get("src", ""):
                    target = n
                    break
            stack.extend(n.get("children", []) or [])
            stack.extend(n.get("shadowRoots", []) or [])
        if target is None:
            print(f"{tag}CDP 未找到 Turnstile 挑战 iframe", flush=True)
            return False
        box = page.run_cdp("DOM.getBoxModel", nodeId=target["nodeId"])
        q = box["model"]["content"]
        xs, ys = q[0::2], q[1::2]
        left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
        sx = page.run_cdp("Runtime.evaluate", expression="window.scrollX")["result"]["value"]
        sy = page.run_cdp("Runtime.evaluate", expression="window.scrollY")["result"]["value"]
        if right - left < 10 or bottom - top < 10:
            print(f"{tag}Turnstile iframe 尺寸异常: {right-left:.0f}x{bottom-top:.0f}", flush=True)
            return False
        x, y = left - sx + 25, (top + bottom) / 2 - sy  # 复选框位于 iframe 左侧约 25px、垂直居中
        print(f"{tag}Turnstile iframe viewport=({x:.0f},{y:.0f}) size={right-left:.0f}x{bottom-top:.0f}", flush=True)
        page.run_cdp("Input.dispatchMouseEvent", type="mouseMoved", x=x - 60, y=y + 30)
        for i in range(1, 9):
            page.run_cdp("Input.dispatchMouseEvent", type="mouseMoved",
                         x=x - 60 + i * 7.5, y=y + 30 - i * 3.7)
            time.sleep(0.03)
        page.run_cdp("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        time.sleep(0.1)
        page.run_cdp("Input.dispatchMouseEvent", type="mousePressed",
                     x=x, y=y, button="left", clickCount=1, buttons=1)
        time.sleep(0.06)
        page.run_cdp("Input.dispatchMouseEvent", type="mouseReleased",
                     x=x, y=y, button="left", clickCount=1)
        print(f"{tag}已模拟点击 Turnstile 复选框 ({x:.0f},{y:.0f})", flush=True)
        return True
    except Exception as e:
        print(f"{tag}点击异常: {type(e).__name__}: {e}", flush=True)
        return False


def solve_turnstile_token(purpose=""):
    """打开登录页等待 Turnstile 自动通过；超时则模拟点击复选框，仍失败则截图留存。"""
    from DrissionPage import ChromiumOptions, ChromiumPage

    tag = f"[{purpose}] " if purpose else ""
    co = ChromiumOptions()
    co.set_argument("--window-size=1280,900")
    co.set_argument("--no-sandbox")
    co.set_argument("--disable-dev-shm-usage")
    co.set_argument("--disable-gpu")
    co.set_user_data_path(tempfile.mkdtemp(prefix="dp_profile_"))

    chrome_path = find_chrome()
    if chrome_path:
        co.set_browser_path(chrome_path)
    else:
        print(f"{tag}未找到 Chrome 路径，尝试使用 DrissionPage 默认配置")

    os.makedirs(SHOT_DIR, exist_ok=True)
    last_err = ""
    for attempt in range(1, MAX_SOLVE_RETRIES + 1):
        try:
            page = ChromiumPage(co)
            try:
                page.get(f"{BASE_URL}/login", timeout=60, retry=1)
                deadline = time.time() + TOKEN_WAIT_TIMEOUT
                next_click = time.time() + 25  # 先等自动通过，25 秒后开始尝试点击
                clicked = 0
                while time.time() < deadline:
                    ele = page.ele("@name=cf-turnstile-response", timeout=0)
                    if ele:
                        token = ele.attr("value")
                        if token:
                            print(f"{tag}✅ Turnstile token 获取成功 (长度 {len(token)})", flush=True)
                            return token
                    now = time.time()
                    if now >= next_click:
                        clicked += 1
                        next_click = now + 20
                        click_turnstile(page, tag)
                    time.sleep(2)
                last_err = f"等待超时，控件未返回 token（已点击 {clicked} 次）"
            finally:
                try:
                    page.get_screenshot(path=SHOT_DIR, name=f"{purpose or 'solve'}_attempt{attempt}.png")
                except Exception:
                    pass
                page.quit()
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        print(f"{tag}第 {attempt}/{MAX_SOLVE_RETRIES} 次获取 Turnstile token 失败: {last_err}", flush=True)
        time.sleep(3)

    raise RuntimeError(f"{tag}无法获取 Turnstile token: {last_err}")


def login(session: requests.Session, turnstile_token: str):
    """登录并返回用户信息（id + username）。"""
    login_url = f"{BASE_URL}/api/user/login?turnstile={quote(turnstile_token)}"

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/login",
    }

    resp = session.post(
        login_url,
        headers=headers,
        json={"username": EMAIL, "password": PASSWORD},
        timeout=20,
    )

    if resp.status_code != 200:
        print("登录请求失败:", resp.status_code)
        return None

    data = resp.json()
    if not data.get("success"):
        print("登录失败:", data.get("message", ""))
        return None

    user_data = data.get("data", {})
    user_id = user_data.get("id")
    username = user_data.get("username", "")
    if not user_id:
        print("登录成功但未获取到用户 ID")
        return None

    print(f"✅ 登录成功 | 账户: {username} | ID: {user_id}")
    return {"id": user_id, "username": username}


def get_user_info(session: requests.Session, user_id):
    """获取用户信息，返回 data 字典（包含 quota 等字段）。"""
    url = f"{BASE_URL}/api/user/self"

    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0",
        "Referer": BASE_URL,
        "New-Api-User": str(user_id),
    }

    resp = session.get(url, headers=headers, timeout=20)
    data = resp.json()
    if data.get("success"):
        return data.get("data", {})
    return None


def checkin(session: requests.Session, user_id, turnstile_token: str):
    """执行签到，返回签到响应的完整 JSON。"""
    url = f"{BASE_URL}/api/user/checkin?turnstile={quote(turnstile_token)}"

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Origin": BASE_URL,
        "Referer": BASE_URL,
        "New-Api-User": str(user_id),
    }

    resp = session.post(url, headers=headers, json={}, timeout=20)
    return resp.json()


def quota_to_dollar(quota):
    """将内部 quota 值转换为美元金额（整数）。"""
    return round(quota / QUOTA_PER_UNIT)


def send_notification(message):
    print("\n" + "=" * 25)
    print(message)
    print("=" * 25)

    if TG_BOT_TOKEN and TG_CHAT_ID:
        try:
            tg_url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
            resp = requests.post(
                tg_url,
                json={"chat_id": TG_CHAT_ID, "text": message},
                timeout=10,
            )
            if resp.status_code == 200:
                print("Telegram 通知发送成功")
            else:
                print(f"Telegram 通知发送失败: {resp.status_code} {resp.text}")
        except Exception as e:
            print("Telegram 通知发送失败:", e)
    else:
        print("未配置 TG_BOT_TOKEN / TG_CHAT_ID，跳过 Telegram 推送")


def main():
    if not EMAIL or not PASSWORD:
        print("请先设置 EMAIL 和 PASSWORD 环境变量（或在脚本中填写默认值）")
        sys.exit(1)

    # 第 1 步：获取 Turnstile token 并登录
    print("=== 第 1 步：获取 Turnstile token（用于登录） ===")
    try:
        login_token = solve_turnstile_token("登录")
    except Exception as e:
        print("❌", e)
        sys.exit(1)

    session = requests.Session()
    user = login(session, login_token)
    if not user:
        print("\n登录失败，无法继续签到")
        sys.exit(1)

    user_id = user["id"]
    username = user.get("username", str(user_id))

    # 获取签到前余额
    info_before = get_user_info(session, user_id)
    if not info_before:
        print("获取用户信息失败")
        sys.exit(1)
    balance_before = quota_to_dollar(info_before.get("quota", 0))

    # 第 2 步：再获取一个新 token（签到接口同样校验，且 token 一次性有效）
    print("=== 第 2 步：获取 Turnstile token（用于签到） ===")
    try:
        checkin_token = solve_turnstile_token("签到")
    except Exception as e:
        print("❌", e)
        sys.exit(1)

    # 签到
    checkin_data = checkin(session, user_id, checkin_token)

    # 获取签到后余额
    info_after = get_user_info(session, user_id)
    if not info_after:
        print("获取签到后用户信息失败")
        sys.exit(1)
    balance_after = quota_to_dollar(info_after.get("quota", 0))

    # 判断签到结果
    local_time = time.gmtime(time.time() + 8 * 3600)
    now = time.strftime("%Y-%m-%d %H:%M:%S", local_time)
    success = checkin_data.get("success", False)
    msg = str(checkin_data.get("message", ""))

    if success:
        # 签到成功
        awarded_data = checkin_data.get("data", {})
        awarded_quota = awarded_data.get("quota_awarded", 0)
        awarded_dollar = quota_to_dollar(awarded_quota) if awarded_quota else (balance_after - balance_before)
        print(f"✅ 签到成功 | 获得: {awarded_dollar}$")

        message = (
            f"🎁 iamhc 签到通知\n\n"
            f"✅ 签到成功,本次签到获得{awarded_dollar}$\n"
            f"👤 登录账户: {username}\n"
            f"💰 昨日余额: {balance_before}$\n"
            f"💰 当前余额: {balance_after}$\n"
            f"⏱️ 签到时间: {now}"
        )
    elif "已签到" in msg or "重复签到" in msg or "今天已签到" in msg:
        # 今日已签到
        print(f"✅ 今日已签到 | 当前余额: {balance_after}$")

        message = (
            f"🎁 iamhc 签到通知\n\n"
            f"✅ 今日你已经签到过了！\n"
            f"👤 登录账户: {username}\n"
            f"💰 昨日余额: {balance_before}$\n"
            f"💰 当前余额: {balance_after}$\n"
            f"⏱️ 签到时间: {now}"
        )
    else:
        # 签到失败
        print(f"❌ 签到失败 | {msg}")

        message = (
            f"🎁 iamhc 签到通知\n\n"
            f"❌ 签到失败: {msg}\n"
            f"👤 登录账户: {username}\n"
            f"💰 昨日余额: {balance_before}$\n"
            f"💰 当前余额: {balance_after}$\n"
            f"⏱️ 签到时间: {now}"
        )

    # 发送通知
    send_notification(message)


if __name__ == "__main__":
    main()
