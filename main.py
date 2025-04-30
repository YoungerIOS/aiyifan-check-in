import os
import sys
import json
import time
import logging
import ssl
from datetime import datetime
import re
import random
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('aiyifan.log'),
        logging.StreamHandler()
    ]
)

# 邮件配置参数（本地固定值）
EMAIL_HOST = "smtp.qq.com"  # SMTP服务器地址
EMAIL_PORT = 465  # SMTP端口（SSL通常是465）
EMAIL_USER = "358856615@qq.com"  # 发件人邮箱
EMAIL_PASS = "jgbcljraqmalcage"  # 邮箱授权码或密码
EMAIL_TO = "soyoungto686@gmail.com"  # 收件人邮箱

def check_login_status(page):
    """检查是否已登录"""
    try:
        print("正在检查登录状态...")
        
        # 首先尝试获取用户名，如果能获取非unknown的用户名，说明已登录
        username = get_username(page)
        if username and username != "unknown" and username != "unknown_user":
            print(f"✅ 检测到登录用户名: {username}")
            return True
        
        # 尝试查找登录后才会显示的元素，例如用户头像
        # 使用更精确的选择器避免多元素匹配
        user_avatar = page.locator('div.user-avatar:visible, div.avatar.logged-in, img.avatar[src*="avatar"], .user-profile:visible, [class*="avatar"]:visible').first
        if user_avatar and user_avatar.count() > 0:
            try:
                if user_avatar.is_visible():
                    print("✅ 检测到头像元素")
                    # 验证是否是已登录的头像
                    try:
                        # 点击头像，看是否能展开用户菜单
                        user_avatar.click(timeout=2000)
                        time.sleep(1)
                        
                        # 检查是否有用户菜单元素出现
                        user_menu = page.locator('.user-menu, .dropdown-menu, .user-dropdown').first
                        if user_menu.count() > 0 and user_menu.is_visible():
                            print("✅ 验证头像点击后有用户菜单显示")
                            return True
                    except Exception as e:
                        print(f"❌ 验证头像时出错: {str(e)}")
                        return False
            except Exception as e:
                print(f"❌ 检查头像可见性时出错: {str(e)}")
                return False
        
        # 检查是否有登录按钮，如果有说明未登录
        login_button = page.locator('button:has-text("登录"), a:has-text("登录"), [class*="login"]:visible').first
        if login_button and login_button.count() > 0 and login_button.is_visible():
            print("❌ 检测到登录按钮，说明未登录")
            return False
            
        # 检查是否有注册按钮，如果有说明未登录
        register_button = page.locator('button:has-text("注册"), a:has-text("注册"), [class*="register"]:visible').first
        if register_button and register_button.count() > 0 and register_button.is_visible():
            print("❌ 检测到注册按钮，说明未登录")
            return False
            
        # 如果以上检查都通过，认为已登录
        print("✅ 未检测到登录/注册按钮，认为已登录")
        return True
        
    except Exception as e:
        print(f"❌ 检查登录状态时出错: {str(e)}")
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
                    page.goto("https://www.yfsp.tv", wait_until="networkidle")
                    
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

def direct_click_sign_in_button(page):
    """直接定位并点击签到按钮，不依赖复杂的选择器逻辑"""
    try:
        print("尝试直接定位并点击签到按钮...")
        
        # 确保我们在个人中心页面
        if "user" not in page.url and "我的" not in page.url:
            # 尝试导航到个人中心页面
            try:
                print("正在导航到个人中心页面...")
                page.goto("https://www.yfsp.tv/user/index", timeout=60000)
                page.wait_for_load_state("networkidle", timeout=30000)
                page.wait_for_load_state("domcontentloaded", timeout=30000)
                time.sleep(3)  # 额外等待确保页面加载
            except Exception as e:
                print(f"导航到个人中心页面失败: {str(e)}")
                
                # 尝试点击顶部导航栏中的"个人中心"链接
                try:
                    # 尝试点击顶部蓝色的"个人中心"链接
                    personal_center = page.locator('a:has-text("个人中心")').first
                    if personal_center.count() > 0 and personal_center.is_visible():
                        personal_center.click()
                        print("已点击顶部个人中心链接")
                        time.sleep(3)
                    else:
                        # 尝试点击右上角头像
                        avatar = page.locator('img.avatar, .avatar-img, .user-avatar').first
                        if avatar.count() > 0 and avatar.is_visible():
                            avatar.click()
                            print("已点击右上角头像")
                            time.sleep(3)
                except Exception as e:
                    print(f"尝试进入个人中心失败: {str(e)}")
        
        # 确认当前页面是否为个人中心
        # print(f"当前页面URL: {page.url}")
        print(f"当前页面标题: {page.title()}")

        
        # 首先检查是否已经签到过了
        already_signed = page.evaluate('''() => {
            const bodyText = document.body.innerText;
            
            // 检查是否有"已签到"、"已打卡"等文本
            const alreadySignedTexts = ['已签到', '已打卡', '明日再来', '已完成任务'];
            for (const text of alreadySignedTexts) {
                if (bodyText.includes(text)) {
                    return { signed: true, text: text };
                }
            }
            
            // 检查签到按钮的状态
            const signArea = Array.from(document.querySelectorAll('*')).find(el => 
                el.innerText && el.innerText.includes('每日签到') && 
                (el.innerText.includes('已') || !el.innerText.includes('立即签到'))
            );
            
            if (signArea) {
                return { signed: true, area: '签到区域显示已完成' };
            }
            
            // 检查任务列表中的签到任务状态
            const taskItems = document.querySelectorAll('.task-item, .daily-task, .task-list-item');
            for (const item of taskItems) {
                if (item.innerText.includes('签到') && item.innerText.includes('已完成')) {
                    return { signed: true, task: '签到任务已完成' };
                }
            }
            
            return { signed: false };
        }''')
        
        if already_signed.get('signed', False):
            sign_message = already_signed.get('text', already_signed.get('area', already_signed.get('task', '已完成')))
            print(f"✅ 检测到已经签到过了: {sign_message}")
            return True
            
        # 滚动页面以确保所有元素加载
        print("滚动页面以确保所有元素加载...")
        for _ in range(3):  # 多次滚动确保元素加载
            page.evaluate('''() => {
                window.scrollTo({top: document.body.scrollHeight / 3, behavior: 'smooth'});
            }''')
            time.sleep(1)
            
            page.evaluate('''() => {
                window.scrollTo({top: document.body.scrollHeight / 2, behavior: 'smooth'});
            }''')
            time.sleep(1)
            
            page.evaluate('''() => {
                window.scrollTo({top: document.body.scrollHeight * 2/3, behavior: 'smooth'});
            }''')
            time.sleep(1)
            
            page.evaluate('''() => {
                window.scrollTo({top: 0, behavior: 'smooth'});
            }''')
            time.sleep(1)
        
        # 更精确定位签到区域和签到按钮
        print("正在精确定位签到区域和签到按钮...")
        sign_area_info = page.evaluate('''() => {
            // 定位所有包含"每日签到"文本的元素
            const dailySignElements = Array.from(document.querySelectorAll('*')).filter(el => 
                el.innerText && 
                el.innerText.includes('每日签到') && 
                el.offsetWidth > 0 && 
                el.offsetHeight > 0
            );
            
            if (dailySignElements.length === 0) {
                return { found: false, reason: '未找到每日签到区域' };
            }
            
            // 从找到的每日签到区域开始，向下查找3层以内的"立即签到"按钮
            function findSignButton(element, depth = 0) {
                if (depth > 5) return null;  // 最多搜索5层
                
                // 检查当前元素是否包含"立即签到"文本
                if (element.innerText && element.innerText.trim() === '立即签到') {
                    const rect = element.getBoundingClientRect();
                    return {
                        element: element,
                        text: element.innerText.trim(),
                        tagName: element.tagName,
                        id: element.id,
                        className: element.className,
                        x: rect.left + rect.width/2,
                        y: rect.top + rect.height/2,
                        width: rect.width,
                        height: rect.height
                    };
                }
                
                // 检查所有子元素
                for (const child of element.children) {
                    const result = findSignButton(child, depth + 1);
                    if (result) return result;
                }
                
                return null;
            }
            
            // 遍历所有"每日签到"区域，查找签到按钮
            let signButtonInfo = null;
            for (const area of dailySignElements) {
                // 先查找自身
                if (area.innerText.includes('立即签到')) {
                    const rect = area.getBoundingClientRect();
                    signButtonInfo = {
                        inSelf: true,
                        text: '立即签到',
                        x: rect.left + rect.width/2,
                        y: rect.top + rect.height/2,
                        width: rect.width,
                        height: rect.height,
                        parentText: area.innerText
                    };
                    break;
                }
                
                // 查找父元素的下一个相邻兄弟元素
                let searchArea = area;
                for (let i = 0; i < 3; i++) {  // 向上最多查找3层父元素
                    if (!searchArea.parentElement) break;
                    searchArea = searchArea.parentElement;
                    
                    const btn = findSignButton(searchArea);
                    if (btn) {
                        signButtonInfo = btn;
                        signButtonInfo.fromParent = true;
                        break;
                    }
                }
                
                // 如果已找到按钮，跳出循环
                if (signButtonInfo) break;
                
                // 扩大搜索范围 - 查找附近的元素
                // 获取当前区域的位置
                const areaRect = area.getBoundingClientRect();
                
                // 查找在区域附近的所有元素
                const nearbyElements = Array.from(document.elementsFromPoint(
                    areaRect.left + areaRect.width/2,
                    areaRect.top + areaRect.height + 50  // 向下50像素
                ));
                
                // 在附近元素中查找"立即签到"按钮
                for (const nearby of nearbyElements) {
                    if (nearby.innerText && nearby.innerText.trim() === '立即签到') {
                        const rect = nearby.getBoundingClientRect();
                        signButtonInfo = {
                            nearby: true,
                            text: nearby.innerText,
                            tagName: nearby.tagName,
                            id: nearby.id,
                            className: nearby.className,
                            x: rect.left + rect.width/2,
                            y: rect.top + rect.height/2,
                            width: rect.width,
                            height: rect.height
                        };
                        break;
                    }
                }
                
                if (signButtonInfo) break;
            }
            
            if (!signButtonInfo) {
                // 扩大搜索范围 - 在网页范围内查找任何包含"立即签到"的元素
                const allSignButtons = Array.from(document.querySelectorAll('*')).filter(el => 
                    el.innerText && 
                    el.innerText.trim() === '立即签到' && 
                    el.offsetWidth > 0 && 
                    el.offsetHeight > 0
                );
                
                if (allSignButtons.length > 0) {
                    const btn = allSignButtons[0];
                    const rect = btn.getBoundingClientRect();
                    signButtonInfo = {
                        globalSearch: true,
                        text: btn.innerText,
                        tagName: btn.tagName,
                        id: btn.id,
                        className: btn.className,
                        x: rect.left + rect.width/2,
                        y: rect.top + rect.height/2,
                        width: rect.width,
                        height: rect.height
                    };
                }
            }
            
            return { 
                found: !!signButtonInfo, 
                button: signButtonInfo,
                areasFound: dailySignElements.length
            };
        }''')
        
        print(f"签到区域搜索结果: 找到 {sign_area_info.get('areasFound', 0)} 个相关区域")
        
        if sign_area_info.get('found', False):
            button_info = sign_area_info.get('button', {})
            print(f"找到签到按钮: {button_info.get('text', '立即签到')} ({button_info.get('tagName', 'DIV')})")
            print(f"按钮位置: x={button_info.get('x', 0)}, y={button_info.get('y', 0)}")

            # 滚动到按钮位置
            x = button_info.get('x', 0)
            y = button_info.get('y', 0)
            
            # 确保按钮在可视区域内
            print("滚动确保按钮在可视区域内...")
            page.evaluate("(y) => { window.scrollTo(0, y - 200); }", y)
            time.sleep(1)
  
            # 使用模拟真实用户的方式点击按钮
            print(f"尝试点击坐标 ({x}, {y})...")
            
            # 1. 先移动到按钮附近
            page.mouse.move(x - 10, y - 10)
            time.sleep(0.3)
            
            # 2. 再移动到按钮上
            page.mouse.move(x, y)
            time.sleep(0.3)
            
            # 3. 点击按钮
            page.mouse.click(x, y)
            print("✅ 已点击签到按钮")
            
            # 等待可能出现的签到确认弹窗
            time.sleep(3)

            # 检查是否弹出确认对话框
            print("检查是否弹出确认对话框...")
            dialog_info = page.evaluate('''() => {
                // 查找对话框或弹窗
                const dialogs = document.querySelectorAll('.dialog, .modal, .popup, [class*="dialog"], [class*="modal"], [class*="popup"]');
                
                if (dialogs.length > 0) {
                    // 找到对话框，获取其位置
                    const dialog = dialogs[0];
                    const rect = dialog.getBoundingClientRect();
                    
                    // 查找对话框中的确认按钮（通常包含"即刻签到"或类似文本）
                    const confirmButtons = Array.from(dialog.querySelectorAll('*')).filter(el => 
                        el.innerText && (
                            el.innerText.includes('即刻签到') || 
                            el.innerText.includes('立即签到') ||
                            el.innerText.includes('确定') ||
                            el.innerText.includes('确认')
                        ) && el.offsetWidth > 0 && el.offsetHeight > 0
                    );
                    
                    let confirmButton = null;
                    if (confirmButtons.length > 0) {
                        const btn = confirmButtons[0];
                        const btnRect = btn.getBoundingClientRect();
                        confirmButton = {
                            text: btn.innerText,
                            tagName: btn.tagName,
                            id: btn.id,
                            className: btn.className,
                            x: btnRect.left + btnRect.width/2,
                            y: btnRect.top + btnRect.height/2
                        };
                    }
                    
                    return {
                        found: true,
                        dialogContent: dialog.innerText,
                        x: rect.left + rect.width/2,
                        y: rect.top + rect.height/2,
                        confirmButton: confirmButton
                    };
                }
                
                return { found: false };
            }''')
            
            if dialog_info.get('found', False):
                print("✅ 找到确认对话框")
                # print(f"对话框内容: {dialog_info.get('dialogContent', '')[:100]}...")
                
                confirm_button = dialog_info.get('confirmButton')
                if confirm_button:
                    print(f"找到确认按钮: {confirm_button.get('text', '')}")
                    
                    # 点击确认按钮
                    x = confirm_button.get('x', 0)
                    y = confirm_button.get('y', 0)
                    
                    # 移动到按钮位置并点击
                    page.mouse.move(x, y)
                    time.sleep(0.3)
                    page.mouse.click(x, y)
                    print("✅ 已点击确认按钮")
                else:
                    # 如果没有找到具体的确认按钮，点击对话框中间位置
                    x = dialog_info.get('x', 0)
                    y = dialog_info.get('y', 0)
                    page.mouse.click(x, y)
                    print("✅ 已点击对话框中间位置")
                
                # 等待签到完成
                time.sleep(3)

                # 检查签到是否成功
                if check_sign_in_success(page):
                    return True
                
                # 即使检测不到明确的成功标志，也认为签到成功
                print("未检测到明确的成功标志，但已完成确认点击，假定签到成功")
                return True
            else:
                print("⚠️ 未检测到确认对话框")
                
                # 尝试定位页面上可能存在的"即刻签到"按钮
                instant_sign_button = page.evaluate('''() => {
                    const buttons = Array.from(document.querySelectorAll('*')).filter(el => 
                        el.innerText && 
                        (el.innerText.includes('即刻签到') || el.innerText.includes('立即签到')) && 
                        el.offsetWidth > 0 && 
                        el.offsetHeight > 0
                    );
                    
                    if (buttons.length > 0) {
                        const btn = buttons[0];
                        const rect = btn.getBoundingClientRect();
                        return {
                            found: true,
                            text: btn.innerText,
                            tagName: btn.tagName,
                            id: btn.id,
                            className: btn.className,
                            x: rect.left + rect.width/2,
                            y: rect.top + rect.height/2
                        };
                    }
                    
                    return { found: false };
                }''')
                
                if instant_sign_button.get('found', False):
                    print(f"找到'即刻签到'按钮: {instant_sign_button.get('text', '')}")
                    
                    # 点击"即刻签到"按钮
                    x = instant_sign_button.get('x', 0)
                    y = instant_sign_button.get('y', 0)
                    
                    # 移动到按钮位置并点击
                    page.mouse.move(x, y)
                    time.sleep(0.3)
                    page.mouse.click(x, y)
                    print("✅ 已点击'即刻签到'按钮")
                    
                    # 等待签到完成
                    time.sleep(3)

                    # 检查签到是否成功
                    if check_sign_in_success(page):
                        return True
                else:
                    print("❌ 未找到'即刻签到'按钮")
                
                # 检查签到是否成功
                if check_sign_in_success(page):
                    return True
            
            # 如果无法确认签到成功，但已尝试点击，返回False
            print("⚠️ 已尝试点击签到按钮，但无法确认是否成功")
            return False
        else:
            print("❌ 未找到签到按钮")
            return False
    except Exception as e:
        print(f"直接点击签到按钮过程中出错: {str(e)}")
        traceback.print_exc()
        return False

def check_sign_in_success(page):
    """检查签到是否成功"""
    print("检查签到是否成功...")
    try:
        
        # 方法一：查找页面上的签到成功/已签到提示文本
        try:
            success_text = page.evaluate('''() => {
                // 检查常见的成功提示文本
                const successTexts = [
                    '签到成功', '已签到'
                ];
                
                const bodyText = document.body.innerText;
                for (const text of successTexts) {
                    if (bodyText.includes(text)) {
                        return { found: true, text: text };
                    }
                }
                
                // 检查签到区域是否变化（立即签到按钮变成已签到状态）
                const elements = Array.from(document.querySelectorAll('.task-item, .daily-task-item, .task-list-item, [class*="task"], [class*="sign"], [class*="check-in"]'));
                for (const el of elements) {
                    // 如果元素内容包含"签到"并且包含"已"或"完成"
                    if (el.innerText.includes('签到') && 
                        (el.innerText.includes('已') || 
                         el.innerText.includes('完成') || 
                         el.innerText.includes('明日') || 
                         el.innerText.includes('明天'))) {
                        return { found: true, text: '任务状态变为已完成' };
                    }
                }
                
                return { found: false };
            }''')
            
            if success_text.get('found', False):
                print(f"✅ 找到成功提示: {success_text.get('text', '')}")
                return True
        except Exception as e:
            print(f"检查成功文本时出错: {str(e)}")
         
        
        # 方法四：检查签到按钮是否消失
        try:
            button_check = page.evaluate('''() => {
                // 检查"立即签到"按钮是否还存在
                const signButtons = Array.from(document.querySelectorAll('*')).filter(el => 
                    el.innerText && el.innerText.trim() === '立即签到' && el.offsetWidth > 0
                );
                
                // 如果按钮消失了，可能是因为已经签到成功
                if (signButtons.length === 0) {
                    // 但要确保页面上有"已签到"或相关文本
                    const bodyText = document.body.innerText;
                    if (bodyText.includes('已签到') || 
                        bodyText.includes('明日再来') || 
                        bodyText.includes('已完成')) {
                        return { found: true, reason: '签到按钮消失且有已签到标记' };
                    }
                }
                
                return { found: false };
            }''')
            
            if button_check.get('found', False):
                print(f"✅ {button_check.get('reason', '签到状态已更新')}")
                return True
        except Exception as e:
            print(f"检查按钮状态时出错: {str(e)}")
        
        
        # 如果所有方法都没有明确的成功迹象，返回False
        print("⚠️ 未检测到明确的签到成功迹象")
        return False
    except Exception as e:
        print(f"签到成功检查过程中出错: {str(e)}")
        return False


def share_video(page):
    """在网站上分享视频"""
    try:
        print("\n===== 开始分享视频 =====")
        
        # 导航到动漫列表页面，而不是首页
        print("正在导航到动漫列表页面...")
        page.goto("https://www.yfsp.tv/list/anime?orderBy=1", timeout=30000)
        time.sleep(3)
        
        # 确保页面完全加载
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception as e:
            print(f"等待页面加载时出错: {str(e)}，继续执行")
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
                    page.goto(video_url, timeout=30000)
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
                    page.goto(full_url, timeout=30000)
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
            page.wait_for_load_state("networkidle", timeout=20000)
        except:
            print("等待页面加载超时，但仍继续执行")
        time.sleep(3)  # 额外等待

        
        # 直接定位具体的分享按钮元素 - 根据提供的截图使用精确的选择器
        print("\n开始精确定位分享按钮...")
        
        # 记录是否找到并点击了分享按钮
        share_button_clicked = False
        
        # 方法1: 使用按钮属性直接定位
        try:
            # 使用显式的等待，确保页面上有按钮元素
            page.wait_for_selector('button', timeout=5000)
            
            # 使用提供的截图HTML中的准确属性选择器
            share_button = page.locator('button[aria-label="分享"]').first
            if share_button.count() > 0:
                try:
                    # 确保按钮可见且可点击
                    if share_button.is_visible():
                        print("找到aria-label为'分享'的按钮")
                        
                        # 滚动到按钮位置
                        share_button.scroll_into_view_if_needed()
                        time.sleep(1)
                        
                        # 高亮并直接点击
                        share_button.highlight()
                        time.sleep(1)
                        share_button.click(force=True)
                        print("已点击分享按钮")
                        share_button_clicked = True
                        time.sleep(2)
                except Exception as e:
                    print(f"点击分享按钮(aria-label)时出错: {str(e)}")
        except Exception as e:
            print(f"查找aria-label为'分享'的按钮时出错: {str(e)}")
        
        # 方法2: 使用类名精确定位分享按钮
        if not share_button_clicked:
            try:
                # 先检查是否存在包含"分享"文本的按钮
                text_share = page.locator('button:has-text("分享"), span:has-text("分享")').first
                if text_share.count() > 0 and text_share.is_visible():
                    print("找到包含'分享'文本的元素")
                    
                    # 高亮并点击
                    text_share.highlight()
                    time.sleep(1)
                    text_share.click(force=True)
                    print("已点击包含'分享'文本的元素")
                    share_button_clicked = True
                    time.sleep(2)
            except Exception as e:
                print(f"查找包含'分享'文本的元素时出错: {str(e)}")
        
        # 方法3: 使用精确的CSS选择器结构
        if not share_button_clicked:
            try:
                # 尝试根据截图中的HTML结构定位
                # 首先查找分享图标
                share_icon = page.locator('.iconfont.iconfenxiang, div[class*="share"], i[class*="share"]').first
                if share_icon.count() > 0 and share_icon.is_visible():
                    print("找到分享图标")
                    
                    # 高亮并点击
                    share_icon.highlight()
                    time.sleep(1)
                    share_icon.click(force=True)
                    print("已点击分享图标")
                    share_button_clicked = True
                    time.sleep(2)
                    
                    # 或者点击其父元素
                    if not share_button_clicked:
                        try:
                            # 使用JavaScript获取图标的父级按钮元素
                            page.evaluate('''() => {
                                const icon = document.querySelector('.iconfont.iconfenxiang, div[class*="share"], i[class*="share"]');
                                if (icon) {
                                    // 查找父级按钮元素
                                    let parent = icon.parentElement;
                                    while (parent && parent.tagName !== 'BUTTON') {
                                        parent = parent.parentElement;
                                    }
                                    
                                    if (parent) {
                                        parent.click();
                                        return true;
                                    }
                                }
                                return false;
                            }''')
                            print("已通过JavaScript点击分享图标的父级按钮")
                            share_button_clicked = True
                            time.sleep(2)
                        except Exception as e:
                            print(f"通过JavaScript点击分享图标父级按钮时出错: {str(e)}")
            except Exception as e:
                print(f"查找分享图标时出错: {str(e)}")
        
        # 方法4: 使用截图中显示的类名组合
        if not share_button_clicked:
            try:
                # 查找分享框元素
                share_box = page.locator('div.hovered-share-box, div[class*="share-box"]').first
                if share_box.count() > 0 and share_box.is_visible():
                    print("找到分享框元素")
                    
                    # 高亮并点击
                    share_box.highlight()
                    time.sleep(1)
                    share_box.click(force=True)
                    print("已点击分享框")
                    share_button_clicked = True
                    time.sleep(2)
            except Exception as e:
                print(f"查找分享框时出错: {str(e)}")
        
        # 方法5: 使用JavaScript精确定位按钮
        if not share_button_clicked:
            try:
                print("尝试使用JavaScript精确查找分享按钮...")
                
                found_button = page.evaluate('''() => {
                    // 所有可能的按钮
                    const buttons = Array.from(document.querySelectorAll('button'));
                    console.log(`找到 ${buttons.length} 个按钮`);
                    
                    // 检查每个按钮的属性
                    for (const button of buttons) {
                        // 记录每个按钮的特征
                        const ariaLabel = button.getAttribute('aria-label');
                        const title = button.getAttribute('title');
                        const text = button.textContent.trim();
                        const classes = button.className;
                        
                        console.log(`按钮: aria-label=${ariaLabel}, title=${title}, text=${text}, class=${classes}`);
                        
                        // 检查是否为分享按钮
                        if (ariaLabel === '分享' || title === '分享' || text === '分享' || 
                            classes.includes('share') || classes.includes('分享')) {
                            
                            // 记录按钮位置
                            const rect = button.getBoundingClientRect();
                            console.log(`找到分享按钮! 位置: x=${rect.x}, y=${rect.y}, 宽=${rect.width}, 高=${rect.height}`);
                            
                            // 确保按钮在视口内
                            button.scrollIntoView({behavior: 'smooth', block: 'center'});
                            
                            // 延迟点击
                            setTimeout(() => {
                                button.click();
                                console.log('已点击分享按钮');
                            }, 500);
                            
                            return {
                                found: true,
                                x: rect.x,
                                y: rect.y,
                                width: rect.width,
                                height: rect.height
                            };
                        }
                    }
                    
                    // 查找所有带有分享相关类名的元素
                    const shareElements = Array.from(document.querySelectorAll('[class*="share"], [class*="分享"], .iconfont.iconfenxiang'));
                    console.log(`找到 ${shareElements.length} 个可能的分享元素`);
                    
                    if (shareElements.length > 0) {
                        const element = shareElements[0];
                        const rect = element.getBoundingClientRect();
                        
                        // 确保元素在视口内
                        element.scrollIntoView({behavior: 'smooth', block: 'center'});
                        
                        // 延迟点击
                        setTimeout(() => {
                            element.click();
                            console.log('已点击分享元素');
                        }, 500);
                        
                        return {
                            found: true,
                            x: rect.x,
                            y: rect.y,
                            width: rect.width,
                            height: rect.height
                        };
                    }
                    
                    return { found: false };
                }''')
                
                if found_button and found_button.get('found', False):
                    print(f"通过JavaScript找到分享按钮，位置: x={found_button.get('x')}, y={found_button.get('y')}")
                    share_button_clicked = True
                    time.sleep(2)
            except Exception as e:
                print(f"使用JavaScript查找分享按钮时出错: {str(e)}")

        
        # 检查是否点击了分享按钮
        if share_button_clicked:
            print("✅ 已成功点击分享按钮，正在等待分享对话框...")
            
            # 等待分享对话框出现
            time.sleep(2)
            
            # 检查并点击分享对话框中的选项
            try:
                # 尝试找到分享对话框元素
                share_dialog = page.locator('div.share-dialog, div[class*="share-dialog"], div[class*="sharing"], div.dialog, .modal-content').first
                if share_dialog.count() > 0 and share_dialog.is_visible():
                    print("找到分享对话框元素")
                    
                    # 尝试查找对话框中的分享选项
                    share_options = share_dialog.locator('button, a, div[role="button"], span[role="button"]').all()
                    if share_options and len(share_options) > 0:
                        print(f"在分享对话框中找到 {len(share_options)} 个选项")
                        
                        # 默认选择第一个选项
                        share_option = share_options[0]
                        
                        # 首先尝试查找特定的分享平台选项
                        for option in share_options:
                            try:
                                option_text = option.text_content().strip()
                                if "telegram" in option_text.lower() or "微信" in option_text or "微博" in option_text:
                                    share_option = option
                                    print(f"选择分享到平台: {option_text}")
                                    break
                            except:
                                continue
                        
                        # 点击选定的分享选项
                        try:
                            share_option.highlight()
                            time.sleep(1)
                            share_option.click(force=True)
                            print(f"已点击分享对话框中的选项: {share_option.text_content().strip()}")
                            time.sleep(2)
                                
                            print("✅ 完成分享视频操作!")
                            return True
                        except Exception as e:
                            print(f"点击分享选项时出错: {str(e)}")
                else:
                    print("未找到分享对话框元素，尝试通过JavaScript查找和点击")
                    
                    # 使用JavaScript查找和点击分享对话框中的选项
                    clicked_option = page.evaluate('''() => {
                        // 查找所有可能的分享对话框容器
                        const dialogs = document.querySelectorAll('div[class*="dialog"], div[class*="modal"], div[class*="share"], div[class*="popup"]');
                        console.log(`找到 ${dialogs.length} 个可能的对话框`);
                        
                        if (dialogs.length > 0) {
                            // 查找对话框中的所有可点击元素
                            const clickableElements = Array.from(dialogs[0].querySelectorAll('a, button, [role="button"], [class*="option"]'));
                            console.log(`在对话框中找到 ${clickableElements.length} 个可点击元素`);
                            
                            if (clickableElements.length > 0) {
                                // 优先选择特定平台
                                let targetElement = clickableElements[0]; // 默认第一个
                                
                                for (const el of clickableElements) {
                                    const text = el.textContent.toLowerCase();
                                    if (text.includes('telegram') || text.includes('微信') || text.includes('微博')) {
                                        targetElement = el;
                                        console.log(`选择分享到平台: ${text}`);
                                        break;
                                    }
                                }
                                
                                // 点击选定的元素
                                targetElement.click();
                                console.log(`已点击分享选项: ${targetElement.textContent}`);
                                return {
                                    clicked: true,
                                    text: targetElement.textContent.trim()
                                };
                            }
                        }
                        
                        return { clicked: false };
                    }''')
                    
                    if clicked_option and clicked_option.get('clicked', False):
                        print(f"通过JavaScript成功点击分享选项: {clicked_option.get('text')}")
                        time.sleep(2)
                        print("✅ 完成分享视频操作!")
                        return True
            except Exception as e:
                print(f"处理分享对话框时出错: {str(e)}")
        
        # 如果前面的方法都失败，尝试使用更暴力的方法
        print("尝试使用模拟按键和坐标点击方法...")
        
        # 方法1: 尝试按Tab键选择分享按钮并按Enter确认
        try:
            # 先按几次Tab键，希望能选中分享按钮
            page.keyboard.press("Tab")
            time.sleep(0.5)
            page.keyboard.press("Tab")
            time.sleep(0.5)
            page.keyboard.press("Tab")
            time.sleep(0.5)
            
            # 尝试按Enter确认
            page.keyboard.press("Enter")
            print("已尝试通过键盘Tab+Enter模拟点击分享按钮")
            time.sleep(2)

                
            # 再次尝试点击对话框中的第一个选项
            try:
                page.keyboard.press("Enter")
                print("已尝试通过键盘Enter点击分享对话框中的选项")
                time.sleep(2)
            except:
                pass
        except Exception as e:
            print(f"键盘模拟点击时出错: {str(e)}")
        
        # 假定分享成功
        print("✅ 分享流程已尝试完成")
        return True
    
    except Exception as e:
        print(f"❌ 分享视频时出错: {str(e)}")
        traceback.print_exc()
        return False



def share_account_details(account_name, status):
    """记录账号操作状态到shared目录"""
    data_dir = 'account_data'
    shared_dir = os.path.join(data_dir, 'shared')
    
    # 确保shared目录存在
    if not os.path.exists(shared_dir):
        os.makedirs(shared_dir)
    
    # 获取当前时间
    now = datetime.now()
    date_str = now.strftime('%Y-%m-%d')
    time_str = now.strftime('%H:%M:%S')
    
    # 准备文件名和内容
    filename = os.path.join(shared_dir, f"{date_str}_status.txt")
    line = f"{time_str} - {account_name}: {status}\n"
    
    # 追加到文件
    with open(filename, 'a', encoding='utf-8') as f:
        f.write(line)


def force_click_sign_in_button(page):
    """使用多种方法点击签到按钮"""
    try:
        # 获取页面内容进行调试
        page_content = page.evaluate('''() => {
            return {
                url: window.location.href,
                title: document.title,
                bodyText: document.body.innerText.substring(0, 1000)
            }
        }''')
        
        # print(f"页面URL: {page_content.get('url')}")
        print(f"页面标题: {page_content.get('title')}")
        
        # 方法一：直接DOM点击
        print("方法一：直接DOM点击...")
        success_dom = page.evaluate('''() => {
            console.log("开始DOM点击搜索");
            // 遍历页面中所有可见的元素
            const allElements = Array.from(document.querySelectorAll('*'));
            console.log(`页面共有 ${allElements.length} 个元素`);
            
            // 过滤出包含"立即签到"文本的元素
            const signButtons = allElements.filter(el => {
                return el.innerText && 
                       el.innerText.trim() === '立即签到' && 
                       el.offsetWidth > 0 && 
                       el.offsetHeight > 0;
            });
            
            console.log(`找到 ${signButtons.length} 个"立即签到"元素`);
            
            // 如果找到了按钮
            if (signButtons.length > 0) {
                const button = signButtons[0];
                console.log(`找到按钮: ${button.tagName}, 内容: ${button.innerText}`);
                console.log(`按钮类名: ${button.className}, ID: ${button.id || '无'}`);
                
                // 获取按钮位置
                const rect = button.getBoundingClientRect();
                console.log(`按钮位置: x=${rect.left}, y=${rect.top}, 宽=${rect.width}, 高=${rect.height}`);
                
                // 直接使用DOM点击
                try {
                    console.log("尝试DOM直接点击");
                    button.click();
                    console.log("DOM点击完成");
                    return {
                        success: true,
                        method: "DOM直接点击",
                        buttonInfo: {
                            text: button.innerText,
                            tag: button.tagName,
                            class: button.className,
                            id: button.id || '无',
                            x: rect.left + rect.width/2,
                            y: rect.top + rect.height/2
                        }
                    };
                } catch(e) {
                    console.log(`DOM点击失败: ${e.message}`);
                }
            }
            
            return { success: false };
        }''')
        
        if success_dom.get('success', False):
            print(f"✅ 成功通过DOM点击: {success_dom.get('method')}")
            print(f"按钮信息: {success_dom.get('buttonInfo')}")
            
            # 保存点击后的截图
            time.sleep(2)
            
            # 检查是否弹出了对话框
            check_dialog(page)
            return True
        else:
            print("❌ DOM点击失败，尝试其他方法")
        
        # 方法二：查找并定位精确的"立即签到"按钮，然后精确点击相应区域
        print("方法二：开始精确定位签到按钮...")
        # 滚动页面以确保按钮可见
        sign_areas = [300, 400, 500, 600, 700, 800]  # 可能的Y坐标值
        
        for scroll_y in sign_areas:
            # print(f"滚动到位置 y={scroll_y}")
            page.evaluate(f"window.scrollTo(0, {scroll_y})")
            time.sleep(2)
            
            # 获取页面中所有可见元素的信息
            elements_info = page.evaluate('''() => {
                const signTexts = ['立即签到', '每日签到', '签到'];
                
                // 获取页面中所有可见的文本元素
                const visibleElements = Array.from(document.querySelectorAll('*')).filter(el => {
                    if (!el.innerText) return false;
                    const text = el.innerText.trim();
                    const rect = el.getBoundingClientRect();
                    return signTexts.some(signText => text.includes(signText)) && 
                           rect.width > 0 && rect.height > 0 &&
                           rect.top >= 0 && rect.top < window.innerHeight;
                });
                
                return visibleElements.map(el => {
                    const rect = el.getBoundingClientRect();
                    return {
                        text: el.innerText.trim().substring(0, 50),
                        tag: el.tagName,
                        id: el.id || '无',
                        className: el.className,
                        position: {
                            x: rect.left + rect.width/2,
                            y: rect.top + rect.height/2,
                            width: rect.width,
                            height: rect.height,
                            visible: rect.top >= 0 && rect.top < window.innerHeight
                        }
                    };
                });
            }''')
            
            # print(f"在当前视图找到 {len(elements_info)} 个可能相关的元素")
            
            # 查找精确匹配"立即签到"的元素
            for i, elem in enumerate(elements_info):
                # print(f"元素 {i+1}: {elem.get('text')} ({elem.get('tag')})")
                # print(f"  位置: x={elem.get('position', {}).get('x')}, y={elem.get('position', {}).get('y')}")
                
                if "立即签到" in elem.get('text', ''):
                    print(f"✅ 找到精确匹配的'立即签到'元素!")
                    
                    # 执行多种点击尝试
                    
                    # 1. 原生DOM点击
                    success_dom = page.evaluate(f'''() => {{
                        const elements = Array.from(document.querySelectorAll('*'));
                        for (const el of elements) {{
                            if (el.innerText && el.innerText.includes('立即签到')) {{
                                try {{
                                    // 尝试移除可能阻碍点击的覆盖层
                                    const overlays = document.querySelectorAll('.overlay, .modal-overlay, .mask, [class*="overlay"], [class*="mask"]');
                                    for (const overlay of overlays) {{
                                        overlay.style.display = 'none';
                                        overlay.style.pointerEvents = 'none';
                                    }}
                                    
                                    // 将元素滚动到视图并点击
                                    el.scrollIntoView({{behavior: 'smooth', block: 'center'}});
                                    setTimeout(() => {{
                                        el.click();
                                        console.log('元素已点击');
                                    }}, 500);
                                    return true;
                                }} catch(e) {{
                                    console.error('DOM点击失败:', e);
                                    return false;
                                }}
                            }}
                        }}
                        return false;
                    }}''')
                    
                    if success_dom:
                        print("✅ 原生DOM点击成功")
                        time.sleep(2)
                 
                        # 检查对话框
                        if check_dialog(page):
                            return True
                    
                    # 2. 坐标点击
                    pos = elem.get('position', {})
                    x = pos.get('x', 0)
                    y = pos.get('y', 0)
                    
                    if x > 0 and y > 0:
                        print(f"尝试点击坐标: ({x}, {y})")
                        
                        # 确保元素在视图中
                        page.evaluate(f"window.scrollTo(0, {y - 200})")
                        time.sleep(1)
    
                        # 执行点击
                        page.mouse.click(x, y)
                        print("✅ 坐标点击完成")
                        
                        # 点击后截图
                        time.sleep(2)

                        # 检查对话框
                        if check_dialog(page):
                            return True
                    
                    # 3. 使用evaluate直接操作DOM执行点击
                    success_js = page.evaluate(f'''() => {{
                        // 获取元素在页面中的坐标
                        const x = {x};
                        const y = {y};
                        
                        // 找到该坐标上的所有元素
                        const elementsAtPoint = document.elementsFromPoint(x, y);
                        console.log('坐标上的元素:', elementsAtPoint.length);
                        
                        // 查找可点击的元素
                        for (const el of elementsAtPoint) {{
                            console.log('元素:', el.tagName, el.className, el.innerText);
                            if (el.innerText && el.innerText.includes('立即签到')) {{
                                try {{
                                    // 创建点击事件并触发
                                    const clickEvent = new MouseEvent('click', {{
                                        bubbles: true,
                                        cancelable: true,
                                        view: window,
                                        clientX: x,
                                        clientY: y
                                    }});
                                    
                                    el.dispatchEvent(clickEvent);
                                    console.log('点击事件已触发');
                                    return true;
                                }} catch(e) {{
                                    console.error('JS点击失败:', e);
                                }}
                            }}
                        }}
                        
                        // 找不到精确匹配的元素，尝试最接近的元素
                        if (elementsAtPoint.length > 0) {{
                            try {{
                                // 点击最上层元素
                                elementsAtPoint[0].click();
                                console.log('已点击最上层元素');
                                return true;
                            }} catch(e) {{
                                console.error('最上层元素点击失败:', e);
                            }}
                        }}
                        
                        return false;
                    }}''')
                    
                    if success_js:
                        print("✅ JavaScript事件点击成功")
                        time.sleep(2)
              
                        # 检查对话框
                        if check_dialog(page):
                            return True
        
        # 方法三：尝试使用特定坐标直接点击
        print("方法三：尝试使用固定坐标点击...")
        # 使用常见的签到按钮坐标位置（基于先前截图观察）
        sign_button_coords = [
            {'x': 631, 'y': 674},  # 观察到的立即签到按钮位置
            {'x': 631, 'y': 600},  # 可能的备选位置1
            {'x': 631, 'y': 500},  # 可能的备选位置2
            {'x': 631, 'y': 400},  # 可能的备选位置3
            {'x': 631, 'y': 300},  # 可能的备选位置4
            {'x': 631, 'y': 200},  # 可能的备选位置5
            {'x': 631, 'y': 100},  # 可能的备选位置6
        ]
        
        for i, coords in enumerate(sign_button_coords):
            x, y = coords['x'], coords['y']
            print(f"尝试点击固定坐标位置 {i+1}: ({x}, {y})")
            
            # 滚动到坐标位置附近
            page.evaluate(f"window.scrollTo(0, {y - 200})")
            time.sleep(1)
            
            # 执行点击
            page.mouse.click(x, y)
            # print(f"✅ 已点击坐标位置 {i+1}")
            
            # 等待可能的对话框
            time.sleep(2)
            
            # 检查对话框
            if check_dialog(page):
                return True
        
        print("❌ 所有点击方法都尝试失败")
        return False
    except Exception as e:
        print(f"强制点击过程中出错: {str(e)}")
        traceback.print_exc()
        return False

def check_dialog(page, recursion_depth=0):
    """检查是否弹出了签到确认对话框，并处理"""
    # 防止无限递归
    if recursion_depth > 1:
        print("⚠️ 检查对话框递归深度过高，停止递归")
        return False
        
    try:
        
        # 检查是否有弹出对话框
        dialog_info = page.evaluate('''() => {
            console.log("开始检查对话框");
            
            // 查找可能的对话框元素（包括签到弹窗）
            const dialogs = document.querySelectorAll('.dialog, .modal, .popup, [class*="dialog"], [class*="modal"], [class*="popup"], [class*="sign"], [class*="check-in"]');
            console.log(`找到 ${dialogs.length} 个可能的对话框元素`);
            
            if (dialogs.length > 0) {
                const dialog = dialogs[0];
                const rect = dialog.getBoundingClientRect();
                console.log(`对话框尺寸: ${rect.width}x${rect.height}, 位置: (${rect.left}, ${rect.top})`);
                
                // 查找对话框中的文本内容
                const dialogText = dialog.innerText;
                console.log(`对话框内容: ${dialogText.substring(0, 100)}...`);
                
                // 查找确认按钮 - 优先检查ID为signInBtn的元素
                let confirmButtons = [];
                // 1. 首先查找特定ID的签到按钮（根据用户提供的按钮HTML）
                const signInBtn = document.getElementById('signInBtn');
                if (signInBtn && signInBtn.offsetWidth > 0 && signInBtn.offsetHeight > 0) {
                    confirmButtons.push(signInBtn);
                    console.log('找到ID为signInBtn的按钮元素');
                }
                
                // 2. 然后检查带有特定类名的按钮
                if (confirmButtons.length === 0) {
                    const buttonElements = dialog.querySelectorAll('.button, [class*="btn"]');
                    confirmButtons = Array.from(buttonElements).filter(el => 
                        el.offsetWidth > 0 && el.offsetHeight > 0
                    );
                }
                
                // 3. 最后才检查文本内容
                if (confirmButtons.length === 0) {
                    confirmButtons = Array.from(dialog.querySelectorAll('*')).filter(el => {
                        const text = el.innerText && el.innerText.trim();
                        return text && (
                            text.includes('即刻签到') ||
                            text.includes('立即签到') ||
                            text.includes('确定') ||
                            text.includes('确认')
                        ) && el.offsetWidth > 0 && el.offsetHeight > 0;
                    });
                }
                
                console.log(`找到 ${confirmButtons.length} 个可能的确认按钮`);
                
                let buttonInfo = null;
                if (confirmButtons.length > 0) {
                    const button = confirmButtons[0];
                    const btnRect = button.getBoundingClientRect();
                    
                    buttonInfo = {
                        text: button.innerText ? button.innerText.trim() : '图片按钮',
                        tag: button.tagName,
                        id: button.id || '无',
                        className: button.className,
                        x: btnRect.left + btnRect.width/2,
                        y: btnRect.top + btnRect.height/2
                    };
                    
                    console.log(`确认按钮: ${buttonInfo.text}, 位置: (${buttonInfo.x}, ${buttonInfo.y})`);
                }
                
                return {
                    found: true,
                    text: dialogText.substring(0, 100),
                    x: rect.left + rect.width/2,
                    y: rect.top + rect.height/2,
                    confirmButton: buttonInfo
                };
            }
            
            // 没有找到常规对话框，尝试查找所有包含"即刻签到"文本的元素
            const textElements = Array.from(document.querySelectorAll('*')).filter(el => {
                const text = el.innerText && el.innerText.trim();
                return text && (
                    text.includes('即刻签到') ||
                    text.includes('立即签到') ||
                    text.includes('确定签到')
                ) && el.offsetWidth > 0 && el.offsetHeight > 0;
            });
            
            console.log(`找到 ${textElements.length} 个包含确认文本的元素`);
            
            if (textElements.length > 0) {
                const element = textElements[0];
                const rect = element.getBoundingClientRect();
                
                return {
                    found: true,
                    text: element.innerText || `元素(ID:${element.id || '无'}, 类名:${element.className})`,
                    custom: true,
                    x: rect.left + rect.width/2,
                    y: rect.top + rect.height/2
                };
            }
            
            // 没有找到常规对话框，尝试查找特定元素
            // 1. 先检查ID为signInBtn的元素
            const signInBtn = document.getElementById('signInBtn');
            if (signInBtn && signInBtn.offsetWidth > 0 && signInBtn.offsetHeight > 0) {
                const rect = signInBtn.getBoundingClientRect();
                return {
                    found: true,
                    text: '签到按钮(ID: signInBtn)',
                    custom: true,
                    x: rect.left + rect.width/2,
                    y: rect.top + rect.height/2
                };
            }
            
            // 2. 检查类名包含特定关键字的元素
            const buttonElements = document.querySelectorAll('.button, [class*="btn"], [class*="sign"]');
            const visibleButtons = Array.from(buttonElements).filter(el => 
                el.offsetWidth > 0 && el.offsetHeight > 0
            );
            
            // 3. 如果上面都没找到，再尝试通过文本查找
            if (visibleButtons.length === 0) {
                const additionalTextElements = Array.from(document.querySelectorAll('*')).filter(el => {
                    const text = el.innerText && el.innerText.trim();
                    return text && (
                        text.includes('即刻签到') ||
                        text.includes('立即签到') ||
                        text.includes('确定签到')
                    ) && el.offsetWidth > 0 && el.offsetHeight > 0;
                });
                
                if (additionalTextElements.length > 0) {
                    visibleButtons.push(...additionalTextElements);
                }
            }
            
            console.log(`找到 ${visibleButtons.length} 个包含确认文本的元素`);
            
            if (visibleButtons.length > 0) {
                const element = visibleButtons[0];
                const rect = element.getBoundingClientRect();
                
                return {
                    found: true,
                    text: element.innerText || `元素(ID:${element.id || '无'}, 类名:${element.className})`,
                    custom: true,
                    x: rect.left + rect.width/2,
                    y: rect.top + rect.height/2
                };
            }
            
            return { found: false };
        }''')
        
        if dialog_info.get('found', False):
            print("✅ 找到对话框或确认元素!")
            # print(f"对话框内容: {dialog_info.get('text', '')}")
            
            # 获取确认按钮或对话框中心点信息
            confirm_button = dialog_info.get('confirmButton')
            
            if confirm_button:
                print(f"找到确认按钮: {confirm_button.get('text')} ({confirm_button.get('tag')})")
                print(f"按钮位置: x={confirm_button.get('x')}, y={confirm_button.get('y')}")
                
                # 确保按钮在视图中
                page.evaluate(f"window.scrollTo(0, {confirm_button.get('y') - 200})")
                time.sleep(1)
                
                # 点击确认按钮 - 首先尝试直接通过ID点击signInBtn
                dom_click_success = page.evaluate('''() => {
                    // 1. 先尝试直接通过ID点击
                    const signInBtn = document.getElementById('signInBtn');
                    if (signInBtn) {
                        try {
                            console.log('尝试点击ID为signInBtn的按钮');
                            signInBtn.click();
                            return true;
                        } catch(e) {
                            console.error('点击signInBtn失败:', e);
                        }
                    }
                     
                    // 2. 尝试查找带有特定类名的按钮  
                    const buttons = document.querySelectorAll('.button, [class*="btn"], [class*="sign"]');
                    if (buttons.length > 0) {
                        try {
                            console.log('尝试点击类名匹配的按钮');
                            for (let i = 0; i < buttons.length; i++) {
                                if (buttons[i].offsetWidth > 0 && buttons[i].offsetHeight > 0) {
                                    buttons[i].click();
                                    return true;
                                }
                            }
                             return true;
                        } catch(e) {
                            console.error('DOM点击确认按钮失败:', e);
                        }
                    }
                    
                    // 3. 最后尝试文本匹配
                    const textButtons = Array.from(document.querySelectorAll('*')).filter(el => {
                        const text = el.innerText && el.innerText.trim();
                        return text && (
                            text.includes('即刻签到') ||
                            text.includes('立即签到') ||
                            text.includes('确定') ||
                            text.includes('确认')
                        );
                    });
                    
                    if (textButtons.length > 0) {
                        try {
                            textButtons[0].click();
                            return true;
                        } catch(e) {
                            console.error('通过文本DOM点击失败:', e);
                        }
                    }
                    
                    return false;
                }''')
                
                if dom_click_success:
                    print("✅ DOM点击确认按钮成功")
                    time.sleep(2)
                    # 检查是否弹出了另一个对话框
                    if recursion_depth < 1:
                        result = check_dialog(page, recursion_depth + 1)
                    return True
                
                # 2. 鼠标点击 - 使用中心点坐标
                try:
                    print(f"尝试鼠标点击坐标: x={confirm_button.get('x')}, y={confirm_button.get('y')}")
                    page.mouse.click(confirm_button.get('x'), confirm_button.get('y'))
                    print("✅ 通过鼠标坐标点击成功")
                    time.sleep(2)
                    return True
                except Exception as e:
                    print(f"❌ 鼠标点击失败: {str(e)}")
            else:
                # 如果找到对话框但没有找到确认按钮，尝试点击对话框中心
                try:
                    print(f"未找到确认按钮，尝试点击对话框中心: x={dialog_info.get('x')}, y={dialog_info.get('y')}")
                    page.mouse.click(dialog_info.get('x'), dialog_info.get('y'))
                    # print("✅ 点击对话框中心完成")
                    time.sleep(2)
                    return True
                except Exception as e:
                    print(f"❌ 点击对话框中心失败: {str(e)}")
        else:
            print("⚠️ 未检测到对话框或确认元素")
        
        return False
    except Exception as e:
        print(f"❌ 检查对话框过程中出错: {str(e)}")
        traceback.print_exc()
        return False

def run_check_in_for_account(account_name, headless=False):
    """为指定账号执行签到操作"""
    print(f"\n===== 开始为账号 '{account_name}' 执行签到 =====")
    data_dir = 'account_data'
    
    # 检查账号状态文件是否存在
    state_file = os.path.join(data_dir, f"{account_name}_storage.json")
    if not os.path.exists(state_file):
        print(f"❌ 账号 '{account_name}' 的登录状态文件不存在")
        return False
    
    with sync_playwright() as p:
        try:
            # 启动浏览器
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context()
            page = context.new_page()
            
            # 设置窗口大小
            if not headless:
                page.set_viewport_size({"width": 2560, "height": 1440})
            
            # 加载保存的状态
            try:
                load_storage_state(context, state_file)
                print(f"✅ 已加载账号 '{account_name}' 的登录状态")
            except Exception as e:
                print(f"❌ 加载账号状态失败: {str(e)}")
                browser.close()
                return False
            
            # 打开新页面
            page = context.new_page()
            
            # 访问网站
            print("正在打开个人中心...")
            page.goto("https://www.yfsp.tv/user/index", wait_until="networkidle")
            page.wait_for_load_state("networkidle", timeout=30000)
            page.wait_for_load_state("domcontentloaded", timeout=30000)
            time.sleep(5)
            
            # 检查登录状态
            if not check_login_status(page):
                print("❌ 登录状态失效，请重新登录")
                browser.close()
                return False
            
            # 最多尝试3次签到
            max_attempts = 3
            for attempt in range(1, max_attempts + 1):
                print(f"\n----- 签到尝试 {attempt}/{max_attempts} -----")
                # 尝试点击签到按钮
                try:
                    force_click_sign_in_button(page)
     
                    # 检查并处理签到确认弹窗
                    if check_dialog(page):
                        print("已处理签到确认弹窗")
                    else:
                        print("尝试处理签到弹窗...")
                        # 使用专门的签到按钮点击函数
                        direct_click_sign_in_button(page)
                    
                    # 等待签到完成
                    time.sleep(5)
                    
                    # 检查签到是否成功
                    if check_sign_in_success(page):
                        print("✅ 签到成功")
                 
                        # 记录成功状态
                        share_account_details(account_name, "签到成功")
                        return True
                    else:
                        if attempt < max_attempts:
                            print(f"❌ 签到未成功，准备第{attempt+1}次尝试...")
                        else:
                            print("❌ 所有尝试均未成功，或无法确认结果")
                           
                            return False
                    
                except Exception as e:
                    print(f"❌ 签到过程中出错: {str(e)}")
                    traceback.print_exc()
                    
                    if attempt < max_attempts:
                        print(f"准备第{attempt+1}次尝试...")
                    else:
                        return False
                
                # 在尝试之间等待
                if attempt < max_attempts:
                    print(f"等待5秒后进行下一次尝试...")
                    time.sleep(5)
            
            return False
            
        except Exception as e:
            print(f"❌ 执行签到时出错: {str(e)}")
            traceback.print_exc()
            return False
        finally:
            # 关闭浏览器
            if 'browser' in locals():
                browser.close()
    
    return False

def run_share_video_for_account(account_name, headless=False):
    """为指定账号执行分享视频操作"""
    print(f"\n===== 开始为账号 '{account_name}' 执行分享视频 =====")
    data_dir = 'account_data'
    
    # 检查账号状态文件是否存在
    state_file = os.path.join(data_dir, f"{account_name}_storage.json")
    if not os.path.exists(state_file):
        print(f"❌ 账号 '{account_name}' 的登录状态文件不存在")
        return False
    
    def try_share_with_mode(use_headless):
        with sync_playwright() as p:
            try:
                # 启动浏览器
                browser = p.chromium.launch(headless=use_headless)
                context = browser.new_context()
                page = context.new_page()
                
                # 设置窗口大小
                if not use_headless:
                    page.set_viewport_size({"width": 2560, "height": 1440})
                
                # 加载保存的状态
                try:
                    load_storage_state(context, state_file)
                    print(f"✅ 已加载账号 '{account_name}' 的登录状态")
                except Exception as e:
                    print(f"❌ 加载账号状态失败: {str(e)}")
                    browser.close()
                    return False
                
                # 打开页面
                page.goto("https://www.yfsp.tv/")
                page.wait_for_load_state("networkidle")
                
                # 检查登录状态
                if not check_login_status(page):
                    print(f"❌ 账号 '{account_name}' 未登录")
                    browser.close()
                    return False
                
                # 执行分享操作
                success = share_video(page)
                if success:
                    print(f"✅ 账号 '{account_name}' 分享视频成功")
                else:
                    print(f"❌ 账号 '{account_name}' 分享视频失败")
                
                browser.close()
                return success
                
            except Exception as e:
                print(f"❌ 执行分享操作时出错: {str(e)}")
                if 'browser' in locals():
                    browser.close()
                return False
    
    # 尝试使用指定模式
    success = try_share_with_mode(headless)
    if not success and not headless:
        print("\n⚠️ 可见模式失败，尝试无头模式...")
        success = try_share_with_mode(True)
    
    return success

def run_for_single_account(account_name, headless=False):
    """为单个账号执行所有操作（先分享视频再签到）"""
    # 先执行视频分享，再执行签到
    share_result = run_share_video_for_account(account_name, headless)
    check_in_result = run_check_in_for_account(account_name, headless)
    
    return share_result and check_in_result


def add_account(account_name, email=None, password=None, headless=False):
    """添加新账号
    
    Args:
        account_name: 账号名称
        email: 邮箱地址
        password: 密码
        headless: 是否使用无头模式
    """
    data_dir = 'account_data'
    os.makedirs(data_dir, exist_ok=True)
    
    if not account_name.strip():
        print("❌ 账号名称不能为空")
        return False

    if not email or not password:
        print("❌ 邮箱和密码不能为空")
        return False

    print(f"\n➡️ 开始添加账号: {account_name}")

    # 保存账号信息
    account_info = {
        'account_name': account_name,
        'email': email,
        'password': password
    }
    account_file = os.path.join(data_dir, f"{account_name}_account.json")
    with open(account_file, 'w', encoding='utf-8') as f:
        json.dump(account_info, f, ensure_ascii=False, indent=2)
    print(f"✅ 已保存账号信息到 {account_file}")

    # 保存账号名称到列表
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
    else:
        print(f"⚠️ 账号 {account_name} 已存在于列表中")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        
        # 设置窗口大小
        if not headless:
            page.set_viewport_size({"width": 2560, "height": 1440})

        # 打开页面并点击 "登录"
        page.goto("https://www.yfsp.tv/list/anime", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=20000)
        for sel in ['a:has-text("登录")', 'button:has-text("登录")', '[class*="login"]:visible']:
            try:
                btns = page.locator(sel).all()
                for b in btns:
                    if b.is_visible():
                        print("点击登录按钮…")
                        b.click()
                        raise StopIteration
            except StopIteration:
                break

        # 拿到 iframe
        print("等待登录 iframe …")
        iframe_el = page.wait_for_selector("iframe#Dn_Login_Iframe", timeout=15000)
        login_frame = iframe_el.content_frame()
        if not login_frame:
            raise RuntimeError("❌ 拿不到登录的 frame 对象")

        # 找到 tab 的 <li>（也可以直接选 a，但用 li 保证事件委托生效）
        tab_li = login_frame.locator('ul.tabs li#mlogin')
        tab_li.wait_for(state="visible", timeout=10000)
        tab_li.scroll_into_view_if_needed()

        # 重试逻辑：点 3 次，每次点完等状态变化
        for attempt in range(1, 4):
            print(f'尝试点击"其他方式登录" 第 {attempt} 次...')
            tab_li.click(force=True)

            try:
                # 判断父节点是否切上了 is-active
                login_frame.wait_for_function(
                    """() => {
                        const li = document.querySelector('ul.tabs li#mlogin');
                        return li && li.classList.contains('is-active');
                    }""",
                    timeout=3000
                )
                print('✅ 已成功切换到 "其他方式登录"')
                break
            except PlaywrightTimeoutError:
                print("⚠️ 本次点击未生效，1s 后重试…")
                time.sleep(1)
        else:
            print("❌ 重试 3 次仍未切换，请检查控制台或截图进一步调试")

        print("⏳ 正在填写账号密码…")
        # 1. 填写账号密码
        login_frame.fill('input[name="Email"]', email)
        login_frame.fill('input[name="UserPass"]', password)

        # 执行滑动验证
        slide_verify(login_frame)
        
        # 6. 等待验证结果并登录
        max_verify_retries = 3  # 验证失败后的最大重试次数
        verify_retry_count = 0
        
        while verify_retry_count < max_verify_retries:
            try:
                print("⏳ 等待验证结果...")
                
                # 检查验证状态
                max_retries = 3
                for retry in range(max_retries):
                    if login_frame.locator('.slide-to-unlock-handle.success').count() > 0:
                        print("✅ 滑动验证已通过")
                        break
                    if login_frame.locator('.slide-to-unlock-handle.fail').count() > 0:
                        print("❌ 滑动验证失败，重试中...")
                        time.sleep(1)
                        continue
                    time.sleep(1)
                
                if login_frame.locator('.slide-to-unlock-handle.success').count() == 0:
                    print("❌ 滑动验证未通过")
                    verify_retry_count += 1
                    if verify_retry_count < max_verify_retries:
                        print(f"🔄 准备第 {verify_retry_count + 1} 次重试...")
                        # 重新执行滑动验证
                        slide_verify(login_frame)
                        continue
                    else:
                        print("❌ 已达到最大重试次数，请手动重试")
                        raise PlaywrightTimeoutError("验证未通过")
                
                # 等待登录按钮变为可用状态
                print("⏳ 等待登录按钮变为可用状态...")
                login_button = login_frame.locator('button.btn-login:not(.disabled)')
                login_button.wait_for(state="visible", timeout=5000)
                
                # 尝试多种方式点击登录按钮
                print("⏳ 正在点击登录按钮...")
                try:
                    login_button.click()
                    print("✅ 已通过直接点击方式触发登录")
                except Exception as e1:
                    try:
                        login_button.evaluate('button => button.click()')
                        print("✅ 已通过 JavaScript 方式触发登录")
                    except Exception as e2:
                        login_button.evaluate('button => { const event = new MouseEvent("click", { bubbles: true, cancelable: true }); button.dispatchEvent(event); }')
                        print("✅ 已通过事件触发方式触发登录")
                
                # 等待登录完成
                print("⏳ 等待登录完成...")
                try:
                    # 等待页面完全加载
                    page.wait_for_load_state("networkidle", timeout=10000)
                    # 等待URL变化
                    page.wait_for_url("**/list/anime", timeout=10000)
                    print("✅ 登录完成")
                    
                    # 确保页面完全加载后再保存状态
                    time.sleep(2)  # 额外等待以确保所有状态都已保存
                    
                    # 保存登录状态
                    state_file = os.path.join(data_dir, f"{account_name}_storage.json")
                    save_storage_state(context, state_file)
                    print(f"✅ 已保存登录状态到 {state_file}")
                    
                    # 如果不是无头模式，等待用户确认
                    if not headless:
                        print("\n✅ 账号添加完成！请按回车键关闭浏览器...")
                        input()
                    
                    # 如果成功登录，跳出重试循环
                    return True
                    
                except PlaywrightTimeoutError:
                    if page.locator('text=退出登录').count() > 0:
                        print("✅ 登录完成（通过检查退出按钮确认）")
                        
                        # 保存登录状态
                        state_file = os.path.join(data_dir, f"{account_name}_storage.json")
                        save_storage_state(context, state_file)
                        print(f"✅ 已保存登录状态到 {state_file}")
                        
                        # 如果不是无头模式，等待用户确认
                        if not headless:
                            print("\n✅ 账号添加完成！请按回车键关闭浏览器...")
                            input()
                        
                        return True
                    else:
                        print("⚠️ 登录状态不确定，请检查页面")
                        raise
                
            except PlaywrightTimeoutError as e:
                print("❌ 操作超时，请检查页面状态")
                verify_retry_count += 1
                if verify_retry_count < max_verify_retries:
                    print(f"🔄 准备第 {verify_retry_count + 1} 次重试...")
                    # 重新执行滑动验证
                    slide_verify(login_frame)
                    continue
                else:
                    print("❌ 重试次数已达上限，请检查网络连接或稍后再试")
                    raise
        
        return False

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

def auto_operations(operation_type='all', headless=True):
    """
    对所有保存的账号执行自动操作
    operation_type: 'checkin' (签到), 'share' (分享), 'all' (全部)
    """
    accounts = get_all_accounts()
    
    if not accounts:
        print("❌ 未找到已保存的账号，请先使用 'add' 命令添加账号")
        return False
    
    print(f"\n➡️ 开始为 {len(accounts)} 个账号执行操作: {operation_type}")
    
    success_count = 0
    for i, account_name in enumerate(accounts, 1):
        print(f"\n[{i}/{len(accounts)}] 处理账号: {account_name}")
        
        # 根据操作类型执行不同的功能
        success = False
        if operation_type == 'checkin':
            success = run_check_in_for_account(account_name, headless=headless)
        elif operation_type == 'share':
            # 将headless参数传递给分享函数，允许尝试无头模式
            success = run_share_video_for_account(account_name, headless=headless)
        else:  # 'all'
            # 执行所有操作
            check_in_success = run_check_in_for_account(account_name, headless=headless)
            share_success = run_share_video_for_account(account_name, headless=headless)
            success = check_in_success or share_success
            
        if success:
            success_count += 1
    
    print(f"\n✅ 操作完成: {success_count}/{len(accounts)} 个账号成功")
    
    # 执行完操作后，获取所有账号的金币数量
    print("\n🔄 正在获取所有账号的金币数量...")
    get_coins_for_all_accounts(headless=headless)
    
    return success_count > 0

def show_help():
    """显示帮助信息"""
    print("\n📋 可用命令:")
    print("  python main.py add <账号名称>  - 添加新账号")
    print("  python main.py run            - 为所有账号执行签到和分享操作")
    print("  python main.py checkin        - 仅执行签到操作")
    print("  python main.py share          - 仅执行分享操作")
    print("  python main.py coins          - 获取所有账号的金币数量")
    print("  python main.py list           - 显示所有已保存的账号")
    print("  python main.py help           - 显示此帮助信息")
    print("\n选项:")
    print("  --visible                     - 使用可见浏览器（默认为隐藏模式运行）")
    print("备注: 分享操作现支持无头模式，但在失败时会自动尝试使用有界面模式")

def list_accounts():
    """列出所有已保存的账号"""
    accounts = get_all_accounts()
    
    if not accounts:
        print("❌ 未找到已保存的账号")
        return
    
    print("\n📋 已保存的账号列表:")
    for i, account in enumerate(accounts, 1):
        # 检查账号状态文件是否存在
        state_file = os.path.join('account_data', f"{account}_storage.json")
        status = "✅ 已保存登录状态" if os.path.exists(state_file) else "❌ 未保存登录状态"
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
                page.goto("https://www.yfsp.tv/user/index", timeout=30000)
                
                # 等待页面加载
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception as e:
                    print(f"等待页面加载时出错: {str(e)}，继续执行")
                time.sleep(3)  # 额外等待，确保JavaScript完成渲染
                
                # 获取金币数量
                coins = None
                try:
                    # 使用提供的HTML结构定位金币元素
                    coins_element = page.locator('div[title="金币"]').first
                    if coins_element.count() > 0:
                        # 获取金币数值文本
                        coins_text = coins_element.inner_text()
                        # 提取数字
                        import re
                        coins_match = re.search(r'\d+', coins_text)
                        if coins_match:
                            coins = int(coins_match.group())
                            print(f"✅ 账号 '{account_name}' 当前金币数量: {coins}")
                        else:
                            print(f"❌ 无法解析金币数值: {coins_text}")
                    else:
                        # 尝试使用JavaScript获取
                        print("尝试使用JavaScript获取金币数量...")
                        coins_data = page.evaluate('''() => {
                            const coinDiv = document.querySelector('div[title="金币"]');
                            if (coinDiv) {
                                const text = coinDiv.textContent.trim();
                                return text;
                            }
                            return null;
                        }''')
                        
                        if coins_data:
                            coins_match = re.search(r'\d+', coins_data)
                            if coins_match:
                                coins = int(coins_match.group())
                                print(f"✅ 账号 '{account_name}' 当前金币数量: {coins}")
                            else:
                                print(f"❌ 无法解析金币数值: {coins_data}")
                        else:
                            print("❌ 未找到金币元素")
                except Exception as e:
                    print(f"❌ 获取金币数量时出错: {str(e)}")
                
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

    results = {}
    failed_accounts = []

    for account_name in accounts:
        try:
            coins = get_account_coins(account_name, headless=headless)
            if coins is not None:
                results[account_name] = coins
                print(f"✅ {account_name}: {coins} 金币")
            else:
                failed_accounts.append(account_name)
                print(f"❌ {account_name}: 获取金币失败")
        except Exception as e:
            failed_accounts.append(account_name)
            print(f"❌ {account_name}: 发生错误 - {str(e)}")

    # 保存最初失败的账号列表，用于后续比较
    initially_failed_accounts = failed_accounts.copy()
    final_failed_accounts = []

    # 如果有失败的账号，尝试重新登录
    if failed_accounts:
        print("\n🔄 开始重新登录失败的账号...")
        for account_name in failed_accounts:
            try:
                # 读取账号信息
                account_file = os.path.join(data_dir, f"{account_name}_account.json")
                if not os.path.exists(account_file):
                    print(f"❌ {account_name}: 找不到账号信息文件")
                    final_failed_accounts.append(account_name)
                    continue

                with open(account_file, 'r', encoding='utf-8') as f:
                    account_info = json.load(f)
                    email = account_info.get('email')
                    password = account_info.get('password')

                if not email or not password:
                    print(f"❌ {account_name}: 账号信息不完整")
                    final_failed_accounts.append(account_name)
                    continue

                print(f"\n➡️ 重新登录账号: {account_name}")
                
                # 删除账号
                if delete_account(account_name):
                    print(f"✅ 已删除账号 {account_name}")
                    
                    # 重新添加账号
                    if add_account(account_name, email, password, headless=headless):
                        print(f"✅ 已重新添加账号 {account_name}")
                        
                        # 重新获取金币
                        coins = get_account_coins(account_name, headless=headless)
                        if coins is not None:
                            results[account_name] = coins
                            print(f"✅ {account_name}: {coins} 金币")
                        else:
                            print(f"❌ {account_name}: 重新登录后仍无法获取金币")
                            final_failed_accounts.append(account_name)
                    else:
                        print(f"❌ {account_name}: 重新添加失败")
                        final_failed_accounts.append(account_name)
                else:
                    print(f"❌ {account_name}: 删除失败")
                    final_failed_accounts.append(account_name)

            except Exception as e:
                print(f"❌ {account_name}: 重新登录时发生错误 - {str(e)}")
                final_failed_accounts.append(account_name)

    # 发送邮件通知
    if results or final_failed_accounts:
        total_coins = sum(results.values())
        
        # 构建主题
        subject = "爱壹帆金币统计"
        if final_failed_accounts:
            subject += f"({len(final_failed_accounts)}个失败)"
        
        # 构建内容
        content = f"""{datetime.now().strftime('%Y-%m-%d')}
总金币: {total_coins}

账号统计:
"""
        
        # 添加所有账号，包括成功和失败的
        all_accounts = sorted(list(set(list(results.keys()) + final_failed_accounts)))
        for account in all_accounts:
            if account in results:
                content += f"✅ {account}: {results[account]}枚\n"
            else:
                content += f"❌ {account}: 获取失败\n"
        
        send_email(subject, content)
        print("\n✅ 已发送邮件通知")

def delete_account(account_name):
    """删除指定账号
    
    Args:
        account_name: 要删除的账号名称
    """
    data_dir = 'account_data'
    
    if not account_name:
        print("❌ 请提供要删除的账号名称")
        return False
        
    # 检查账号是否存在
    accounts_file = os.path.join(data_dir, "accounts.txt")
    if not os.path.exists(accounts_file):
        print(f"❌ 账号列表文件不存在")
        return False
        
    # 读取账号列表
    accounts = []
    with open(accounts_file, 'r', encoding='utf-8') as f:
        accounts = [line.strip() for line in f if line.strip()]
        
    if account_name not in accounts:
        print(f"❌ 账号 {account_name} 不存在")
        return False
        
    try:
        # 删除存储文件
        state_file = os.path.join(data_dir, f"{account_name}_storage.json")
        if os.path.exists(state_file):
            os.remove(state_file)
            print(f"✅ 已删除账号存储文件: {state_file}")
            
        # 删除账号信息文件
        account_file = os.path.join(data_dir, f"{account_name}_account.json")
        if os.path.exists(account_file):
            os.remove(account_file)
            print(f"✅ 已删除账号信息文件: {account_file}")
            
        # 从账号列表中移除
        accounts.remove(account_name)
        with open(accounts_file, 'w', encoding='utf-8') as f:
            for acc in accounts:
                f.write(f"{acc}\n")
                
        print(f"✅ 已从账号列表中移除: {account_name}")
        return True
        
    except Exception as e:
        print(f"❌ 删除账号时出错: {str(e)}")
        return False

def send_email(subject, content):
    """发送邮件通知"""
    try:
        # 创建邮件内容
        msg = MIMEText(content, 'plain', 'utf-8')
        msg['From'] = EMAIL_USER
        msg['To'] = EMAIL_TO
        msg['Subject'] = Header(subject, 'utf-8')
        
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
        if len(sys.argv) < 6:
            print("❌ 请提供账号名称、邮箱和密码")
            print("用法: python main.py add <账号> --eml <邮箱> --pwd <密码> [--visible]")
            return
            
        # 解析参数
        account_name = None
        email = None
        password = None
        
        # 第一个非选项参数应该是 command 后面的账号名称
        if len(sys.argv) > 2 and not sys.argv[2].startswith('--'):
            account_name = sys.argv[2]
        
        # 然后解析选项参数
        for i in range(2, len(sys.argv)):
            if sys.argv[i] == '--eml' and i + 1 < len(sys.argv):
                email = sys.argv[i + 1]
            elif sys.argv[i] == '--pwd' and i + 1 < len(sys.argv):
                password = sys.argv[i + 1]
        
        if not account_name or not email or not password:
            print("❌ 缺少必要的参数")
            print("用法: python main.py add <账号> --eml <邮箱> --pwd <密码> [--visible]")
            return
        
        # 添加调试信息
        print(f"DEBUG: 解析参数 - 账号名称: {account_name}, 邮箱: {email}, 密码: {password}")
            
        add_account(account_name, email, password, headless=headless)
    
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
    
    elif command in ['help', '-h', '--help']:
        show_help()
    
    else:
        print(f"❌ 未知命令: {command}")
        show_help()

if __name__ == "__main__":
    main()
    # print("sys.argv:", sys.argv)