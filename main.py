import os
import sys
import json
import time
import logging
import ssl
from datetime import datetime, timedelta
import re
import random
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formatdate, make_msgid, formataddr
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import traceback
import contextlib
import io
import subprocess


def short_error(error):
    """将 Playwright 的长错误压缩成一行，避免批量任务刷屏。"""
    return str(error).splitlines()[0]


def play_prompt_sound(sound_name="Glass"):
    """
    播放系统提示音（提醒用户手动滑动滑块）：
    - macOS: 调用系统自带的 afplay 播放 /System/Library/Sounds 下的指定系统音频（默认 Glass.aiff）
    - Windows: 调用 winsound.MessageBeep
    - 其他: 回退到终端铃声
    """
    try:
        if sys.platform == "darwin":
            sound_file = f"/System/Library/Sounds/{sound_name}.aiff"
            if not os.path.exists(sound_file):
                sound_file = "/System/Library/Sounds/Ping.aiff"
            if os.path.exists(sound_file):
                subprocess.Popen(["afplay", sound_file], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                sys.stdout.write("\a")
                sys.stdout.flush()
        elif sys.platform.startswith("win"):
            import winsound
            winsound.MessageBeep()
        else:
            sys.stdout.write("\a")
            sys.stdout.flush()
    except Exception:
        try:
            sys.stdout.write("\a")
            sys.stdout.flush()
        except Exception:
            pass


# 配置日志
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
LOG_PATH = os.path.join(BASE_DIR, "aiyifan.log")

# 为避免在交互环境 / 多次导入时重复添加 handler，先清空根 logger 的已有 handler，
# 再只添加文件 handler；这样不会向终端输出，也不会重复打印多次。
root_logger = logging.getLogger()
for _h in list(root_logger.handlers):
    root_logger.removeHandler(_h)

file_handler = logging.FileHandler(LOG_PATH)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[file_handler],
)

# 不再在模块导入时向终端打印环境信息，保持脚本静默

# 邮件配置参数（默认占位，实际值从本地配置文件加载）
EMAIL_HOST = None  # SMTP服务器地址
EMAIL_PORT = None  # SMTP端口
EMAIL_USER = None  # 发件人邮箱
EMAIL_PASS = None  # 邮箱授权码或密码
EMAIL_TO = None    # 收件人邮箱


def load_email_config():
    """
    从本地的 JSON 配置文件加载邮箱配置。
    """
    global EMAIL_HOST, EMAIL_PORT, EMAIL_USER, EMAIL_PASS, EMAIL_TO

    try:
        config_path = os.path.join(BASE_DIR, "account_data", "email_config.json")
        if not os.path.exists(config_path):
            logging.warning("email_config.json 不存在，邮件发送功能将被禁用")
            return

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        EMAIL_HOST = cfg.get("EMAIL_HOST", EMAIL_HOST)
        EMAIL_PORT = cfg.get("EMAIL_PORT", EMAIL_PORT)
        EMAIL_USER = cfg.get("EMAIL_USER", EMAIL_USER)
        EMAIL_PASS = cfg.get("EMAIL_PASS", EMAIL_PASS)
        EMAIL_TO = cfg.get("EMAIL_TO", EMAIL_TO)

        logging.info("已从 email_config.json 加载邮件配置")
    except Exception as e:
        logging.error(f"加载 email_config.json 失败: {e}")


# 启动时尝试加载一次邮件配置
load_email_config()


def _load_yfsp_login_entry_css():
    """可选：account_data/yfsp_ui_config.json 中的 login_entry_css，站点改版时可强制指定登录入口。"""
    path = os.path.join(BASE_DIR, "account_data", "yfsp_ui_config.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        sel = (cfg.get("login_entry_css") or "").strip()
        return sel or None
    except Exception as e:
        logging.warning(f"读取 yfsp_ui_config.json 失败: {e}")
        return None


# 爱壹帆顶栏头像右侧「登录」文案节点（Chrome 复制；站方改版后可用 yfsp_ui_config.json / YFSP_LOGIN_ENTRY_CSS 覆盖）
YFSP_BUILTIN_LOGIN_CSS_FALLBACKS = (
    "body > div.root-container > div > div.top-nav.fixed > div > div > div.box.justify-content-end > div.user-block.d-flex.d-inline-flex > div > div > span",
    "div.top-nav.fixed div.user-block.d-flex span",
    "div.top-nav.fixed div.user-block span",
)


def check_login_status(page):
    """
    检查页面当前是否处于登录状态。
    可靠性准则：必须以正向凭据为准，坚决不使用‘未见登录按钮即算登录’的负向猜测逻辑。
    """
    try:
        print("正在检查登录状态...")

        # 1. 如果当前在个人中心路由，优先通过个人资产/金币元素确凿判定
        if "user" in page.url.lower():
            user_assets = page.locator('div[title="金币"], [class*="coin"], .user-info, .user-card').first
            if user_assets.count() > 0 and user_assets.is_visible():
                print("✅ 个人中心检测到有效资产/用户信息，确认已登录")
                return True

        # 2. 尝试获取非 unknown 的登录用户名
        username = get_username(page)
        if username and username not in ("unknown", "unknown_user", ""):
            print(f"✅ 检测到登录用户名: {username}")
            return True

        # 3. 尝试查找用户头像 / 已登录用户卡片
        user_avatar = page.locator('div.user-avatar:visible, div.avatar.logged-in, img.avatar[src*="avatar"], .user-profile:visible, [class*="avatar"]:visible').first
        if user_avatar and user_avatar.count() > 0:
            try:
                if user_avatar.is_visible():
                    print("✅ 检测到已登录头像元素")
                    return True
            except Exception:
                pass

        # 4. 检查是否有明确的退出登录入口（只有已登录才会出现）
        logout_btn = page.locator('a:has-text("退出"), button:has-text("退出"), [href*="logout"]').first
        if logout_btn and logout_btn.count() > 0 and logout_btn.is_visible():
            print("✅ 检测到退出登录按钮，确认已登录")
            return True

        # 5. 检查明确的未登录标志（顶栏或页面上有登录/注册入口）
        login_button = page.locator('button:has-text("登录"), a:has-text("登录"), span:has-text("登录"), div.user-block:has-text("登录"), [class*="login"]:visible').first
        if login_button and login_button.count() > 0 and login_button.is_visible():
            print("❌ 检测到登录入口元素，当前为未登录状态")
            return False

        register_button = page.locator('button:has-text("注册"), a:has-text("注册"), [class*="register"]:visible').first
        if register_button and register_button.count() > 0 and register_button.is_visible():
            print("❌ 检测到注册入口元素，当前为未登录状态")
            return False

        # 6. 安全兜底：未找到任何有效登录凭证时，坚决判定为未登录（彻底杜绝假阳性）
        print("❌ 未检测到有效登录凭据（无用户名、无头像、无资产数据），判定为未登录")
        return False

    except Exception as e:
        msg = str(e).lower()
        if "has been closed" in msg or ("closed" in msg and "target" in msg):
            print("❌ 页面或浏览器已关闭，无法判断登录状态")
        else:
            print(f"❌ 检查登录状态时出错: {short_error(e)}")
        return False


def verify_account_session(page, timeout_ms=25000):
    """
    基于服务端鉴权事实严格检验当前会话（Session/Cookie）是否有效。
    原理：
    服务端对有效会话会正常渲染 /user/index 并返回金币/资料等用户数据；
    若 Session 失效，服务端会重定向到首页 / 或不返回任何个人中心资产。
    返回: True (服务端鉴权有效) / False (已失效或未登录)
    """
    try:
        try:
            page.goto("https://www.yfsp.tv/user/index", timeout=timeout_ms, wait_until="domcontentloaded")
        except Exception as e:
            print(f"⚠️ 导航到个人中心超时: {short_error(e)}，尝试继续检查页面")

        time.sleep(2.5)

        # 1. 检查是否重定向离开了个人中心
        if "user" not in page.url.lower():
            return False

        # 2. 检查个人中心核心资产元素或用户资料元素
        has_assets = page.evaluate("""() => {
            const coinEl = document.querySelector('div[title="金币"], [class*="coin"]');
            if (coinEl && coinEl.textContent.trim().length > 0) return true;

            const profileEl = document.querySelector('.user-info, .user-name, .nickname, [class*="profile"]');
            if (profileEl && profileEl.textContent.trim().length > 0) return true;

            const logoutEl = document.querySelector('a[href*="logout"], a:has-text("退出")');
            if (logoutEl) return true;

            return false;
        }""")

        return bool(has_assets)
    except Exception as e:
        print(f"⚠️ 会话鉴权校验发生异常: {short_error(e)}")
        return False

def get_username(page):
    """尝试获取当前登录用户名"""
    try:
        # 尝试多种方法获取用户名
        username = page.evaluate('''() => {
            // 方法1: 从用户元素获取
            const userElements = document.querySelectorAll('.username, .user-name, .account-name, .nickname, [class*="username"]:not([class*="login"]), [class*="user-name"]:not([class*="login"])');
            for (const el of userElements) {
                if (el.textContent && el.textContent.trim() && !el.textContent.includes('登录') && !el.textContent.includes('注册')) {
                    return el.textContent.trim();
                }
            }
            
            // 方法2: 从localStorage获取
            const storageKeys = ['userInfo', 'user', 'userData', 'account'];
            for (const key of storageKeys) {
                const data = localStorage.getItem(key);
                if (data) {
                    try {
                        const parsed = JSON.parse(data);
                        const possibleNameKeys = ['username', 'nickname', 'name', 'userName', 'displayName'];
                        for (const nameKey of possibleNameKeys) {
                            if (parsed[nameKey] && typeof parsed[nameKey] === 'string') {
                                return parsed[nameKey];
                            }
                        }
                    } catch (e) {}
                }
            }
            
            // 方法3: 从页面其他元素推断
            const profileElements = document.querySelectorAll('.user-profile, .profile, .user-info');
            for (const el of profileElements) {
                if (el.textContent && el.textContent.trim() && !el.textContent.includes('登录') && !el.textContent.includes('注册')) {
                    return el.textContent.trim().slice(0, 20); // 限制长度
                }
            }
            
            return 'unknown';
        }''')
        
        # 如果获取到的用户名是空或只包含空白字符，返回unknown
        if not username or username.strip() == '':
            return 'unknown'
        
        # 验证用户名不是"登录"或"注册"
        if '登录' in username or '注册' in username:
            return 'unknown'
            
        return username
    except:
        return "unknown_user"

def save_storage_state(context, file_path):
    """保存浏览器状态（cookies, localStorage等）"""
    storage = context.storage_state()
    with open(file_path, 'w') as f:
        json.dump(storage, f)
    print(f"✅ 已保存浏览器状态到 {file_path}")

def load_storage_state(context, file_path):
    """加载保存的浏览器状态，包括cookies和localStorage"""
    try:
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                storage = json.load(f)
            
            # 加载cookies
            if 'cookies' in storage:
                context.add_cookies(storage['cookies'])
                print(f"✅ 成功加载 {len(storage['cookies'])} 个cookies")
            
            # 加载localStorage
            if 'origins' in storage:
                # 创建一个临时页面并导航到目标网站
                page = context.new_page()
                try:
                    # 先导航到目标网站以获取正确的源
                    try:
                        page.goto("https://www.yfsp.tv", wait_until="domcontentloaded", timeout=45000)
                    except PlaywrightTimeoutError as e:
                        print(f"⚠️ 恢复 localStorage 时打开首页超时，继续使用已加载 cookies: {short_error(e)}")
                    
                    # 处理origins是列表的情况
                    if isinstance(storage['origins'], list):
                        for origin_data in storage['origins']:
                            if 'origin' in origin_data and 'localStorage' in origin_data:
                                try:
                                    # 设置localStorage
                                    for item in origin_data['localStorage']:
                                        if 'name' in item and 'value' in item:
                                            page.evaluate(f"localStorage.setItem('{item['name']}', '{item['value']}')")
                                    print(f"✅ 成功设置 {len(origin_data['localStorage'])} 个localStorage项")
                                except Exception:
                                    # 静默处理localStorage访问错误
                                    pass
                    # 处理origins是字典的情况
                    else:
                        for origin, storage_data in storage['origins'].items():
                            if 'localStorage' in storage_data:
                                try:
                                    # 设置localStorage
                                    for key, value in storage_data['localStorage'].items():
                                        page.evaluate(f"localStorage.setItem('{key}', '{value}')")
                                    print(f"✅ 成功设置 {len(storage_data['localStorage'])} 个localStorage项")
                                except Exception:
                                    # 静默处理localStorage访问错误
                                    pass
                finally:
                    page.close()
            
            print(f"✅ 成功从 {file_path} 加载浏览器状态")
            return True
        else:
            print(f"❌ 未找到保存的状态文件: {file_path}")
            return False
    except Exception as e:
        print(f"❌ 加载浏览器状态时出错: {str(e)}")
        return False

def logout(page):
    """登出当前账号"""
    try:
        # 尝试点击头像
        avatar = page.locator('.user-avatar, .avatar, .user-profile').first
        if avatar.count() > 0:
            avatar.click()
            time.sleep(1)
            
            # 尝试点击登出按钮
            logout_btn = page.locator('a:has-text("退出"), a:has-text("登出"), button:has-text("退出")').first
            if logout_btn.count() > 0:
                logout_btn.click()
                time.sleep(2)
                print("✅ 已成功登出")
                return True
        
        # 如果上面的方法失败，尝试直接清除登录状态
        page.evaluate('''() => {
            localStorage.clear();
            sessionStorage.clear();
            document.cookie.split(";").forEach(function(c) { 
                document.cookie = c.replace(/^ +/, "").replace(/=.*/, "=;expires=" + new Date().toUTCString() + ";path=/"); 
            });
        }''')
        page.reload()
        time.sleep(2)
        print("✅ 已通过清除存储登出")
        return True
    except Exception as e:
        print(f"❌ 登出失败: {str(e)}")
        return False

def get_current_coins(page):
    """
    从个人中心页面提取当前账号的金币数量，若无法获取则返回 None。
    使用爱壹帆页面标准的 div[title='金币'] 定位器及正则提取纯数字。
    """
    try:
        coins_el = page.locator('div[title="金币"], [class*="coin"]').first
        if coins_el and coins_el.count() > 0 and coins_el.is_visible():
            text = coins_el.inner_text()
            match = re.search(r'\d+', text)
            if match:
                return int(match.group())

        # JavaScript 兜底查询
        coins_data = page.evaluate('''() => {
            const coinDiv = document.querySelector('div[title="金币"], [class*="coin"]');
            if (coinDiv) {
                return coinDiv.textContent.trim();
            }
            return null;
        }''')
        if coins_data:
            match = re.search(r'\d+', coins_data)
            if match:
                return int(match.group())
    except Exception as e:
        print(f"⚠️ 读取当前金币数量异常: {short_error(e)}")
    return None


def find_sign_in_button(page):
    """
    在个人中心寻找当前处于高亮、可点击状态的真实签到按钮。
    准则（方案一：彻底杜绝模版幽灵按钮）：
    1. 严格排除任何位于弹窗模版内部的节点（.dialog, .modal, #signInModal 等）。
    2. 严格可见性检查：通过 checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }) 与 getComputedStyle 过滤透明/隐藏节点。
    3. 文本规范化后严格全等'立即签到'，严格使用 innerText（屏幕实际渲染文本），坚决不用 textContent！
    4. 处于未禁用状态（非 disabled、非 gray、非 aria-disabled）。
    返回: Playwright Locator 或 None
    """
    try:
        target_found = page.evaluate('''() => {
            // 清理可能遗留的标记
            document.querySelectorAll('[data-target-signin="true"]').forEach(el => el.removeAttribute('data-target-signin'));
            
            const candidates = Array.from(document.querySelectorAll('button, a, div[role="button"], [class*="btn"], span, div, p'));
            const matches = [];
            for (const el of candidates) {
                // 1. 严格排除弹窗与模版容器内的元素
                if (el.closest('.dialog, .modal, .popup, [role="dialog"], #signInModal, [class*="dialog"], [class*="modal"], [class*="popup"]')) {
                    continue;
                }

                // 2. 严格可见性校验
                if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
                if (typeof el.checkVisibility === 'function') {
                    if (!el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) continue;
                } else {
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
                }
                
                // 3. 严格使用 innerText（屏幕实际可见文字），绝对不用 textContent
                const cleanText = (el.innerText || '').replace(/\\s+/g, '');
                if (cleanText === '立即签到') {
                    const className = el.className ? String(el.className) : '';
                    const disabled = el.hasAttribute('disabled') || 
                                     el.getAttribute('aria-disabled') === 'true' ||
                                     className.includes('disabled') || 
                                     className.includes('gray') ||
                                     className.includes('btn-disable');
                    if (!disabled) {
                        matches.push(el);
                    }
                }
            }

            if (matches.length === 0) return false;

            // 优先选择更具按钮特征的标签或类名
            let best = matches[0];
            for (const el of matches) {
                const tag = el.tagName.toLowerCase();
                const role = el.getAttribute('role') || '';
                const cls = el.className ? String(el.className).toLowerCase() : '';
                if (tag === 'button' || tag === 'a' || role === 'button' || cls.includes('btn')) {
                    best = el;
                    break;
                }
            }

            best.setAttribute('data-target-signin', 'true');
            return true;
        }''')
        if target_found:
            loc = page.locator('[data-target-signin="true"]').first
            if loc.count() > 0 and loc.is_visible():
                return loc
    except Exception as e:
        print(f"⚠️ 查找立即签到按钮异常: {short_error(e)}")
    return None


def check_already_signed_in_ui(page):
    """
    检查个人中心主界面上是否已经呈现'已签到'状态。
    严格排除弹窗内模版节点，严格校验真实可见性。
    """
    try:
        status_info = page.evaluate('''() => {
            const candidates = Array.from(document.querySelectorAll('button, a, div[role="button"], [class*="btn"], span, div, p'));
            for (const el of candidates) {
                // 排除弹窗与模版容器
                if (el.closest('.dialog, .modal, .popup, [role="dialog"], #signInModal, [class*="dialog"], [class*="modal"], [class*="popup"]')) {
                    continue;
                }
                if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
                if (typeof el.checkVisibility === 'function') {
                    if (!el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) continue;
                } else {
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
                }

                // 严格使用 innerText
                const cleanText = (el.innerText || '').replace(/\\s+/g, '');
                if (cleanText === '已签到') {
                    return { signed: true, text: '已签到' };
                }
            }
            return { signed: false };
        }''')
        if status_info and status_info.get('signed'):
            return status_info.get('text', '已签到')
        return None
    except Exception:
        return None


def wait_for_sign_in_section(page, timeout_sec=15):
    """
    动态轮询等待个人中心单页应用(SPA)完成用户资产与签到组件的异步渲染。
    优先级法则（方案二）：
    1. 优先查验主界面是否已经是灰色的'已签到'。若已签到且无高亮待签按钮，直接判定此前已签/冷却中，绝不进行盲目点击。
    2. 若未签到，且主界面明确挂载了真实可见的'立即签到'，返回待签按钮。
    """
    start_time = time.time()
    
    # 适当平滑滚动触发页面可见区域的懒加载与挂载
    try:
        page.evaluate("window.scrollTo(0, 300)")
        time.sleep(0.5)
        page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass

    while time.time() - start_time < timeout_sec:
        already_signed_text = check_already_signed_in_ui(page)
        btn = find_sign_in_button(page)
        
        # 优先判定：主界面已明确呈现'已签到'且无有效'立即签到'按钮（处于冷却中）
        if already_signed_text and btn is None:
            return 'already_signed', already_signed_text
            
        # 待签判定：主界面明确挂载了真实可点击的'立即签到'
        if btn is not None:
            return 'ready_to_sign', btn
            
        time.sleep(1)
        
    return 'timeout', None


def handle_check_in_dialog(page, timeout_sec=6):
    """
    处理点击'立即签到'后弹出的确认小窗口，并点击'即刻签到'。
    爱壹帆核心签到逻辑：点击主页面的'立即签到'后会弹出小窗口，
    用户必须点击弹窗中的'即刻签到'按钮（或 id='signInBtn'）签到才真正完成。
    """
    print("正在等待签到弹窗及'即刻签到'按钮...")
    start_time = time.time()
    while time.time() - start_time < timeout_sec:
        try:
            # 1. 优先通过专用 ID 定位: #signInBtn
            sign_in_btn = page.locator('#signInBtn:visible').first
            if sign_in_btn.count() > 0 and sign_in_btn.is_visible():
                print("✅ 找到弹窗确认按钮(id='signInBtn')，正在点击...")
                sign_in_btn.scroll_into_view_if_needed()
                time.sleep(0.3)
                sign_in_btn.click(timeout=3000)
                print("✅ 已点击'即刻签到'按钮")
                time.sleep(2.5)
                return True

            # 2. 通过文本严格匹配'即刻签到'
            instant_btn = page.locator('button, a, div[role="button"], [class*="btn"], div, span') \
                              .filter(has_text=re.compile(r'^\s*即刻签到\s*$')) \
                              .first
            if instant_btn.count() > 0 and instant_btn.is_visible():
                print("✅ 找到文本为'即刻签到'的弹窗按钮，正在点击...")
                instant_btn.scroll_into_view_if_needed()
                time.sleep(0.3)
                instant_btn.click(timeout=3000)
                print("✅ 已点击'即刻签到'按钮")
                time.sleep(2.5)
                return True

            # 3. DOM 内部直接搜索 #signInBtn 或包含'即刻签到'的可点击元素并触发点击
            clicked = page.evaluate('''() => {
                const btnById = document.getElementById('signInBtn');
                if (btnById && btnById.offsetWidth > 0 && btnById.offsetHeight > 0) {
                    btnById.click();
                    return 'id:signInBtn';
                }
                
                const allEls = Array.from(document.querySelectorAll('button, a, [role="button"], [class*="btn"], div, span'));
                for (const el of allEls) {
                    if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
                    const text = el.innerText ? el.innerText.trim() : '';
                    if (text === '即刻签到') {
                        el.click();
                        return 'text:即刻签到';
                    }
                }
                
                // 4. 检查是否有弹窗容器内部的确认按钮（确定/确认）
                const dialogs = Array.from(document.querySelectorAll('.dialog, .modal, .popup, [class*="dialog"], [class*="modal"], [class*="popup"], [role="dialog"]'))
                                     .filter(d => d.offsetWidth > 0 && d.offsetHeight > 0);
                for (const d of dialogs) {
                    const buttons = Array.from(d.querySelectorAll('button, a, [role="button"], [class*="btn"], div, span'));
                    for (const btn of buttons) {
                        if (btn.offsetWidth === 0 || btn.offsetHeight === 0) continue;
                        const text = btn.innerText ? btn.innerText.trim() : '';
                        if (['确定', '确认', '即刻签到', '立即签到'].includes(text)) {
                            btn.click();
                            return 'dialog:' + text;
                        }
                    }
                }
                return null;
            }''')
            if clicked:
                print(f"✅ 通过 JavaScript 成功点击弹窗确认按钮 ({clicked})")
                time.sleep(2.5)
                return True

        except Exception as e:
            pass

        time.sleep(0.5)

    print("⚠️ 未检测到'即刻签到'弹窗按钮，可能已直接生效或弹窗未能弹出")
    return False


def check_sign_in_success(page, coins_before=None):
    """
    基于不可伪造的客观事实验证签到是否真正成功。
    金标准法则（方案三：以金币增加为绝对核心）：
    在获取到 coins_before 的前提下，【金币资产真实增加】是唯一的成败铁律！
    若金币未增加，即使页面挂着'已签到'，也绝不承认成功（杜绝把旧状态误当新成果）。
    """
    try:
        time.sleep(2)

        # 1. 检查金币是否已即时增加
        coins_after = get_current_coins(page)
        if coins_before is not None and coins_after is not None:
            if coins_after > coins_before:
                print(f"✅ 金币资产已真实增加：{coins_before} ➡️ {coins_after}（服务端入账铁证）")
                return True

        # 2. 若 DOM 未即时更新，刷新个人中心页面进行最终核验
        print("当前金币尚未刷新，正在刷新个人中心页面进行最终复核...")
        try:
            page.reload(wait_until="domcontentloaded", timeout=20000)
            time.sleep(3)

            coins_after_reload = get_current_coins(page)
            if coins_before is not None and coins_after_reload is not None:
                if coins_after_reload > coins_before:
                    print(f"✅ 刷新后确认金币增加：{coins_before} ➡️ {coins_after_reload}（服务端入账铁证）")
                    return True
                else:
                    print(f"❌ 刷新后金币未发生增加：{coins_before} ➡️ {coins_after_reload}，服务端未给予入账（签到未生效）")
                    return False
        except Exception as e:
            print(f"⚠️ 刷新页面复核出错: {short_error(e)}")

        # 3. 仅在未成功读取 coins_before 的极罕见异常兜底下，才辅助参考 UI 翻转
        if coins_before is None:
            if check_already_signed_in_ui(page) and find_sign_in_button(page) is None:
                print("⚠️ 无法获取基准金币数，仅通过按钮翻转为'已签到'降级判定为完成")
                return True

        print("❌ 未检测到确凿的金币增加入账证据，签到未成功")
        return False
    except Exception as e:
        print(f"❌ 签到成功核验过程中出错: {short_error(e)}")
        return False


def direct_click_sign_in_button(page):
    """兼容旧接口：定位并点击签到按钮"""
    btn = find_sign_in_button(page)
    if btn:
        btn.click()
        handle_check_in_dialog(page)
        return True
    return False



def share_video(page):
    """在网站上分享视频（点击分享按钮唤起小窗，点击社交平台图标打开新标签页完成任务）"""
    try:
        print("\n===== 开始分享视频 =====")
        
        # 监听浏览器打开的新标签页（点击分享图标会触发 target="_blank" 或 window.open）
        opened_tabs = []
        def _on_new_page(new_tab):
            opened_tabs.append(new_tab)
        try:
            page.context.on("page", _on_new_page)
        except Exception:
            pass

        # 导航到动漫列表页面，而不是首页
        print("正在导航到动漫列表页面...")
        page.goto("https://www.yfsp.tv/list/anime?orderBy=1", timeout=45000, wait_until="domcontentloaded")
        time.sleep(3)
        
        # 确保页面完全加载
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception as e:
            print(f"⚠️ 等待页面加载超时，继续执行: {short_error(e)}")
        time.sleep(2)  # 额外等待，确保JavaScript完成渲染
        
            
        # 首先检查页面是否成功加载并包含动漫列表
        page_title = page.title()
        print(f"页面标题: {page_title}")
        
        # 使用多种选择器查找视频链接
        video_selectors = [
            'a[href*="/video/"]',  # 原来的选择器
            '.video-item a',       # 常见的视频项目链接
            '.anime-item a',       # 动漫项目链接
            '.video-card a',       # 视频卡片链接
            '.item a',             # 通用项目链接
            'a.video-link',        # 视频链接类
            'a[href*="/play/"]',   # 播放链接
            'a[href*="/anime/"]',  # 动漫详情链接
            '.list-item a'         # 列表项链接
        ]
        
        videos = []
        for selector in video_selectors:
            found_videos = page.locator(selector).all()
            if found_videos and len(found_videos) > 0:
                videos = found_videos
                break
        
        # 如果常规选择器没找到，使用JavaScript更广泛地搜索
        if not videos:
            print("使用JavaScript搜索视频链接...")
            video_links = page.evaluate('''() => {
                // 查找所有链接
                const allLinks = Array.from(document.querySelectorAll('a[href]'));
                
                // 过滤出可能是视频的链接
                const videoLinks = allLinks.filter(link => {
                    const href = link.getAttribute('href');
                    return href.includes('/video/') || 
                           href.includes('/play/') || 
                           href.includes('/anime/') ||
                           href.includes('/watch/');
                });
                
                return videoLinks.map(link => {
                    const rect = link.getBoundingClientRect();
                    return {
                        href: link.getAttribute('href'),
                        text: link.textContent.trim(),
                        x: rect.left + rect.width/2,
                        y: rect.top + rect.height/2,
                        visible: rect.width > 0 && rect.height > 0
                    };
                });
            }''')
            
            if video_links and len(video_links) > 0:
                visible_links = [link for link in video_links if link.get('visible')]
                if visible_links:
                    print(f"通过JavaScript找到 {len(visible_links)} 个可见的视频链接")
                    # 从中随机选择一个链接点击
                    selected_link = random.choice(visible_links)
                    print(f"选择视频链接: {selected_link.get('href')} - {selected_link.get('text')}")
                    
                    # 直接导航到视频页面，而不是点击
                    video_href = selected_link.get('href')
                    video_url = "https://www.yfsp.tv" + video_href if video_href.startswith("/") else video_href
                    # print(f"直接导航到视频页面: {video_url}")
                    try:
                        page.goto(video_url, timeout=30000, wait_until="domcontentloaded")
                    except Exception as e:
                        print(f"⚠️ 导航到视频页面超时: {short_error(e)}，尝试继续")
                    time.sleep(5)
                    
                    # 等待视频页面加载
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=15000)
                    except Exception as e:
                        print(f"等待视频页面加载时出错: {str(e)}，继续执行")
                    time.sleep(3)  # 额外等待
                else:
                    print("❌ 未找到可见的视频链接")
                    # 尝试点击页面上的第一个项目
                    try:
                        print("尝试点击页面上的第一个项目...")
                        page.click('.item:first-child, .video-item:first-child, .anime-item:first-child')
                        time.sleep(5)
                    except Exception as e:
                        print(f"点击第一个项目失败: {str(e)}")
                        return False
            else:
                print("❌ 通过JavaScript也未找到视频链接")

                return False
        
        # 如果找到了视频列表
        if videos:
            # 从找到的视频中随机选择一个
            random_index = random.randint(0, min(10, len(videos) - 1))
            selected_video = videos[random_index]
            
            print(f"找到 {len(videos)} 个视频，选择第 {random_index + 1} 个视频进行分享")
            
            # 尝试获取链接
            try:
                href = selected_video.get_attribute("href")
                if href:
                    full_url = "https://www.yfsp.tv" + href if href.startswith("/") else href
                    # print(f"直接导航到视频页面: {full_url}")
                    try:
                        page.goto(full_url, timeout=30000, wait_until="domcontentloaded")
                    except Exception as e:
                        print(f"⚠️ 导航到视频页面超时: {short_error(e)}，尝试继续")
                    time.sleep(5)
                    
                    # 等待视频页面加载
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=15000)
                    except Exception as e:
                        print(f"等待视频页面加载时出错: {str(e)}，继续执行")
                    time.sleep(3)  # 额外等待
                else:
                    print("无法获取视频链接地址")
                    try:
                        # 点击选定的视频
                        selected_video.click()
                        time.sleep(5)
                    except Exception as e:
                        print(f"点击视频时出错: {str(e)}")
                        return False
            except Exception as e:
                print(f"获取视频链接时出错: {str(e)}")
                try:
                    # 点击选定的视频
                    selected_video.click()
                    time.sleep(5)
                except Exception as e:
                    print(f"点击视频时出错: {str(e)}")
                    return False

            
        # 获取当前视频页面URL和标题
        video_url = page.url
        video_title = page.title()
        print(f"当前视频: {video_title}")
        
        # 验证当前页面是否为视频页面
        if not ("/video/" in video_url or "/play/" in video_url or "/watch/" in video_url or "/anime/" in video_url):
            print("⚠️ 警告：当前页面可能不是视频页面")


        # 强制等待页面完全加载，确保所有元素都已渲染
        print("等待页面完全加载...")
        try:
            page.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception as e:
            print(f"⚠️ 等待页面加载超时，继续执行: {short_error(e)}")
        time.sleep(3)  # 额外等待

        # 稍微平滑下滚，确保播放器下方的工具栏（收藏、点赞、分享）完整渲染并在视口内
        try:
            page.evaluate("window.scrollBy(0, 350)")
            time.sleep(1)
        except Exception:
            pass

        print("\n开始精确定位分享按钮...")

        # 1. 查找主分享触发按钮（播放器下方的节点状分享图标，悬停显示“分享”）
        share_btn = None
        selectors = [
            'button[title="分享"]',
            '[title="分享"]',
            'button[aria-label="分享"]',
            '[aria-label="分享"]',
            '[data-title="分享"]',
            '[data-tooltip="分享"]',
            '.iconfont.iconfenxiang',
            '[class*="iconfenxiang"]',
            '[class*="icon-fenxiang"]',
            '[class*="icon-share"]',
            'div[class*="share-btn"]',
            'div[class*="share"]:has(.iconfont)',
            'div[class*="share"]:has(svg)',
            'button:has-text("分享")',
            '[role="button"]:has-text("分享")',
        ]
        for sel in selectors:
            loc = page.locator(f"{sel}:visible").first
            if loc.count() > 0 and loc.is_visible():
                share_btn = loc
                print(f"找到节点状分享图标按钮: {sel}")
                break

        if not share_btn:
            # JS 兜底定位：查找 title/aria-label/class 为分享的图标或按钮
            js_found = page.evaluate('''() => {
                const els = Array.from(document.querySelectorAll('button, a, div[role="button"], span, div, i, svg'));
                for (const el of els) {
                    if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
                    const aria = (el.getAttribute('aria-label') || '').trim();
                    const title = (el.getAttribute('title') || '').trim();
                    const dataTitle = (el.getAttribute('data-title') || el.getAttribute('data-tooltip') || '').trim();
                    const cls = el.className ? String(el.className) : '';
                    if (aria === '分享' || title === '分享' || dataTitle === '分享' || cls.includes('iconfenxiang') || cls.includes('icon-share')) {
                        const target = el.closest('button, [role="button"], a') || el;
                        target.scrollIntoView({behavior: 'smooth', block: 'center'});
                        return true;
                    }
                }
                return false;
            }''')
            if js_found:
                share_btn = page.locator('button[title="分享"], [title="分享"], button[aria-label="分享"], [aria-label="分享"], .iconfont.iconfenxiang, [class*="iconfenxiang"]').first

        if not share_btn or share_btn.count() == 0:
            print("❌ 未能定位到任何分享主按钮，分享失败")
            return False

        # 2. 点击'分享'主按钮唤起弹出的小窗
        print("正在点击'分享'按钮唤起分享小窗...")
        try:
            share_btn.scroll_into_view_if_needed()
            time.sleep(0.5)
            share_btn.highlight()
            time.sleep(0.3)
            share_btn.click(force=True)
            print("✅ 已点击'分享'按钮")
        except Exception as e:
            print(f"⚠️ 点击分享按钮异常: {short_error(e)}，尝试通过JS点击")
            try:
                page.evaluate("(el) => el.click()", share_btn.element_handle())
            except Exception as e2:
                print(f"❌ JS点击依然失败: {short_error(e2)}")
                return False

        # 等待小窗弹出与动画完成
        print("正在等待分享小窗及平台图标加载...")
        time.sleep(2)

        # 3. 在弹出的分享小窗中寻找真实的社交平台分享图标（微博 / QQ / 微信 / Facebook / X / Telegram）
        print("正在小窗中定位真实的分享平台图标（微博 / QQ / 微信 / Facebook / X / Telegram）...")

        # 从小窗中提取真实的目标平台图标候选（严格限制在确认的 6 个平台内，彻底杜绝误判）
        candidates = page.evaluate('''() => {
            const dialogs = Array.from(document.querySelectorAll(
                '.dialog, .modal, .popup, [role="dialog"], [class*="dialog"], [class*="modal"], [class*="popup"], [class*="share"], .popover'
            )).filter(el => {
                if (el.offsetWidth === 0 || el.offsetHeight === 0) return false;
                if (el.querySelector('video') || el.classList.contains('player')) return false;
                const s = window.getComputedStyle(el);
                return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
            });

            // 确认为爱壹帆网站分享小窗内的 6 大官方平台
            const platformDefs = [
                { key: 'weibo', name: '微博', matches: (s, t, c) => s.includes('weibo') || s.includes('微博') },
                { key: 'qq', name: 'QQ', matches: (s, t, c) => s.includes('qq') || s.includes('qzone') || s.includes('connect.qq') },
                { key: 'wechat', name: '微信', matches: (s, t, c) => s.includes('wechat') || s.includes('weixin') || s.includes('微信') },
                { key: 'facebook', name: 'Facebook', matches: (s, t, c) => s.includes('facebook') || c.includes('icon-fb') || c.includes('share-fb') },
                { key: 'x', name: 'X', matches: (s, t, c) => s.includes('twitter') || s.includes('x.com') || s.includes('推特') || s.includes('分享到x') || t === 'x' || c.includes('icon-x') },
                { key: 'telegram', name: 'Telegram', matches: (s, t, c) => s.includes('telegram') || s.includes('t.me') || s.includes('电报') }
            ];

            const badWords = ['二维码', '立刻观看', '海量', '评分', '暂无评分', '关闭', 'close', '×', '看《', '快来和我', '万', '分', '免费在线观看'];

            const list = [];
            for (const d of dialogs) {
                const items = Array.from(d.querySelectorAll('a, button, [role="button"], [class*="icon"], img, svg, div, span'));
                for (const item of items) {
                    if (item.offsetWidth === 0 || item.offsetHeight === 0) continue;
                    const text = (item.innerText || item.textContent || '').trim();
                    if (text.length > 25) continue;
                    if (badWords.some(b => text.includes(b))) continue;

                    const href = (item.getAttribute('href') || '').toLowerCase();
                    const cls = (item.className ? String(item.className) : '').toLowerCase();
                    const title = (item.getAttribute('title') || '').toLowerCase();
                    const aria = (item.getAttribute('aria-label') || '').toLowerCase();
                    const alt = (item.getAttribute('alt') || '').toLowerCase();

                    if (badWords.some(b => cls.includes(b) || aria.includes(b) || title.includes(b))) continue;

                    const combined = (title + ' ' + aria + ' ' + alt + ' ' + href + ' ' + cls + ' ' + text).toLowerCase();
                    const t = text.toLowerCase();

                    for (const p of platformDefs) {
                        if (p.matches(combined, t, cls)) {
                            const target = item.closest('a, button, [role="button"]') || item;
                            target.setAttribute('data-target-share-platform', p.key);
                            list.push({
                                key: p.key,
                                name: p.name,
                                displayName: title || aria || text || p.name,
                                href: target.getAttribute('href') || '',
                                hasHref: Boolean(target.getAttribute('href') && target.getAttribute('href') !== '#' && target.getAttribute('href') !== 'javascript:;')
                            });
                            break;
                        }
                    }
                }
            }

            // 按 platform key 去重，确保每个平台至多一个独立候选
            const res = [];
            const seen = new Set();
            for (const item of list) {
                if (!seen.has(item.key)) {
                    seen.add(item.key);
                    res.push(item);
                }
            }

            // 优先点击通常会打开独立网页的平台 (QQ, Facebook, Telegram, Weibo, X)，微信常为弹内二维码放在后备
            const priority = { 'qq': 10, 'facebook': 9, 'telegram': 8, 'weibo': 7, 'x': 6, 'wechat': 1 };
            res.sort((a, b) => {
                if (a.hasHref !== b.hasHref) return (b.hasHref ? 1 : 0) - (a.hasHref ? 1 : 0);
                return (priority[b.key] || 0) - (priority[a.key] || 0);
            });
            return res;
        }''')

        print(f"小窗内检测到 {len(candidates)} 个候选平台图标: {[c['displayName'] for c in candidates]}")
        if not candidates:
            print("❌ 未能在分享小窗中找到任何合法的平台分享图标，分享失败")
            return False

        # 4. 逐个尝试点击候选图标，直到确凿打开目标平台新标签页
        new_tab_detected = False
        tab_url = ""

        for idx, cand in enumerate(candidates):
            print(f"正在点击分享平台图标 [{idx+1}/{len(candidates)}]: '{cand['displayName']}'({cand['name']})...")
            tabs_before_count = len(opened_tabs)

            # 通过绑定的 data-target-share-platform 属性精准点击
            clicked = page.evaluate('''(key) => {
                const el = document.querySelector(`[data-target-share-platform="${key}"]`);
                if (el) {
                    const target = el.closest('a, button, [role="button"]') || el;
                    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    target.click();
                    return true;
                }
                return false;
            }''', cand['key'])

            if not clicked:
                try:
                    loc = page.locator(f'[data-target-share-platform="{cand["key"]}"]').first
                    if loc.count() > 0:
                        loc.click(force=True, timeout=3000)
                        clicked = True
                except Exception:
                    pass

            # 等待最多 3 秒检测是否有目标平台新标签页打开
            for _ in range(6):
                if len(opened_tabs) > tabs_before_count:
                    new_tab_detected = True
                    break
                time.sleep(0.5)

            if new_tab_detected:
                opened_tab = opened_tabs[-1]
                try:
                    tab_url = opened_tab.url
                except Exception:
                    pass
                print(f"✅ 成功打开目标平台标签页: {tab_url or cand['displayName']}（服务端任务判定达成）")
                try:
                    time.sleep(1)
                    opened_tab.close()
                    print("✅ 已安全关闭目标平台标签页")
                except Exception:
                    pass
                break
            else:
                print(f"⚠️ 点击 '{cand['displayName']}' 后未检测到新标签页打开，尝试下一个图标...")

        # 铁律：必须真正检测到新标签页打开，才算分享成功！
        if not new_tab_detected:
            print("❌ 所有候选图标点击后均未唤起目标平台标签页，分享失败")
            return False

        print("✅ 完成视频分享操作")
        return True
    
    except Exception as e:
        print(f"❌ 分享视频时出错: {str(e)}")
        traceback.print_exc()
        return False



def cleanup_shared_logs(max_keep=30):
    """限制 account_data/shared 目录下的状态日志文件数量，最多保留最新的 max_keep 份。"""
    data_dir = 'account_data'
    shared_dir = os.path.join(data_dir, 'shared')
    if not os.path.exists(shared_dir):
        return

    try:
        files = [
            f for f in os.listdir(shared_dir)
            if f.endswith('_status.txt') and os.path.isfile(os.path.join(shared_dir, f))
        ]
        files.sort()  # YYYY-MM-DD_status.txt 按字母升序即按日期先后排序
        if len(files) > max_keep:
            to_remove = files[:-max_keep]
            for fname in to_remove:
                fpath = os.path.join(shared_dir, fname)
                try:
                    os.remove(fpath)
                except Exception as err:
                    logging.warning(f"删除历史日志文件失败 {fpath}: {err}")
    except Exception as e:
        logging.warning(f"清理 shared 状态日志失败: {e}")


def get_today_status_file_path(date_str=None):
    """获取指定日期的状态日志文件路径"""
    data_dir = 'account_data'
    shared_dir = os.path.join(data_dir, 'shared')
    os.makedirs(shared_dir, exist_ok=True)
    if not date_str:
        date_str = datetime.now().strftime('%Y-%m-%d')
    return os.path.join(shared_dir, f"{date_str}_status.txt")


def init_today_status_file(accounts=None, ignored_accounts=None, date_str=None):
    """
    初始化今日账号打卡状态表格文件。
    若今日文件不存在，则按账号列表生成打卡表格骨架：
    yifan1 - 签到: ⏳ 未执行 --- 分享: ⏳ 未执行 --- 金币: ⏳ 未获取
    yifan5 - 签到: ⏭️ 已忽略 --- 分享: ⏭️ 已忽略 --- 金币: ⏭️ 已忽略
    """
    filename = get_today_status_file_path(date_str)
    if os.path.exists(filename):
        return filename

    data_dir = 'account_data'
    if accounts is None:
        try:
            accounts = get_all_accounts()
        except Exception:
            accounts_file = os.path.join(data_dir, "accounts.txt")
            if os.path.exists(accounts_file):
                with open(accounts_file, 'r', encoding='utf-8') as f:
                    accounts = [l.strip() for l in f if l.strip()]
            else:
                accounts = []

    if ignored_accounts is None:
        try:
            ignored_accounts = set(get_ignored_accounts())
        except Exception:
            ignored_file = os.path.join(data_dir, "ignored_accounts.txt")
            if os.path.exists(ignored_file):
                with open(ignored_file, 'r', encoding='utf-8') as f:
                    ignored_accounts = set([l.strip() for l in f if l.strip()])
            else:
                ignored_accounts = set()

    lines = []
    for acc in accounts:
        if acc in ignored_accounts:
            lines.append(f"{acc} - 签到: ⏭️ 已忽略 --- 分享: ⏭️ 已忽略 --- 金币: ⏭️ 已忽略\n")
        else:
            lines.append(f"{acc} - 签到: ⏳ 未执行 --- 分享: ⏳ 未执行 --- 金币: ⏳ 未获取\n")

    with open(filename, 'w', encoding='utf-8') as f:
        f.writelines(lines)

    cleanup_shared_logs(30)
    return filename


def update_account_status(account_name, operation_type, success=True, time_str=None, date_str=None, coins=None):
    """
    就地更新今日表格中指定账号的操作状态、执行时间以及最新金币数。
    格式: yifan1 - 签到: ✅（13:22:24） --- 分享: ✅（15:20:48） --- 金币: 1828
    """
    now = datetime.now()
    if not date_str:
        date_str = now.strftime('%Y-%m-%d')
    if not time_str:
        time_str = now.strftime('%H:%M:%S')

    filename = get_today_status_file_path(date_str)
    
    # 确保文件已存在，不存在则初始化
    if not os.path.exists(filename):
        init_today_status_file(date_str=date_str)

    status_badge = f"✅（{time_str}）" if success else f"❌（{time_str}）"

    try:
        with open(filename, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"⚠️ 读取状态文件失败: {e}")
        lines = []

    account_found = False
    updated_lines = []
    # 正则兼容: 兼容 2 段旧格式与 3 段新格式，容忍中英文冒号
    row_pattern = re.compile(
        rf"^({re.escape(account_name)})\s*-\s*签到[:：]\s*(.*?)\s*---\s*分享[:：]\s*(.*?)(?:\s*---\s*金币[:：]\s*(.*?))?\s*$"
    )

    for line in lines:
        stripped = line.strip()
        m = row_pattern.match(stripped)
        if m:
            account_found = True
            acc, signin_part, share_part, coins_part = m.groups()
            if not coins_part:
                coins_part = "⏳ 未获取"

            if operation_type == '签到':
                signin_part = status_badge
            elif operation_type == '分享':
                share_part = status_badge
            elif operation_type == '金币':
                coins_part = str(coins) if coins is not None else status_badge

            if coins is not None:
                coins_part = str(coins)

            updated_lines.append(f"{acc} - 签到: {signin_part} --- 分享: {share_part} --- 金币: {coins_part}\n")
        else:
            updated_lines.append(line)

    if not account_found:
        signin_part = status_badge if operation_type == '签到' else "⏳ 未执行"
        share_part = status_badge if operation_type == '分享' else "⏳ 未执行"
        coins_part = str(coins) if coins is not None else "⏳ 未获取"
        new_line = f"{account_name} - 签到: {signin_part} --- 分享: {share_part} --- 金币: {coins_part}\n"
        updated_lines.append(new_line)

    try:
        with open(filename, 'w', encoding='utf-8') as f:
            f.writelines(updated_lines)
    except Exception as e:
        print(f"⚠️ 写入状态文件失败: {e}")

    cleanup_shared_logs(30)


def get_today_account_coins(account_name, date_str=None):
    """
    从今日状态日志文件中读取该账号已记录的金币数量。
    返回 int (如果存在且为有效数字)，否则返回 None。
    """
    filename = get_today_status_file_path(date_str)
    if not os.path.exists(filename):
        return None
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            for line in f:
                line_str = line.strip()
                if line_str.startswith(f"{account_name} -") or line_str.startswith(f"{account_name} "):
                    m = re.search(r'金币[:：]\s*(\d+)', line_str)
                    if m:
                        return int(m.group(1))
    except Exception:
        pass
    return None


def natural_sort_key(s):
    """
    自然排序键函数，将字符串中的数字提取为整数排序，确保 yifan2 排在 yifan10 之前。
    """
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', str(s))]


def print_coins_summary_and_send_email(results, failed_accounts=None):
    """
    统一打印金币汇总结果并根据规则触发邮件通知。
    邮件发送规则：
    1. 有失败的账号时必须发送
    2. 如果全部成功，则只有周一才发送
    """
    if failed_accounts is None:
        failed_accounts = []

    # 如果有失败的账号，提示用户手动登录
    if failed_accounts:
        print("\n" + "=" * 50)
        print("⚠️  以下账号需要手动登录（登录状态已过期或操作失败）:")
        for account_name in failed_accounts:
            print(f"   - {account_name}")
        print("\n💡 请使用以下命令手动登录:")
        for account_name in failed_accounts:
            print(f"   python main.py login {account_name}")
        print("=" * 50)

    # 打印汇总结果（按自然序号 yifan1, yifan2, ... 严格排序）
    if results:
        print("\n📊 金币统计汇总:")
        total_coins = sum(results.values())
        for account_name in sorted(results.keys(), key=natural_sort_key):
            print(f"   ✅ {account_name}: {results[account_name]} 金币")
        print(f"   💰 总计: {total_coins} 金币")

    # 检查是否需要发送邮件:
    # 1. 有失败的账号时必须发送
    # 2. 如果全部成功，则只有周一才发送
    is_monday = datetime.now().weekday() == 0
    has_failures = len(failed_accounts) > 0
    should_send_email = has_failures or is_monday

    # 只有在符合发送条件且邮箱配置正确时才发送邮件
    if should_send_email and EMAIL_HOST and EMAIL_HOST != "smtp.xx.com":
        total_coins = sum(results.values()) if results else 0
        
        # 构建主题
        subject = "爱壹帆金币统计"
        if failed_accounts:
            subject += f"({len(failed_accounts)}个失败)"
        
        # 构建内容
        content = f"""{datetime.now().strftime('%Y-%m-%d')}
总金币: {total_coins}

账号统计:
"""
        # 添加所有账号（按自然序号严格排序）
        all_accounts = sorted(list(set(list(results.keys()) + failed_accounts)), key=natural_sort_key)
        for account in all_accounts:
            if account in results:
                content += f"✅ {account}:  {results[account]} 金币\n"
            else:
                content += f"❌ {account}:  需要手动登录\n"
        
        send_email(subject, content)
        print("\n✅ 已发送邮件通知")


def share_account_details(account_name, status):
    """兼容旧接口"""
    if "签到" in status:
        update_account_status(account_name, "签到", success=("成功" in status))
    elif "分享" in status:
        update_account_status(account_name, "分享", success=("成功" in status))


def check_today_status(account_name, operation_type, date_str=None):
    """
    检查账号在指定自然日（默认当天 00:00~23:59）内是否已成功完成指定操作（'签到' 或 '分享'）。
    严格自然日逻辑：以自然日为绝对边界，只查验当天的状态日志，绝不跨天向前回溯。
    
    Args:
        account_name: 账号名称
        operation_type: 操作类型 ('签到' 或 '分享')
        date_str: 可选自然日，格式 YYYY-MM-DD，默认为今天
    
    Returns:
        bool: True 如果当天已成功完成该操作（需要跳过），否则 False
    """
    data_dir = 'account_data'
    shared_dir = os.path.join(data_dir, 'shared')
    
    if not date_str:
        date_str = datetime.now().strftime('%Y-%m-%d')
    
    status_file = os.path.join(shared_dir, f"{date_str}_status.txt")
    if not os.path.exists(status_file):
        return False

    try:
        with open(status_file, 'r', encoding='utf-8') as f:
            for line in f:
                line_str = line.strip()
                # 1. 全新打卡表格格式（例如: "yifan1 - 签到: ✅（22:17:42） --- 分享: ..."）
                if line_str.startswith(f"{account_name} -") or line_str.startswith(f"{account_name} "):
                    pattern = rf"{operation_type}[:：]\s*✅"
                    if re.search(pattern, line_str):
                        return True

                # 2. 兼容旧版格式（例如: "HH:MM:SS - account_name: 签到成功"）
                if f"- {account_name}: {operation_type}成功" in line_str:
                    return True
    except Exception:
        return False

    return False


def force_click_sign_in_button(page):
    """兼容旧接口：定位并点击签到按钮"""
    btn = find_sign_in_button(page)
    if btn:
        btn.click(force=True)
        return True
    return False


def check_dialog(page, recursion_depth=0):
    """兼容旧接口：处理签到确认弹窗"""
    return handle_check_in_dialog(page)


def run_check_in_for_account(account_name, headless=False):
    """为指定账号执行签到操作"""
    print(f"\n===== 开始为账号 '{account_name}' 执行签到 =====")
    data_dir = 'account_data'
    
    # 检查今日是否已签到成功
    if check_today_status(account_name, '签到'):
        print(f"⏭️ 账号 '{account_name}' 今日已签到成功，跳过")
        return True  # 返回 True 表示无需再次操作
    
    # 检查账号状态文件是否存在
    state_file = os.path.join(data_dir, f"{account_name}_storage.json")
    if not os.path.exists(state_file):
        print(f"❌ 账号 '{account_name}' 的登录状态文件不存在")
        update_account_status(account_name, '签到', success=False)
        return False
    
    with sync_playwright() as p:
        try:
            # 启动浏览器，设定标准桌面视口避免无头模式下页面折叠
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(viewport={"width": 1920, "height": 1080})
            
            # 加载保存的状态
            try:
                if not load_storage_state(context, state_file):
                    browser.close()
                    update_account_status(account_name, '签到', success=False)
                    return False
                print(f"✅ 已加载账号 '{account_name}' 的登录状态")
            except Exception as e:
                print(f"❌ 加载账号状态失败: {short_error(e)}")
                browser.close()
                update_account_status(account_name, '签到', success=False)
                return False
            
            page = context.new_page()
            
            # 访问网站
            print("正在打开个人中心...")
            try:
                page.goto("https://www.yfsp.tv/user/index", wait_until="domcontentloaded", timeout=45000)
            except Exception as e:
                print(f"⚠️ 打开个人中心超时: {short_error(e)}，尝试继续")
            
            # 检查登录状态
            if not check_login_status(page):
                print("❌ 登录状态失效，请重新登录")
                browser.close()
                update_account_status(account_name, '签到', success=False)
                return False
            
            # 动态轮询等待签到模块异步数据挂载（最长等待 15 秒）
            print("正在等待个人中心签到状态加载...")
            status, result = wait_for_sign_in_section(page, timeout_sec=15)

            if status == 'already_signed':
                print(f"⏭️ 页面显示账号 '{account_name}' 今日此前已完成签到（{result}），无需重复点击")
                coins_current = get_current_coins(page)
                update_account_status(account_name, "签到", success=True, coins=coins_current)
                browser.close()
                return True

            if status == 'timeout':
                print("⚠️ 超时未检测到'立即签到'或'已签到'按钮")
                debug_info = page.evaluate('''() => {
                    const list = [];
                    document.querySelectorAll('*').forEach(el => {
                        if (el.children.length === 0 && el.innerText && el.innerText.includes('签到') && el.offsetWidth > 0) {
                            list.push({
                                tag: el.tagName,
                                class: el.className ? String(el.className) : '',
                                text: el.innerText.replace(/\\s+/g, ' ').trim().substring(0, 30)
                            });
                        }
                    });
                    return list.slice(0, 8);
                }''')
                if debug_info:
                    print(f"🔍 调试诊断：页面包含'签到'字样的元素: {debug_info}")
                browser.close()
                update_account_status(account_name, '签到', success=False)
                return False

            # status == 'ready_to_sign'
            sign_btn = result

            # 获取签到前的金币数
            coins_before = get_current_coins(page)
            if coins_before is not None:
                print(f"💰 签到前金币数量: {coins_before}")

            # 尝试签到（最多尝试 2 次）
            max_attempts = 2
            sign_in_success = False
            for attempt in range(1, max_attempts + 1):
                print(f"\n----- 签到尝试 {attempt}/{max_attempts} -----")
                if attempt > 1:
                    # 重试时重新定位按钮
                    sign_btn = find_sign_in_button(page)
                    if not sign_btn:
                        # 尝试2未找到按钮时，必须确凿核验金币增加才认可成功
                        if check_sign_in_success(page, coins_before):
                            print("✅ 签到成功（金币资产已确认增加）")
                            coins_after = get_current_coins(page)
                            update_account_status(account_name, "签到", success=True, coins=coins_after or coins_before)
                            sign_in_success = True
                            break
                        print("⚠️ 未找到高亮可点击的'立即签到'按钮")
                        time.sleep(2)
                        continue

                print("找到高亮可点击的'立即签到'按钮，正在执行点击...")
                try:
                    sign_btn.scroll_into_view_if_needed()
                    time.sleep(0.5)
                    sign_btn.click(timeout=5000)
                    print("✅ 已点击'立即签到'按钮")
                except Exception as e:
                    print(f"⚠️ 点击签到按钮异常: {short_error(e)}，尝试通过JS点击")
                    try:
                        page.evaluate("(el) => el.click()", sign_btn.element_handle())
                        print("✅ 已通过JS点击'立即签到'按钮")
                    except Exception as e2:
                        print(f"❌ 点击仍然失败: {short_error(e2)}")

                # 处理可能弹出的确认对话框（在小窗口中点击'即刻签到'）
                handle_check_in_dialog(page, timeout_sec=6)

                # 严格核验证签到结果（金币增加 / 按钮翻转为'已签到'）
                if check_sign_in_success(page, coins_before):
                    print("✅ 签到成功")
                    coins_after = get_current_coins(page)
                    update_account_status(account_name, "签到", success=True, coins=coins_after or coins_before)
                    sign_in_success = True
                    break
                else:
                    if attempt < max_attempts:
                        print(f"⚠️ 第 {attempt} 次尝试未确认成功，准备重试...")
                        time.sleep(3)

            browser.close()
            if not sign_in_success:
                update_account_status(account_name, '签到', success=False)
            return sign_in_success
            
        except Exception as e:
            print(f"❌ 执行签到时出错: {short_error(e)}")
            update_account_status(account_name, '签到', success=False)
            return False
        finally:
            if 'browser' in locals():
                try:
                    browser.close()
                except Exception:
                    pass



def run_share_video_for_account(account_name, headless=False):
    """为指定账号执行分享视频操作"""
    print(f"\n===== 开始为账号 '{account_name}' 执行分享视频 =====")
    data_dir = 'account_data'
    
    # 检查今日是否已分享成功
    if check_today_status(account_name, '分享'):
        print(f"⏭️ 账号 '{account_name}' 今日已分享成功，跳过")
        return True  # 返回 True 表示无需再次操作
    
    # 检查账号状态文件是否存在
    state_file = os.path.join(data_dir, f"{account_name}_storage.json")
    if not os.path.exists(state_file):
        print(f"❌ 账号 '{account_name}' 的登录状态文件不存在")
        update_account_status(account_name, "分享", success=False)
        return False
    
    session_expired = False

    def try_share_with_mode(use_headless):
        nonlocal session_expired
        with sync_playwright() as p:
            try:
                # 启动浏览器，统一配置 1080P 标准视口并授予剪贴板读写权限
                browser = p.chromium.launch(headless=use_headless)
                context = browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    permissions=["clipboard-read", "clipboard-write"]
                )
                page = context.new_page()
                
                # 加载保存的状态
                try:
                    if not load_storage_state(context, state_file):
                        browser.close()
                        return False
                    print(f"✅ 已加载账号 '{account_name}' 的登录状态")
                except Exception as e:
                    print(f"❌ 加载账号状态失败: {short_error(e)}")
                    browser.close()
                    return False
                
                # 严格服务端鉴权验证（直接访问个人中心并校验资产或用户信息）
                print("正在通过服务端验证账号登录态有效性...")
                if not verify_account_session(page, timeout_ms=30000):
                    print(f"❌ 账号 '{account_name}' 登录态在服务端已失效，终止执行分享")
                    session_expired = True
                    browser.close()
                    return False

                # 此时在个人中心，获取分享前金币数量
                coins_before = get_current_coins(page)
                if coins_before is not None:
                    print(f"💰 分享前金币数量: {coins_before}")
                
                print(f"✅ 账号 '{account_name}' 服务端鉴权有效，开始执行分享视频...")
                # 执行分享操作
                success = share_video(page)
                coins_after = None
                if success:
                    # 重新核验个人中心资产与任务状态变动
                    try:
                        page.goto("https://www.yfsp.tv/user/index", wait_until="domcontentloaded", timeout=25000)
                        time.sleep(2)
                        
                        # 检查个人中心分享任务按钮状态（如'待完成' / '已完成'）
                        task_items = page.evaluate('''() => {
                            const res = [];
                            document.querySelectorAll('*').forEach(el => {
                                if (el.children.length === 0 && el.innerText && el.offsetWidth > 0) {
                                    const t = el.innerText.trim();
                                    if (t === '待完成' || t === '已完成' || t === '去完成' || (t.includes('分享') && t.length < 20)) {
                                        res.push(t);
                                    }
                                }
                            });
                            return res;
                        }''')
                        if task_items:
                            print(f"📋 个人中心任务状态项: {task_items}")

                        coins_after = get_current_coins(page)
                        if coins_before is not None and coins_after is not None:
                            if coins_after > coins_before:
                                print(f"✅ 金币资产已真实增加：{coins_before} ➡️ {coins_after}（服务端入账铁证）")
                            elif coins_after == coins_before:
                                print(f"ℹ️ 个人中心当前金币数量: {coins_after}")
                    except Exception as e:
                        print(f"⚠️ 复核分享后个人中心跳过: {short_error(e)}")

                    print(f"✅ 账号 '{account_name}' 分享视频成功")
                    final_coins = coins_after if coins_after is not None else coins_before
                    # 记录分享成功状态与最新金币数
                    update_account_status(account_name, "分享", success=True, coins=final_coins)
                else:
                    print(f"❌ 账号 '{account_name}' 分享视频失败")
                    update_account_status(account_name, "分享", success=False, coins=coins_before)
                
                browser.close()
                return success
                
            except Exception as e:
                print(f"❌ 执行分享操作时出错: {short_error(e)}")
                if 'browser' in locals():
                    browser.close()
                return False
    
    # 尝试使用指定模式
    success = try_share_with_mode(headless)
    if not success and not headless and not session_expired:
        print("\n⚠️ 可见模式失败，尝试无头模式...")
        success = try_share_with_mode(True)
    
    if not success:
        update_account_status(account_name, "分享", success=False)
    
    return success

def run_for_single_account(account_name, headless=False):
    """为单个账号执行所有操作（先签到再分享视频）"""
    check_in_result = run_check_in_for_account(account_name, headless)
    share_result = run_share_video_for_account(account_name, headless)
    
    return check_in_result and share_result


def get_accounts_from_account_json(data_dir=None):
    """从 account_data/account.json 读取所有已保存凭据的账号列表（保持文件中的原有顺序）。"""
    if not data_dir:
        data_dir = 'account_data'
    db_path = os.path.join(data_dir, "account.json")
    if os.path.exists(db_path):
        try:
            with open(db_path, "r", encoding="utf-8") as f:
                db = json.load(f)
            if isinstance(db, dict):
                return list(db.keys())
        except Exception as e:
            logging.warning(f"读取 account.json 失败: {e}")
    return []


def load_account_credentials(account_name, data_dir):
    """从 account_data/account.json 读取指定账号的邮箱与密码；若无则回退到旧的 <name>_account.json。"""
    account_name = (account_name or "").strip()
    if not account_name:
        return None, None
    db_path = os.path.join(data_dir, "account.json")
    if os.path.exists(db_path):
        try:
            with open(db_path, "r", encoding="utf-8") as f:
                db = json.load(f)
            if isinstance(db, dict):
                entry = db.get(account_name)
                if isinstance(entry, dict):
                    email = (entry.get("email") or "").strip()
                    password = entry.get("password")
                    if password is not None:
                        password = str(password)
                        if email and password:
                            return email, password
        except Exception as e:
            logging.warning(f"读取 account.json 失败: {e}")
    legacy = os.path.join(data_dir, f"{account_name}_account.json")
    if not os.path.exists(legacy):
        return None, None
    try:
        with open(legacy, "r", encoding="utf-8") as f:
            data = json.load(f)
        email = (data.get("email") or "").strip()
        password = data.get("password")
        if password is None:
            return None, None
        password = str(password)
        if not email or not password:
            return None, None
        return email, password
    except Exception as e:
        logging.warning(f"读取旧版账号文件失败: {e}")
        return None, None


def _yfsp_try_open_login_modal(page) -> bool:
    """点击页面上未登录态的登录入口（右上头像+「登录」/ 立即登录 / 自定义选择器等）。"""
    custom = (os.environ.get("YFSP_LOGIN_ENTRY_CSS") or "").strip() or _load_yfsp_login_entry_css()
    if custom:
        try:
            loc = page.locator(custom).first
            loc.wait_for(state="visible", timeout=6000)
            loc.scroll_into_view_if_needed()
            loc.click(force=True, timeout=5000)
            print(f"✅ 已使用自定义登录入口 CSS: {custom}")
            return True
        except Exception as e:
            print(f"⚠️ 自定义 YFSP_LOGIN_ENTRY_CSS / yfsp_ui_config.json 未点到 ({e})，继续自动探测…")

    for sel in YFSP_BUILTIN_LOGIN_CSS_FALLBACKS:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            loc.wait_for(state="visible", timeout=5000)
            loc.scroll_into_view_if_needed()
            loc.click(force=True, timeout=5000)
            print("✅ 已点击内置顶栏「登录」选择器")
            return True
        except Exception:
            continue

    try:
        page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass
    time.sleep(0.35)

    strategy = page.evaluate(
        """() => {
        function visible(el) {
            if (!el) return '';
            const st = window.getComputedStyle(el);
            if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0)
                return '';
            const r = el.getBoundingClientRect();
            if (r.width < 2 || r.height < 2 || r.bottom < 0 || r.top > window.innerHeight + 400)
                return '';
            return 'ok';
        }
        function norm(s) {
            return (s || '').replace(/\\s+/g, ' ').trim();
        }
        // 0) 纯文本节点「登录」且在视口右上（头像右侧两字常见为独立文本节点）
        const tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        let tn;
        while (tn = tw.nextNode()) {
            if (norm(tn.nodeValue) !== '登录') continue;
            let el = tn.parentElement;
            if (!el || !visible(el)) continue;
            const r = el.getBoundingClientRect();
            if (r.left < window.innerWidth * 0.48) continue;
            if (r.top > 220) continue;
            let target = el;
            if (window.getComputedStyle(el).pointerEvents === 'none') {
                let q = el.parentElement;
                for (let k = 0; k < 6 && q; k++) {
                    if (window.getComputedStyle(q).pointerEvents !== 'none') {
                        target = q;
                        break;
                    }
                    q = q.parentElement;
                }
            }
            target.click();
            return 'textnode-login-topright';
        }
        // 0b) header 内靠右小图与同容器内精确「登录」子节点
        const hdrs = document.querySelectorAll('header, [class*="header"], [class*="Header"], [class*="top-bar"], [class*="TopBar"]');
        const roots = hdrs.length ? Array.from(hdrs) : [document.body];
        for (const root of roots) {
            for (const img of root.querySelectorAll('img')) {
                const r = img.getBoundingClientRect();
                if (r.right < window.innerWidth * 0.50 || r.top > 220 || r.width < 6 || r.height < 6)
                    continue;
                const wrap = img.closest('a, button, [role="button"], div, span, li') || img.parentElement;
                if (!wrap) continue;
                for (const leaf of wrap.querySelectorAll('span, a, em, i, b, strong, p, div, label')) {
                    if (leaf.contains && leaf.contains(img)) continue;
                    if (norm(leaf.textContent) !== '登录') continue;
                    if (!visible(leaf)) continue;
                    leaf.click();
                    return 'header-row-login-label';
                }
                let sib = img.nextElementSibling;
                for (let j = 0; j < 5 && sib; j++) {
                    for (const leaf of sib.querySelectorAll ? sib.querySelectorAll('span, a, em, i') : []) {
                        if (norm(leaf.textContent) === '登录' && visible(leaf)) {
                            leaf.click();
                            return 'img-next-sibling-deep';
                        }
                    }
                    if (norm(sib.textContent) === '登录' && visible(sib)) {
                        sib.click();
                        return 'img-next-sibling';
                    }
                    sib = sib.nextElementSibling;
                }
            }
        }
        // 1) 默认头像：取最靠右的候选，避免点到页内其它小图
        const imgSelectors = [
            'img[src*="logon"]',
            'img[src*="logon.png"]',
            'img[alt*="头像"]',
            '.user-icon img',
            'header img[src*="user"]'
        ];
        let bestImg = null;
        let bestRight = -1;
        for (const sel of imgSelectors) {
            for (const img of document.querySelectorAll(sel)) {
                if (!visible(img)) continue;
                const r = img.getBoundingClientRect();
                if (r.right < window.innerWidth * 0.45) continue;
                if (r.top > 240) continue;
                if (r.right > bestRight) {
                    bestRight = r.right;
                    bestImg = img;
                }
            }
        }
        if (bestImg) {
            let p = bestImg.parentElement;
            for (let i = 0; i < 14 && p; i++) {
                const tag = p.tagName;
                const cls = (p.className || '').toString();
                if (tag === 'A' || tag === 'BUTTON' || p.getAttribute('role') === 'button') {
                    p.click();
                    return 'header-cta';
                }
                if (/user|login|avatar|logon|account|member|nav|bar/i.test(cls)) {
                    p.click();
                    return 'class-wrapper';
                }
                p = p.parentElement;
            }
            bestImg.click();
            return 'img-direct';
        }
        // 2) 精确文案
        const tags = 'a, button, span, div, p, li, em, strong, label';
        const prefer = ['立即登录', '登录'];
        for (const kw of prefer) {
            const nodes = document.querySelectorAll(tags);
            for (const el of nodes) {
                const t = norm(el.textContent);
                if (t !== kw) continue;
                if (!visible(el)) continue;
                if (kw === '登录' && el.getBoundingClientRect().left < window.innerWidth * 0.45)
                    continue;
                el.click();
                return kw;
            }
        }
        return '';
    }"""
    )
    if strategy:
        print(f"✅ 已尝试点击登录入口（页面内脚本: {strategy}）")
        return True

    xpath_candidates = [
        'xpath=//header//img/following-sibling::*//*[normalize-space()="登录"]',
        'xpath=//header//*[normalize-space()="登录"]',
        'xpath=//*[contains(@class,"header") or contains(@class,"Header")]//*[normalize-space()="登录"]',
    ]
    for xp in xpath_candidates:
        try:
            loc = page.locator(xp).first
            if loc.count() == 0:
                continue
            loc.wait_for(state="visible", timeout=2000)
            loc.scroll_into_view_if_needed()
            loc.click(force=True, timeout=4000)
            disp = xp if len(xp) <= 80 else xp[:78] + "…"
            print(f"✅ 已通过 XPath 点击登录入口: {disp}")
            return True
        except Exception:
            continue

    playwright_targets = [
        page.locator("header").get_by_text("登录", exact=True),
        page.locator('[class*="header"]').first.get_by_text("登录", exact=True),
        page.get_by_role("button", name="立即登录"),
        page.get_by_role("link", name="立即登录"),
        page.get_by_role("button", name="登录"),
        page.get_by_role("link", name="登录"),
        page.get_by_text("立即登录", exact=True),
        page.get_by_text("登录", exact=True),
    ]
    for loc in playwright_targets:
        try:
            tgt = loc.first
            if tgt.count() == 0:
                continue
            tgt.wait_for(state="visible", timeout=2000)
            tgt.scroll_into_view_if_needed()
            tgt.click(force=True, timeout=5000)
            print("✅ 已通过 Playwright 定位点击登录入口")
            return True
        except Exception:
            continue

    css_list = [
        'header a:has-text("立即登录")',
        'header button:has-text("立即登录")',
        'a:has-text("立即登录")',
        'button:has-text("立即登录")',
        'header a:has-text("登录")',
        'header span:has-text("登录")',
        'a:has-text("登录")',
        'button:has-text("登录")',
        'span:has-text("登录")',
        'div.user-entry:visible',
        '[class*="login"]:visible',
    ]
    for sel in css_list:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            loc.wait_for(state="visible", timeout=2000)
            loc.scroll_into_view_if_needed()
            loc.click(force=True, timeout=5000)
            print(f"✅ 已点击 CSS 选择器: {sel}")
            return True
        except Exception:
            continue
    return False


def _yfsp_wait_login_iframe(page, total_timeout_ms=25000):
    """等待登录 iframe 出现在 DOM（弹层可能先 attached 再显示）。"""
    deadline = time.time() + total_timeout_ms / 1000.0
    combined = "iframe#Dn_Login_Iframe, iframe[src*='login'], iframe[src*='Login'], iframe[id*='Login'], iframe[id*='login']"
    last_err = None
    while time.time() < deadline:
        try:
            page.wait_for_selector(combined, timeout=3000, state="attached")
            handle = page.query_selector(combined)
            if handle:
                return handle
        except Exception as e:
            last_err = e
        time.sleep(0.35)
    if last_err:
        logging.warning(f"等待登录 iframe: {last_err}")
    return None


def open_yfsp_login_iframe_and_fill(page, email, password, start_url="https://www.yfsp.tv/list/anime"):
    """打开站点登录框、切换到「其他方式登录」、填写邮箱与密码。不执行滑动验证、不点击提交。"""
    page.goto(start_url, timeout=60000, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("load", timeout=25000)
    except Exception:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    time.sleep(1.0)

    iframe_el = None
    for attempt in range(1, 5):
        iframe_el = _yfsp_wait_login_iframe(page, total_timeout_ms=1200)
        if iframe_el:
            break
        clicked = _yfsp_try_open_login_modal(page)
        if clicked:
            time.sleep(0.75)
        iframe_el = _yfsp_wait_login_iframe(page, total_timeout_ms=8000)
        if iframe_el:
            break
        print(f"⚠️ 第 {attempt} 次未检测到登录 iframe，重试打开…")
        time.sleep(0.6)

    if not iframe_el:
        raise RuntimeError(
            "未检测到登录弹层（iframe）。请先在浏览器里手动点一次右上角「登录」确认能弹出；"
            "若仍失败，可在 account_data/yfsp_ui_config.json 写入 "
            '{"login_entry_css": "从开发者工具复制的选择器"}，'
            "或设置环境变量 YFSP_LOGIN_ENTRY_CSS 为同一 CSS 选择器。"
        )

    print("等待登录 iframe …")
    login_frame = iframe_el.content_frame()
    if not login_frame:
        raise RuntimeError("❌ 拿不到登录的 frame 对象")

    tab_li = login_frame.locator('ul.tabs li#mlogin')
    tab_li.wait_for(state="visible", timeout=10000)
    tab_li.scroll_into_view_if_needed()

    for attempt in range(1, 4):
        print(f'尝试点击"其他方式登录" 第 {attempt} 次...')
        tab_li.click(force=True)
        try:
            login_frame.wait_for_function(
                """() => {
                    const li = document.querySelector('ul.tabs li#mlogin');
                    return li && li.classList.contains('is-active');
                }""",
                timeout=3000,
            )
            print('✅ 已成功切换到 "其他方式登录"')
            break
        except PlaywrightTimeoutError:
            print("⚠️ 本次点击未生效，1s 后重试…")
            time.sleep(1)
    else:
        raise RuntimeError("❌ 重试 3 次仍未切换到「其他方式登录」")

    print("⏳ 正在填写账号密码…")
    login_frame.fill('input[name="Email"]', email)
    login_frame.fill('input[name="UserPass"]', password)

    # 等待滑块插件初始化并把登录按钮置为 disabled 锁定暗色状态（防止在滑块加载前误触登录）
    print("⏳ 等待滑块验证组件加载就绪…")
    try:
        login_frame.wait_for_selector('button.btn-login.disabled', timeout=6000, state="attached")
        print("✅ 登录按钮已处于未验证锁定状态")
    except Exception:
        pass
    return login_frame


def _find_active_login_frame(page, preferred_frame=None):
    """查找当前页面上处于活跃状态的登录 iframe。"""
    if preferred_frame:
        try:
            if not preferred_frame.is_detached():
                return preferred_frame
        except Exception:
            pass

    # 1. 尝试直接从 page.frames 中通过关键特征查找
    try:
        for f in page.frames:
            try:
                if f.is_detached():
                    continue
                test = f.evaluate("""() => {
                    return !!(
                        document.querySelector('.btn-login') ||
                        document.querySelector('#mlogin') ||
                        document.querySelector('input[name="Email"]') ||
                        document.querySelector('.yzm_content') ||
                        document.querySelector('.bar1')
                    );
                }""")
                if test:
                    return f
            except Exception:
                continue
    except Exception:
        pass

    # 2. 尝试通过 iframe 选择器查找
    try:
        combined = "iframe#Dn_Login_Iframe, iframe[src*='login'], iframe[src*='Login'], iframe[id*='Login'], iframe[id*='login']"
        handle = page.query_selector(combined)
        if handle:
            cf = handle.content_frame()
            if cf and not cf.is_detached():
                return cf
    except Exception:
        pass

    return None


def _check_and_click_login_if_verified(login_frame):
    """
    检查登录 iframe 内是否已完成滑动验证。
    严密判定逻辑：
    必须同时满足以下条件才触发点击：
    1. 滑块手柄与轨道必须已加载渲染（排除滑块加载前按钮尚未变暗锁定的阶段）
    2. 登录按钮已解除 disabled 状态（明亮可点击）
    3. 确凿的验证成功凭证：
       - 滑块轨道文本明确出现“验证成功”字样（slideToUnlock 的 serverValidateText）
       - 或者（隐藏验证码 AuthCode 获得后端回执 token 且 滑块手柄/进度条已被拖拽至右侧）
    """
    if not login_frame:
        return False

    try:
        if login_frame.is_detached():
            return False

        # 在 iframe 执行探测和提交
        js_res = login_frame.evaluate("""() => {
            // 1. 检查滑块手柄与轨道、登录按钮是否都在 DOM 中
            const handle = document.querySelector('.slide-to-unlock-handle');
            const yzmContainer = document.querySelector('.yzm_content, .bar1, .bar, .slide-to-unlock-bg');
            const loginBtn = document.querySelector('button.btn-login, button[type="submit"]');

            if (!handle || !yzmContainer || !loginBtn) {
                // 滑块组件尚未加载完毕，坚决不点击
                return { verified: false, clicked: false, reason: '滑块组件尚未就绪' };
            }

            const containerText = yzmContainer.textContent ? yzmContainer.textContent.trim() : '';

            // 2. 核心凭据：轨道文本明确包含“验证成功”
            const hasSuccessText = containerText.includes('验证成功');

            // 3. 辅助凭据：隐藏验证码字段 (input#AuthCode, input.yzm) 是否已经获得有效 token
            const authCodeInput = document.querySelector('input#AuthCode, input.yzm');
            const authCodeVal = (authCodeInput && authCodeInput.value) ? authCodeInput.value.trim() : '';
            const hasAuthCode = authCodeVal.length > 5;

            // 4. 辅助状态：滑块手柄或进度条是否已经拖动到了右端（排除初始状态）
            const progress = document.querySelector('.slide-to-unlock-progress');
            const progressWidth = progress ? (parseFloat(progress.style.width || progress.offsetWidth || '0')) : 0;
            const handleLeft = handle.style.left ? parseFloat(handle.style.left) : (handle.offsetLeft || 0);
            const sliderMovedToRight = (progressWidth > 150) || (handleLeft > 150);

            // 5. 按钮状态：必须已经从暗色（disabled）变亮为可点击状态
            const hasDisabledClass = loginBtn.classList.contains('disabled');
            const hasDisabledAttr = loginBtn.disabled || loginBtn.hasAttribute('disabled');
            const isBtnClickable = (!hasDisabledClass && !hasDisabledAttr);

            // 严格联合判定：
            // 必须：按钮可点击（已去除 disabled）
            // 并且必须：轨道显示“验证成功” 或 (验证码已注入 且 滑块已拖到右边)
            const isSliderVerified = hasSuccessText || (hasAuthCode && sliderMovedToRight);
            const isVerified = isBtnClickable && isSliderVerified;

            if (isVerified) {
                // 同步表单字段
                if (typeof window.beforeSubmit === 'function') {
                    try {
                        window.beforeSubmit();
                    } catch (e) {}
                }

                loginBtn.click();

                return {
                    verified: true,
                    clicked: true,
                    reason: hasSuccessText ? '滑块轨道显示「验证成功」且登录按钮已亮起' : '验证凭据已注入且登录按钮已亮起'
                };
            }

            return {
                verified: false,
                clicked: false,
                reason: ''
            };
        }""")

        if js_res and js_res.get('clicked'):
            reason = js_res.get('reason', '滑动验证成功')
            print(f"\n🎉 识别到{reason}！已自动点击「登录」按钮，正在等待登录完成...")

            # 双重保险：在 Playwright 端再尝试触发一次（若元素仍存在）
            try:
                time.sleep(0.2)
                btn = login_frame.locator('button.btn-login:not(.disabled), button[type="submit"]:has-text("登 录")').first
                if btn.count() > 0 and btn.is_visible():
                    btn.click(timeout=1000)
            except Exception:
                pass

            return True

    except Exception:
        # iframe 跳转、重载或瞬间脱离 DOM 时可能产生异常，静默忽略
        pass

    return False


def wait_for_login_success(page, login_frame=None, timeout_seconds=180, required_successes=2, auto_click_login=True):
    """
    等待用户完成网页登录：
    1. 在等待过程中持续监控滑块验证状态；一旦检测到验证成功，立即自动点击「登录」按钮。
    2. 持续检测页面登录态，连续成功后返回 True。
    """
    deadline = time.time() + timeout_seconds
    success_count = 0
    login_button_clicked = False
    click_timestamp = 0.0
    last_status_check = 0.0
    active_frame = login_frame

    print(f"\n⏳ 正在等待登录完成，最多等待 {timeout_seconds} 秒...")
    print("   💡 提示：在弹窗中手动拖完滑块后，脚本将自动代点「登录」按钮")

    while time.time() < deadline:
        remaining = max(0, int(deadline - time.time()))

        # 检查页面是否仍然活着
        try:
            _ = page.url
        except Exception:
            print("\n❌ 浏览器或页面已关闭，停止等待登录")
            return False

        # 1. 自动识别滑块完成并点击登录按钮
        if auto_click_login:
            # 如果之前点击过，但如果已过去 4 秒且 iframe 按钮变回 disabled（表示上一轮验证可能报错），重置允许再次触发
            if login_button_clicked and (time.time() - click_timestamp > 4.0):
                try:
                    if active_frame and not active_frame.is_detached():
                        is_disabled_again = active_frame.evaluate("""() => {
                            const btn = document.querySelector('button.btn-login, button[type="submit"]');
                            return btn && btn.classList.contains('disabled');
                        }""")
                        if is_disabled_again:
                            login_button_clicked = False
                            play_prompt_sound("Glass")
                            print("\n🔔 [提示音] 滑块已重置，请重新拖动滑块验证！")
                except Exception:
                    pass

            if not login_button_clicked:
                if not active_frame or active_frame.is_detached():
                    active_frame = _find_active_login_frame(page, login_frame)

                if active_frame:
                    if _check_and_click_login_if_verified(active_frame):
                        login_button_clicked = True
                        click_timestamp = time.time()
                        time.sleep(0.8)  # 给提交请求一点处理时间

        # 2. 检查主页面登录态
        # 如果已经点击过登录按钮，每次循环都探测；如果尚未点击，每 2 秒探测一次
        check_now = login_button_clicked or (time.time() - last_status_check >= 2.0)
        if check_now:
            last_status_check = time.time()
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    logged_in = check_login_status(page)

                if logged_in:
                    success_count += 1
                    if success_count >= required_successes:
                        print()
                        print("✅ 已检测到登录成功")
                        return True
                else:
                    success_count = 0
            except Exception as e:
                success_count = 0
                msg = str(e).lower()
                if "has been closed" in msg or ("closed" in msg and "target" in msg):
                    print()
                    print("❌ 浏览器或页面已关闭，停止等待登录")
                    return False

        print(f"\r⏳ 等待登录中... 剩余 {remaining} 秒", end="", flush=True)
        time.sleep(0.35)

    print()
    print("❌ 等待登录超时，未检测到登录成功")
    return False


def manual_login(account_name, timeout_seconds=180, manual_save=False, batch_mode=False):
    """手动登录：打开可见浏览器。若 account.json 中有该账号凭据则自动填邮箱密码；否则仍会尝试点开顶栏「登录」。"""
    data_dir = 'account_data'
    os.makedirs(data_dir, exist_ok=True)
    
    if not account_name or not account_name.strip():
        print("❌ 账号名称不能为空")
        return False

    account_name = account_name.strip()
    login_email, login_password = load_account_credentials(account_name, data_dir)
    
    print(f"\n🔐 开始手动登录账号: {account_name}")
    print("=" * 50)
    print("⚠️  注意事项:")
    if login_email and login_password:
        print(f"   1. 已从 account_data/account.json 读取该账号邮箱与密码，将自动点击「登录」并填写")
        print("   2. 请在登录弹窗内手动拖动滑块完成人机验证，然后点击弹窗内的「登录」提交")
        print("   3. 登录成功后脚本会自动保存登录状态并关闭浏览器")
    else:
        print("   1. 未在 account_data/account.json 中找到该账号凭据：仍会尝试自动点击顶栏「登录」")
        print("   2. 需要自动填邮箱密码时，请执行 add 写入 account.json，或手工编辑该文件")
        print("   3. 登录成功后脚本会自动保存登录状态并关闭浏览器")
    print("=" * 50)
    
    browser = None
    try:
        with sync_playwright() as p:
            print("\n正在启动浏览器...")
            
            # 使用系统 Chrome 浏览器（而非 Playwright 自带的 Chromium）
            # 这样可以避免被网站检测为自动化工具
            try:
                browser = p.chromium.launch(
                    headless=False,
                    channel="chrome",  # 使用系统 Chrome
                    args=[
                        '--disable-blink-features=AutomationControlled',
                    ]
                )
                print("✅ 已启动系统 Chrome 浏览器")
            except Exception as chrome_error:
                print(f"⚠️ 无法启动系统 Chrome: {str(chrome_error)}")
                print("   正在尝试使用 Chromium...")
                browser = p.chromium.launch(
                    headless=False,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--no-sandbox',
                        '--disable-dev-shm-usage'
                    ]
                )
            
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                # 不设置 user_agent，使用浏览器默认值
            )
            page = context.new_page()
            login_frame = None

            # 注册对话框监听器，防止网页 alert / confirm 阻塞流程
            def _handle_page_dialog(dialog):
                print(f"\n⚠️ 网页弹窗提示: {dialog.message}")
                try:
                    dialog.accept()
                except Exception:
                    pass
            page.on("dialog", _handle_page_dialog)

            if login_email and login_password:
                print("\n正在打开站点并自动填写登录表单…")
                try:
                    login_frame = open_yfsp_login_iframe_and_fill(page, login_email, login_password)
                    print("✅ 已在登录弹窗中填写邮箱与密码")
                except Exception as e:
                    print(f"⚠️ 自动打开登录或填写失败: {e}")
                    print("   请你在浏览器中自行完成：打开 https://www.yfsp.tv 并点击「登录」…")
                    try:
                        page.goto("https://www.yfsp.tv", timeout=60000, wait_until="commit")
                        page.wait_for_load_state("domcontentloaded", timeout=15000)
                    except Exception:
                        pass
            else:
                print("正在打开页面并尝试自动点击顶栏「登录」…")
                try:
                    page.goto(
                        "https://www.yfsp.tv/list/anime",
                        timeout=60000,
                        wait_until="domcontentloaded",
                    )
                    print("✅ 页面已开始加载")
                except Exception as nav_error:
                    print(f"⚠️ 打开 list/anime 失败: {nav_error}，改试首页…")
                    try:
                        page.goto(
                            "https://www.yfsp.tv",
                            timeout=60000,
                            wait_until="domcontentloaded",
                        )
                    except Exception:
                        pass
                try:
                    page.wait_for_load_state("load", timeout=20000)
                except Exception:
                    pass
                time.sleep(1.2)
                any_click = False
                for attempt in range(1, 4):
                    if _yfsp_try_open_login_modal(page):
                        any_click = True
                        print(f"✅ 第 {attempt} 次：已尝试点击「登录」入口")
                    time.sleep(0.55)
                    if _yfsp_wait_login_iframe(page, total_timeout_ms=4500):
                        print("✅ 已检测到登录弹层，可在其中输入账号并完成验证")
                        login_frame = _find_active_login_frame(page)
                        break
                if not any_click:
                    print("⚠️ 自动点击未触发，请手动点击页面右上角「登录」")
            
            # 设置页面缩放为 90%，确保所有元素（如头像）可见
            try:
                page.evaluate("document.body.style.zoom = '0.9'")
                print("✅ 已设置页面缩放为 90%")
            except Exception:
                pass
            
            print("\n" + "=" * 50)
            play_prompt_sound("Glass")
            print("🔔 [提示音] 滑块已就绪，请在登录弹窗中手动拖动滑块完成验证！")
            if login_email and login_password:
                print("🌐 接下来请你:")
                print("   · 在登录弹窗内手动拖动滑块完成人机验证")
                print("   · ⚡ 验证成功后，脚本将自动识别并为你点击「登录」按钮！")
                print("   · 登录成功后脚本会自动检测并保存登录态")
            else:
                print("🌐 若已弹出登录框，请在窗口内手动输入邮箱与密码并拖动滑块验证；否则请手动点右上角「登录」")
                print(
                    "   自动填表依赖 account_data/account.json 中该账号条目（email、password），"
                    "不是从浏览器或系统里「提取」密码。"
                )
                print(
                    "   可先执行: python main.py add <邮箱> <密码> 写入 account.json，"
                    "再重新 login 即可自动填写。"
                )
                print("   · ⚡ 验证成功后，脚本将自动识别并为你点击「登录」按钮！")
                print("   · 登录成功后脚本会自动检测并保存登录态。")
            print("=" * 50)
            print("💡 自动保存完成前请勿关闭浏览器窗口。")

            if manual_save:
                input("\n✅ 登录成功后，请按回车键保存登录状态...")
                is_logged_in = check_login_status(page)
            else:
                is_logged_in = wait_for_login_success(page, login_frame=login_frame, timeout_seconds=timeout_seconds)
            
            # 检查页面是否还存在
            try:
                _ = page.url  # 测试页面是否还活着
            except Exception:
                print("❌ 浏览器已关闭，无法保存状态")
                return False

            try:
                if not browser.is_connected():
                    print(
                        "❌ 浏览器已关闭，无法检测或保存登录状态。"
                        "请重新执行 login，完成登录后在终端按回车前保持浏览器窗口打开。"
                    )
                    return False
            except Exception:
                pass

            if is_logged_in:
                # 保存账号到列表
                accounts_file = os.path.join(data_dir, "accounts.txt")
                accounts = []
                if os.path.exists(accounts_file):
                    with open(accounts_file, 'r', encoding='utf-8') as f:
                        accounts = [line.strip() for line in f if line.strip()]
                
                if account_name not in accounts:
                    accounts.append(account_name)
                    with open(accounts_file, 'w', encoding='utf-8') as f:
                        for acc in accounts:
                            f.write(f"{acc}\n")
                    print(f"✅ 已添加账号到列表: {account_name}")
                
                # 保存登录状态
                state_file = os.path.join(data_dir, f"{account_name}_storage.json")
                save_storage_state(context, state_file)
                print(f"✅ 已保存登录状态到 {state_file}")
                
                # 获取并显示用户名
                try:
                    username = get_username(page)
                    print(f"✅ 登录用户: {username}")
                except:
                    print("✅ 登录状态已保存")
                
                browser.close()
                if not batch_mode:
                    print("\n🎉 手动登录完成！现在可以使用以下命令执行自动操作:")
                    print("   python main.py run        # 签到+分享")
                    print("   python main.py checkin    # 仅签到")
                    print("   python main.py share      # 仅分享")
                return True
            else:
                print("❌ 未检测到登录成功，请确保已正确登录")
                if not batch_mode:
                    print("   如需重试，请重新执行: python main.py login <账号名>")
                browser.close()
                return False
                
    except Exception as e:
        print(f"❌ 手动登录过程中出错: {str(e)}")
        traceback.print_exc()
        if browser:
            try:
                browser.close()
            except:
                pass
        return False


def add_account(email, password=None, headless=False):
    """仅将账号邮箱与密码写入 account_data/account.json（合并更新），账号名取邮箱 @ 前缀。

    headless 参数已废弃，仅为兼容旧调用保留。
    """
    _ = headless
    data_dir = "account_data"
    os.makedirs(data_dir, exist_ok=True)

    if not email or not password:
        print("❌ 邮箱和密码不能为空")
        return False

    email = email.strip()
    if "@" not in email or email.startswith("@"):
        print("❌ 邮箱格式不正确，无法从 @ 前缀生成账号名")
        return False

    account_name = email.split("@", 1)[0].strip()
    if not account_name:
        print("❌ 邮箱 @ 前缀为空，无法生成账号名")
        return False

    password = str(password)

    path = os.path.join(data_dir, "account.json")
    db = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                db = raw
            else:
                print("⚠️ 现有 account.json 格式异常（非对象），将按空对象重新合并写入")
        except Exception as e:
            print(f"⚠️ 读取现有 account.json 失败，将重新写入: {e}")

    db[account_name] = {"email": email, "password": password}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 已将邮箱「{email}」保存为账号「{account_name}」，并写入 {path}")
    print(f"   请执行: python main.py login {account_name} 在浏览器中完成登录（凭据从 account.json 读取）。")
    print("   login 成功后会保存登录态并可将账号加入 accounts.txt，之后 list/run 才能包含该账号。")
    return True

def slide_verify(login_frame):
    # 1. 定位滑块手柄和轨道
    handle = login_frame.wait_for_selector('div.slide-to-unlock-handle', timeout=5000)
    bar    = login_frame.wait_for_selector('div.bar1.bar',                timeout=5000)

    # 2. 确保它们都在视口
    handle.scroll_into_view_if_needed()
    bar.scroll_into_view_if_needed()
    time.sleep(0.5)  # 等样式 settle

    # 3. 获取滑块和轨道的边界框
    handle_box = handle.bounding_box()
    bar_box = bar.bounding_box()
    
    # 4. 计算起点和终点
    start_x = handle_box['x'] + handle_box['width'] / 2
    start_y = handle_box['y'] + handle_box['height'] / 2
    target_x = bar_box['x'] + bar_box['width'] - handle_box['width']  # 留出滑块宽度
    target_y = bar_box['y'] + bar_box['height'] / 2
    
    # 5. 执行拖拽
    page = login_frame.page
    
    # 移动到起点
    page.mouse.move(start_x, start_y)
    time.sleep(random.uniform(0.5, 0.8))  # 更长的反应时间
    
    # 在按下前可能有轻微移动（模拟瞄准）
    for _ in range(2):
        page.mouse.move(
            start_x + random.uniform(-2, 2),
            start_y + random.uniform(-2, 2)
        )
        time.sleep(random.uniform(0.1, 0.2))
    
    # 按下鼠标
    page.mouse.down()
    time.sleep(random.uniform(0.3, 0.5))  # 按下后的停顿时间
    
    # 生成移动轨迹
    distance = target_x - start_x
    current_x = start_x
    current_y = start_y
    
    # 分多个阶段移动，每个阶段都有不同的特点
    segments = [
        {"portion": 0.1, "steps": 8, "speed": (0.03, 0.05)},   # 起步阶段：非常慢
        {"portion": 0.2, "steps": 10, "speed": (0.02, 0.04)},  # 加速阶段：稍快
        {"portion": 0.4, "steps": 15, "speed": (0.01, 0.03)},  # 匀速阶段：中速
        {"portion": 0.2, "steps": 12, "speed": (0.02, 0.04)},  # 减速阶段：稍慢
        {"portion": 0.1, "steps": 10, "speed": (0.03, 0.05)}   # 微调阶段：非常慢
    ]
    
    # 在某些点可能会有短暂停顿
    pause_points = random.sample(range(sum(s["steps"] for s in segments)), 3)
    
    step_count = 0
    for segment in segments:
        seg_distance = distance * segment["portion"]
        for i in range(segment["steps"]):
            progress = i / segment["steps"]
            # 使用平滑的缓动函数
            if progress < 0.5:
                ease = 2 * progress ** 2
            else:
                ease = 1 - 2 * (1 - progress) ** 2
                
            move_x = current_x + seg_distance * ease
            # 垂直方向的抖动随进度变化
            shake_range = 1.0 * (1 - abs(2*progress - 1))
            move_y = start_y + random.uniform(-shake_range, shake_range)
            
            page.mouse.move(move_x, move_y)
            
            # 如果是暂停点，添加一个明显的停顿
            if step_count in pause_points:
                time.sleep(random.uniform(0.2, 0.3))
            else:
                time.sleep(random.uniform(*segment["speed"]))
                
            current_x = move_x
            step_count += 1
            
        # 每个阶段结束后可能有短暂停顿
        if random.random() < 0.3:
            time.sleep(random.uniform(0.1, 0.2))
    
    # 在终点附近来回微调
    for _ in range(3):
        adjust_x = random.uniform(-3, 3)
        adjust_y = random.uniform(-1, 1)
        page.mouse.move(
            current_x + adjust_x,
            current_y + adjust_y
        )
        time.sleep(random.uniform(0.1, 0.15))
    
    # 最后确保在正确位置
    page.mouse.move(target_x, target_y)
    time.sleep(random.uniform(0.2, 0.3))
    
    # 释放鼠标
    page.mouse.up()
    time.sleep(0.8)  # 等待验证结果

    # 6. 检查验证结果
    try:
        login_frame.wait_for_selector('.verify-success', timeout=5000)
        print("✅ 滑动验证通过")
        return True
    except PlaywrightTimeoutError:
        print("❌ 滑动验证未通过")
        return False
    

def get_all_accounts():
    """获取所有已保存的账号列表"""
    data_dir = 'account_data'
    accounts_file = os.path.join(data_dir, "accounts.txt")
    
    if not os.path.exists(accounts_file):
        return []
        
    with open(accounts_file, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip()]


def get_ignored_accounts():
    """获取批量任务忽略账号列表。"""
    data_dir = 'account_data'
    ignored_file = os.path.join(data_dir, "ignored_accounts.txt")

    if not os.path.exists(ignored_file):
        return []

    with open(ignored_file, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip()]


def save_ignored_accounts(accounts):
    """保存批量任务忽略账号列表。"""
    data_dir = 'account_data'
    os.makedirs(data_dir, exist_ok=True)
    ignored_file = os.path.join(data_dir, "ignored_accounts.txt")

    unique_accounts = []
    for account in accounts:
        account = (account or "").strip()
        if account and account not in unique_accounts:
            unique_accounts.append(account)

    with open(ignored_file, 'w', encoding='utf-8') as f:
        for account in unique_accounts:
            f.write(f"{account}\n")


def ignore_account(account_name):
    """将账号加入批量任务忽略名单。"""
    account_name = (account_name or "").strip()
    if not account_name:
        print("❌ 请提供要忽略的账号名称")
        return False

    accounts = get_all_accounts()
    if accounts and account_name not in accounts:
        print(f"⚠️ 账号 '{account_name}' 不在 accounts.txt 中，仍会加入忽略名单")

    ignored_accounts = get_ignored_accounts()
    if account_name in ignored_accounts:
        print(f"✅ 账号 '{account_name}' 已在忽略名单中")
        return True

    ignored_accounts.append(account_name)
    save_ignored_accounts(ignored_accounts)
    print(f"✅ 已忽略账号 '{account_name}'，批量 run/checkin/share/coins 将跳过它")
    return True


def unignore_account(account_name):
    """将账号移出批量任务忽略名单。"""
    account_name = (account_name or "").strip()
    if not account_name:
        print("❌ 请提供要取消忽略的账号名称")
        return False

    ignored_accounts = get_ignored_accounts()
    if account_name not in ignored_accounts:
        print(f"✅ 账号 '{account_name}' 不在忽略名单中")
        return True

    ignored_accounts.remove(account_name)
    save_ignored_accounts(ignored_accounts)
    print(f"✅ 已取消忽略账号 '{account_name}'")
    return True


def list_ignored_accounts():
    """列出批量任务忽略账号。"""
    ignored_accounts = get_ignored_accounts()
    if not ignored_accounts:
        print("✅ 当前没有被忽略的账号")
        return

    print("\n📋 当前忽略的账号:")
    for i, account in enumerate(ignored_accounts, 1):
        print(f"  {i}. {account}")


def auto_operations(operation_type='all', headless=True):
    """
    对所有保存的账号执行自动操作
    operation_type: 'checkin' (签到), 'share' (分享), 'all' (全部)
    """
    accounts = get_all_accounts()
    ignored_accounts = set(get_ignored_accounts())
    
    if not accounts:
        print("❌ 未找到已保存的账号，请先使用 'add' 命令添加账号")
        return False

    # 自动初始化今日状态打卡表骨架
    init_today_status_file(accounts, ignored_accounts)

    active_accounts = [account for account in accounts if account not in ignored_accounts]
    skipped_accounts = [account for account in accounts if account in ignored_accounts]

    if skipped_accounts:
        print(f"⏭️ 已忽略 {len(skipped_accounts)} 个账号: {', '.join(skipped_accounts)}")

    if not active_accounts:
        print("❌ 所有账号都在忽略名单中，没有可执行的账号")
        return False
    
    print(f"\n➡️ 开始为 {len(active_accounts)} 个账号执行操作: {operation_type}")
    
    success_count = 0
    results_coins = {}
    failed_accounts = []

    for i, account_name in enumerate(active_accounts, 1):
        print(f"\n[{i}/{len(active_accounts)}] 处理账号: {account_name}")
        
        # 根据操作类型执行不同的功能
        success = False
        if operation_type == 'checkin':
            success = run_check_in_for_account(account_name, headless=headless)
        elif operation_type == 'share':
            # 将headless参数传递给分享函数，允许尝试无头模式
            success = run_share_video_for_account(account_name, headless=headless)
        else:  # 'all'
            # 严格自然日逻辑：只有当签到和分享在今日均已成功完成，才整体跳过该账号
            checkin_done = check_today_status(account_name, '签到')
            share_done = check_today_status(account_name, '分享')
            if checkin_done and share_done:
                print(f"⏭️ 账号 '{account_name}' 今日签到与分享均已完成，跳过")
                success = True
                success_count += 1
                c = get_today_account_coins(account_name)
                if c is not None:
                    results_coins[account_name] = c
                continue

            # 若有任一项未完成，则分别推进（已完成的单项会在各自函数内部秒跳过）
            check_in_success = run_check_in_for_account(account_name, headless=headless)
            share_success = run_share_video_for_account(account_name, headless=headless)
            success = check_in_success and share_success
            
        if success:
            success_count += 1
            c = get_today_account_coins(account_name)
            if c is not None:
                results_coins[account_name] = c
        else:
            failed_accounts.append(account_name)
    
    print(f"\n✅ 操作完成: {success_count}/{len(active_accounts)} 个账号成功")
    
    # 对未在签到/分享过程中直接取得金币的少数账号进行补充获取（若均已取得则无需启动任何浏览器）
    missing_coins = [acc for acc in active_accounts if acc not in results_coins and acc not in failed_accounts]
    if missing_coins:
        print(f"\n🔄 为 {len(missing_coins)} 个账号补充获取金币数量...")
        for acc in missing_coins:
            try:
                coins = get_account_coins(acc, headless=headless)
                if coins is not None:
                    results_coins[acc] = coins
                    update_account_status(acc, "金币", coins=coins)
            except Exception as e:
                print(f"⚠️ 补充获取账号 '{acc}' 金币失败: {e}")

    # 直接输出汇总并触发邮件通知，彻底免除第二轮全量重复打开浏览器的开销
    print_coins_summary_and_send_email(results_coins, failed_accounts)
    
    return success_count > 0

def show_help():
    """显示帮助信息"""
    print("\n📋 可用命令:")
    print("  python main.py login <账号名> [auto] - 🔐 手动登录，加 auto 则登录该账号及后续所有账号")
    print("  python main.py add <邮箱> <密码> - 保存邮箱密码，账号名自动取邮箱 @ 前缀")
    print("  python main.py run              - 为所有账号执行签到和分享操作")
    print("  python main.py checkin          - 仅执行签到操作")
    print("  python main.py share            - 仅执行分享操作")
    print("  python main.py coins            - 获取所有账号的金币数量")
    print("  python main.py list             - 显示所有已保存的账号")
    print("  python main.py ignore <账号名>  - 批量任务忽略指定账号")
    print("  python main.py unignore <账号名> - 取消忽略指定账号")
    print("  python main.py ignored          - 显示当前忽略账号")
    print("  python main.py delete <账号名>  - 删除指定账号")
    print("  python main.py help             - 显示此帮助信息")
    print("\n选项:")
    print("  --visible                       - 使用可见浏览器（默认为隐藏模式运行）")
    print("  --timeout <秒数>                - login 自动等待登录成功的最长时间，默认 180 秒")
    print("  --manual-save                   - login 使用旧模式：登录后手动按回车保存")
    print("\n登录入口点不到时:")
    print("  account_data/yfsp_ui_config.json 中 login_entry_css，或环境变量 YFSP_LOGIN_ENTRY_CSS")
    print("\n💡 推荐用法:")
    print("  1. add <邮箱> <密码> 写入 account.json，再用邮箱 @ 前缀作为账号名 login")
    print("  2. 使用 run/checkin/share 执行自动操作")

def list_accounts():
    """列出所有已保存的账号"""
    accounts = get_all_accounts()
    ignored_accounts = set(get_ignored_accounts())
    
    if not accounts:
        print("❌ 未找到已保存的账号")
        return
    
    print("\n📋 已保存的账号列表:")
    for i, account in enumerate(accounts, 1):
        # 检查账号状态文件是否存在
        state_file = os.path.join('account_data', f"{account}_storage.json")
        status = "✅ 已保存登录状态" if os.path.exists(state_file) else "❌ 未保存登录状态"
        if account in ignored_accounts:
            status += "，⏭️ 批量任务已忽略"
        print(f"  {i}. {account} - {status}")

def get_account_coins(account_name, headless=True):
    """获取账号的金币数量"""
    print(f"\n🔄 正在获取账号 '{account_name}' 的金币数量...")
    browser_args = []
    
    try:
        with sync_playwright() as p:
            browser_type = p.chromium
            try:
                browser = browser_type.launch(headless=headless, args=browser_args)
                print("✅ 已启动浏览器")
            except Exception as e:
                print(f"❌ 启动浏览器失败: {str(e)}")
                return None
                
            try:
                context = browser.new_context()
                
                # 尝试加载已保存的登录状态
                data_dir = 'account_data'
                state_file = os.path.join(data_dir, f"{account_name}_storage.json")
                
                # 加载登录状态
                if not os.path.exists(state_file):
                    print(f"❌ 账号 '{account_name}' 的登录状态文件不存在")
                    return None
                
                try:
                    load_storage_state(context, state_file)
                    print(f"✅ 已加载账号 '{account_name}' 的登录状态")
                except Exception as e:
                    print(f"❌ 无法加载账号状态: {str(e)}")
                    return None
                
                page = context.new_page()
                
                # 导航到个人中心页面
                print("正在导航到个人中心页面...")
                try:
                    page.goto("https://www.yfsp.tv/user/index", timeout=30000, wait_until="domcontentloaded")
                except Exception as e:
                    print(f"⚠️ 导航到个人中心页面超时: {short_error(e)}，尝试继续获取金币")
                
                # 等待页面加载
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception as e:
                    pass
                time.sleep(3)  # 额外等待，确保JavaScript完成渲染
                
                # 获取金币数量
                coins = get_current_coins(page)
                if coins is not None:
                    print(f"✅ 账号 '{account_name}' 当前金币数量: {coins}")
                else:
                    print(f"❌ 未能获取到账号 '{account_name}' 的金币数量")
                
                return coins
            except Exception as e:
                print(f"❌ 获取金币过程中出错: {str(e)}")
                return None
            finally:
                browser.close()
    except Exception as e:
        print(f"❌ 运行获取金币过程发生意外错误: {str(e)}")
        return None

def get_coins_for_all_accounts(headless=True):
    """获取所有账号的金币数量"""
    data_dir = 'account_data'
    if not os.path.exists(data_dir):
        print("❌ 账号数据目录不存在")
        return

    accounts = []
    accounts_file = os.path.join(data_dir, "accounts.txt")
    if os.path.exists(accounts_file):
        with open(accounts_file, 'r', encoding='utf-8') as f:
            accounts = [line.strip() for line in f if line.strip()]

    if not accounts:
        print("❌ 没有找到任何账号")
        return

    ignored_accounts = set(get_ignored_accounts())
    skipped_accounts = [account for account in accounts if account in ignored_accounts]
    accounts = [account for account in accounts if account not in ignored_accounts]

    if skipped_accounts:
        print(f"⏭️ 金币统计已忽略 {len(skipped_accounts)} 个账号: {', '.join(skipped_accounts)}")

    if not accounts:
        print("❌ 所有账号都在忽略名单中，没有可统计的账号")
        return

    results = {}
    failed_accounts = []

    for account_name in accounts:
        try:
            coins = get_account_coins(account_name, headless=headless)
            if coins is not None:
                results[account_name] = coins
                print(f"✅ {account_name}: {coins} 金币")
                update_account_status(account_name, "金币", coins=coins)
            else:
                failed_accounts.append(account_name)
                print(f"❌ {account_name}: 获取金币失败")
        except Exception as e:
            failed_accounts.append(account_name)
            print(f"❌ {account_name}: 发生错误 - {str(e)}")

    # 统一打印汇总结果并发送邮件通知
    final_failed_accounts = failed_accounts.copy()
    print_coins_summary_and_send_email(results, final_failed_accounts)

def delete_account(account_name):
    """删除账号：移除 account.json 中的凭据、旧版 *_account.json、登录态，并从 accounts.txt 移除（若在该列表中）。"""
    data_dir = "account_data"

    if not account_name or not str(account_name).strip():
        print("❌ 请提供要删除的账号名称")
        return False

    account_name = str(account_name).strip()
    accounts_file = os.path.join(data_dir, "accounts.txt")
    accounts = []
    if os.path.exists(accounts_file):
        with open(accounts_file, "r", encoding="utf-8") as f:
            accounts = [line.strip() for line in f if line.strip()]
    in_list = account_name in accounts

    db_path = os.path.join(data_dir, "account.json")
    in_db = False
    db = {}
    if os.path.exists(db_path):
        try:
            with open(db_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                db = raw
                in_db = account_name in db
        except Exception as e:
            print(f"⚠️ 读取 account.json 失败: {e}")

    legacy_path = os.path.join(data_dir, f"{account_name}_account.json")
    state_file = os.path.join(data_dir, f"{account_name}_storage.json")
    orphan = os.path.exists(legacy_path) or os.path.exists(state_file)

    if not in_list and not in_db and not orphan:
        print(f"❌ 账号 {account_name} 不存在（未在 accounts.txt、account.json 中找到，且无旧凭据/状态文件）")
        return False

    try:
        if os.path.exists(state_file):
            os.remove(state_file)
            print(f"✅ 已删除账号存储文件: {state_file}")

        if os.path.exists(legacy_path):
            os.remove(legacy_path)
            print(f"✅ 已删除旧版凭据文件: {legacy_path}")

        if in_db:
            del db[account_name]
            with open(db_path, "w", encoding="utf-8") as f:
                json.dump(db, f, ensure_ascii=False, indent=2)
            print(f"✅ 已从 account.json 移除账号: {account_name}")

        if in_list:
            accounts.remove(account_name)
            with open(accounts_file, "w", encoding="utf-8") as f:
                for acc in accounts:
                    f.write(f"{acc}\n")
            print(f"✅ 已从 accounts.txt 移除: {account_name}")

        ignored_accounts = get_ignored_accounts()
        if account_name in ignored_accounts:
            ignored_accounts.remove(account_name)
            save_ignored_accounts(ignored_accounts)
            print(f"✅ 已从忽略名单移除: {account_name}")

        return True

    except Exception as e:
        print(f"❌ 删除账号时出错: {str(e)}")
        return False

def send_email(subject, content):
    """发送邮件通知"""
    try:
        # 如果邮件配置不完整，则静默跳过发送，避免因为本地未配置影响脚本其它功能
        if not all([EMAIL_HOST, EMAIL_PORT, EMAIL_USER, EMAIL_PASS, EMAIL_TO]):
            logging.warning("邮件配置不完整，跳过发送邮件")
            return

        # 创建邮件内容（严格遵循 RFC 5322 规范，补齐 Date 与 Message-ID 等防垃圾拦截标准头）
        msg = MIMEText(content, 'plain', 'utf-8')
        msg['From'] = formataddr((Header('爱壹帆助手', 'utf-8').encode(), EMAIL_USER))
        msg['To'] = formataddr((Header('爱壹帆用户', 'utf-8').encode(), EMAIL_TO))
        msg['Subject'] = Header(subject, 'utf-8')
        msg['Date'] = formatdate(localtime=True)
        msg['Message-ID'] = make_msgid(domain='qq.com')
        
        # 记录邮件发送尝试
        logging.info(f"尝试发送邮件: 从 {EMAIL_USER} 到 {EMAIL_TO}")
        logging.info(f"邮件主题: {subject}")
        
        # 创建 SSL 上下文，禁用证书验证
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        
        # 尝试发送邮件
        with smtplib.SMTP_SSL(EMAIL_HOST, EMAIL_PORT, context=context) as server:
            logging.info("已连接到 SMTP 服务器")
            server.login(EMAIL_USER, EMAIL_PASS)
            logging.info("登录成功")
            server.sendmail(EMAIL_USER, [EMAIL_TO], msg.as_string())
            logging.info("邮件发送成功")
        print("✅ 邮件发送成功")
    except smtplib.SMTPAuthenticationError as e:
        logging.error(f"SMTP 认证失败: {str(e)}")
        print(f"❌ 邮件发送失败: SMTP 认证失败 - {str(e)}")
    except smtplib.SMTPException as e:
        logging.error(f"SMTP 错误: {str(e)}")
        print(f"❌ 邮件发送失败: SMTP 错误 - {str(e)}")
    except Exception as e:
        logging.error(f"邮件发送失败: {str(e)}")
        print(f"❌ 邮件发送失败: {str(e)}")
        # 打印完整的错误信息
        import traceback
        logging.error(traceback.format_exc())

def main():
    """主函数，处理命令行参数"""
    # 检查是否有足够的参数
    if len(sys.argv) < 2:
        show_help()
        return
    
    # 解析命令行参数
    command = sys.argv[1].lower()
    
    # 检查是否有--visible选项
    headless = '--visible' not in sys.argv
    
    # 处理不同的命令
    if command == 'add':
        if len(sys.argv) < 4:
            print("❌ 请提供邮箱和密码")
            print("用法: python main.py add <邮箱> <密码>")
            return

        email = sys.argv[2]
        password = sys.argv[3]

        if not email or not password:
            print("❌ 缺少必要的参数")
            print("用法: python main.py add <邮箱> <密码>")
            return

        add_account(email, password)
    
    elif command == 'delete':
        if len(sys.argv) < 3:
            print("❌ 请提供要删除的账号名称")
            print("用法: python main.py delete <账号名称>")
            return
            
        account_name = sys.argv[2]
        delete_account(account_name)
    
    elif command == 'run':
        auto_operations('all', headless=headless)
    
    elif command == 'checkin':
        auto_operations('checkin', headless=headless)
    
    elif command == 'share':
        auto_operations('share', headless=headless)
    
    elif command == 'coins':
        get_coins_for_all_accounts(headless=headless)
    
    elif command == 'list':
        list_accounts()

    elif command == 'ignore':
        if len(sys.argv) < 3:
            print("❌ 请提供要忽略的账号名称")
            print("用法: python main.py ignore <账号名>")
            return

        ignore_account(sys.argv[2])

    elif command == 'unignore':
        if len(sys.argv) < 3:
            print("❌ 请提供要取消忽略的账号名称")
            print("用法: python main.py unignore <账号名>")
            return

        unignore_account(sys.argv[2])

    elif command == 'ignored':
        list_ignored_accounts()
    
    elif command == 'login':
        if len(sys.argv) < 3:
            print("❌ 请提供账号名称")
            print("用法: python main.py login <账号名> [auto] [--timeout 秒数] [--manual-save]")
            return
        
        arg2 = sys.argv[2]
        other_args = [a.lower() for a in sys.argv[3:]]
        is_auto = ('auto' in other_args) or ('--auto' in other_args)

        timeout_seconds = 180
        manual_save = '--manual-save' in sys.argv

        if '--timeout' in sys.argv:
            timeout_index = sys.argv.index('--timeout')
            if timeout_index + 1 >= len(sys.argv):
                print("❌ --timeout 后需要提供秒数")
                return
            try:
                timeout_seconds = int(sys.argv[timeout_index + 1])
                if timeout_seconds <= 0:
                    raise ValueError
            except ValueError:
                print("❌ --timeout 必须是正整数秒数")
                return

        accounts_in_db = get_accounts_from_account_json()

        if arg2.lower() in ('auto', '--auto'):
            # 如果命令为 login auto，则从 account.json 的第一个账号开始批量登录
            is_auto = True
            if not accounts_in_db:
                print("❌ account_data/account.json 中未找到任何账号凭据")
                return
            target_accounts = accounts_in_db
        elif is_auto:
            # 如果命令为 login <账号名> auto，从该账号开始一直到最后一个账号
            account_name = arg2
            if not accounts_in_db:
                print("❌ account_data/account.json 中未找到任何账号凭据")
                return
            if account_name not in accounts_in_db:
                print(f"❌ 账号 '{account_name}' 不在 account_data/account.json 中")
                print(f"   account.json 中的可用账号: {', '.join(accounts_in_db)}")
                return
            start_index = accounts_in_db.index(account_name)
            target_accounts = accounts_in_db[start_index:]
        else:
            # 单账号模式
            target_accounts = [arg2]

        if len(target_accounts) > 1:
            total = len(target_accounts)
            print(f"\n🚀 启动批量自动登录模式（起点账号: {target_accounts[0]}），共 {total} 个账号: {', '.join(target_accounts)}")
            success_count = 0
            try:
                for idx, acc in enumerate(target_accounts, 1):
                    print(f"\n{'=' * 60}")
                    print(f"🔄 [{idx}/{total}] 准备登录账号: {acc}")
                    print(f"{'=' * 60}")
                    ok = manual_login(acc, timeout_seconds=timeout_seconds, manual_save=manual_save, batch_mode=True)
                    if ok:
                        success_count += 1
                        print(f"\n✅ [{idx}/{total}] 账号 '{acc}' 登录完成！")
                    else:
                        print(f"\n⚠️ [{idx}/{total}] 账号 '{acc}' 未能成功登录。")

                    if idx < total:
                        next_acc = target_accounts[idx]
                        print(f"⏳ 2 秒后自动打开下一个账号: {next_acc} ...")
                        time.sleep(2)
            except KeyboardInterrupt:
                print("\n\n⏹️ 检测到中断信号，已停止批量登录。")

            print(f"\n{'=' * 60}")
            print(f"🏁 批量连续登录结束: {success_count}/{total} 个账号成功完成登录。")
            print(f"{'=' * 60}")
        else:
            manual_login(target_accounts[0], timeout_seconds=timeout_seconds, manual_save=manual_save, batch_mode=False)
    
    elif command in ['help', '-h', '--help']:
        show_help()
    
    else:
        print(f"❌ 未知命令: {command}")
        show_help()

if __name__ == "__main__":
    main()
    # print("sys.argv:", sys.argv)
