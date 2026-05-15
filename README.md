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

### 添加 / 保存账号邮箱与密码（仅写本地文件）

`add` 把邮箱、密码合并写入 `account_data/account.json`

```bash
python main.py add <账号名称> --eml <邮箱> --pwd <密码>
```

随后在浏览器里完成登录并保存状态（`login` 会从 `account.json` 读取凭据用于自动填表）：

```bash
python main.py login <账号名称>
```

### 删除账号
```bash
python main.py delete <账号名称>
```

如果不确定账号名称，可以先执行：`python main.py list`

### 列出所有账号

```bash
python main.py list
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

- 账号邮箱与密码保存在 `account_data/account.json`，登录态在 `*_storage.json`，账号列表在 `accounts.txt`，请妥善保管。
- 邮箱推送需配置 SMTP 信息，建议使用专用邮箱。

## 故障排查

- 如果修改了本地网络代理，可能出现登录/签到/分享失败，需要重新登录全部账号
- 登录/签到/分享失败时，请查看 `account_data` 目录下的日志和截图。
- 若遇到网站结构变化，请根据报错信息调整选择器。
- 如有问题欢迎提交 issue。 