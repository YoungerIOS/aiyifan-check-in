# (爱壹帆)多账号自动签到与分享自动化工具

本项目基于 Python + Playwright，支持多账号自动化登录、签到、视频分享、金币统计、账号管理等功能，适合定时任务和云端自动运行。

## 功能特点

- ✅ 多账号自动管理（添加、删除、列出账号）
- ✅ 自动保存/恢复登录状态
- ✅ 自动签到
- ✅ 自动分享视频
- ✅ 获取所有账号金币统计
- ✅ 支持无头/可见浏览器模式
- ✅ 支持定时任务和云端部署
- ✅ 支持每周自动邮件推送金币统计（可选）

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

随后在浏览器里完成登录并保存状态（`login` 会从 `account.json` 读取凭据用于自动填表，只需手动完成滑动验证）：

```bash
python main.py login <邮箱前缀>
```

登录成功后脚本会自动检测、保存登录态并关闭浏览器；如需延长等待时间可使用：

```bash
python main.py login <邮箱前缀> --timeout 300
```

### 删除账号
```bash
python main.py delete <邮箱前缀>
```

如果不确定账号名称，可以先执行：`python main.py list`

### 列出所有账号

```bash
python main.py list
```

### 忽略 / 取消忽略账号

如果某账号需要从批量操作中排除，可以加入忽略名单；在取消忽略前，`run`、`checkin`、`share`、`coins` 批量操作都会跳过该账号。

```bash
python main.py ignore <账号名称>
python main.py unignore <账号名称>
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

### 自动推送金币统计到邮箱（需配置邮箱参数）


### 查看帮助

```bash
python main.py help
```

## 定时任务/云端部署建议

- 推荐使用 Docker 部署，详见 Dockerfile 示例。
- 最好配合住宅IP,防止人机验证

## 数据与安全

- 账号邮箱与密码保存在 `account_data/account.json`，登录态在 `*_storage.json`，账号列表在 `accounts.txt`，忽略名单在 `ignored_accounts.txt`，请妥善保管。
- 邮箱推送需配置 SMTP 信息，建议使用专用邮箱。

## 故障排查

- 如果修改了本地网络代理，可能出现登录/签到/分享失败，需要重新登录全部账号
- 登录/签到/分享失败时，请查看 `account_data` 目录下的日志和截图。
- 若遇到网站结构变化，请根据报错信息调整选择器。
- 如有问题欢迎提交 issue。 
