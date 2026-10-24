# (爱壹帆)多账号自动签到与分享自动化工具

本项目基于 Python + Playwright，支持多账号自动化登录、签到、视频分享、金币统计、账号管理等功能，适合定时任务和云端自动运行。

## 功能特点

- ✅ 多账号自动管理（添加、删除、列表、忽略名单）
- ✅ 半自动高效登录（仅需手动滑动验证，无需其他操作）
- ✅ 支持 `auto` 连续批量登录（一键顺次登录后续所有账号）
- ✅ 自动保存/恢复会话登录状态（Cookie / LocalStorage）
- ✅ 自动签到与每日视频分享
- ✅ 获取所有账号当前金币明细与汇总统计
- ✅ 支持无头（后台静默）与可见浏览器模式
- ✅ **智能邮件通知策略**（有账号异常/失败时每日即刻报警；全成功仅在周一例行周报）
- ✅ 完整的本地状态日志与历史执行记录

## 安装

### 1. 安装依赖

```bash
pip install playwright
```

### 2. 安装浏览器驱动

```bash
playwright install chromium
```

## 使用方法

### 添加 / 保存邮箱、密码

把邮箱、密码信息保存在 `account_data/account.json`，账号名称自动使用邮箱前缀。

```bash
python main.py add <邮箱> <密码>
```

---

### 登录账号（支持单账号与连续批量登录）

`login` 命令会从 `account_data/account.json` 自动读取该账号的邮箱和密码并自动填入登录框。

#### 1. 登录单个账号：
```bash
python main.py login <账号名>
```
*如需延长等待时间，可添加 `--timeout 秒数`（默认 180 秒）：*
```bash
python main.py login <账号名> --timeout 300
```

#### 2. 连续批量登录后续所有账号（`auto` 参数）：
当添加 `auto` 参数时，脚本在当前账号登录成功后会自动流转到下一个账号，直到 `account.json` 中的最后一个账户：
```bash
# 从 yifan3 开始，自动顺次登录 yifan3、yifan4... 直到 account.json 最后一个账号
python main.py login yifan3 auto

# 从 account.json 第一个账号开始连续登录全部账号
python main.py login auto
```

💡 **极致半自动登录体验：**
1. **系统提示音提醒**：登录框打开、表单自动填写完毕、且滑块组件完全加载就绪后，系统会**自动播放清脆的提示音**。用户无需一直盯屏，听到声音再切过来操作；
2. **仅需拖动滑块**：用户只需拖动滑块到最右侧完成滑动验证；
3. **自动识别并代点登录**：脚本会自动捕获成功凭据并**自动点击「登录」按钮**；
4. **全自动保存与流转**：脚本自动检测登录态、保存 Cookie 凭据到 `account_data/<账号名>_storage.json` 并自动关闭浏览器，倒数 2 秒后自动拉起下一个账号！

---

### 删除账号
```bash
python main.py delete <账号名>
```

### 列出所有账号
```bash
python main.py list
```

### 忽略 / 取消忽略账号

如果某账号需要从批量操作中排除，可以加入忽略名单；在取消忽略前，`run`、`checkin`、`share`、`coins` 批量操作都会跳过该账号：

```bash
python main.py ignore <账号名>
python main.py unignore <账号名>
python main.py ignored
```

### 为所有账号执行签到和分享

```bash
python main.py run [--visible]
```

### 仅签到

```bash
python main.py checkin [--visible]
```

### 仅分享

```bash
python main.py share [--visible]
```

### 获取所有账号金币统计

```bash
python main.py coins [--visible]
```

---

### 邮件通知与金币汇总（可选）

如需开启邮件通知功能，只需在 `account_data/` 目录下创建 `email_config.json` 文件：

```json
{
  "EMAIL_HOST": "smtp.qq.com",
  "EMAIL_PORT": 465,
  "EMAIL_USER": "your_email@qq.com",
  "EMAIL_PASS": "邮箱SMTP授权码",
  "EMAIL_TO": "receiver@example.com"
}
```

**智能通知策略：**
* 🚨 **异常报警**：任务中只要存在登录态失效或获取金币失败的账号，**每天均会强制发送邮件**，提醒您及时执行 `login` 刷新凭证；
* 📊 **例行周报**：如果所有账号全部执行成功且无报错，脚本**仅在每周一例行发送邮件**进行金币汇总通知，避免日常垃圾邮件打扰。

---

### 日志查看与历史状态

* **邮件与系统运行日志**：
  * 文件位置：[aiyifan.log]
  * 记录内容：邮件发送记录、SMTP 报错、系统关键异常堆栈。
* **每日详细操作流水**：
  * 目录位置：[account_data/shared/]
  * 每日自动生成 `YYYY-MM-DD_status.txt`，详细记录当天各账号何时成功执行了“签到”与“分享”。

---

### 查看命令帮助

```bash
python main.py help
```

## 定时任务/云端部署建议

- 推荐使用系统 Cron 或 Launchd 配置定时每日凌晨执行 `python main.py run`；
- 建议配合住宅/稳定原生 IP 运行，以降低网站人机验证频率与 Session 失效风险。

## 数据与安全

- 账号邮箱与密码保存在 `account_data/account.json`，登录态保存在 `account_data/*_storage.json`，账号列表在 `accounts.txt`，忽略名单在 `ignored_accounts.txt`，请妥善保管。
- 邮箱推送需配置 SMTP 信息，建议使用专用邮箱或生成独立应用授权码。

## 故障排查

- **提示「需要手动登录」**：爱壹帆服务端的 Session Token 通常有 1~3 个月的有效期，过期失效后只需执行 `python main.py login <账号名> auto` 快速刷新登录态；
- **排查具体失败原因**：查看项目根目录下的 `aiyifan.log` 以及 `account_data/shared/` 当天的状态文件。
