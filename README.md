# 多账号自动签到与视频分享自动化工具

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

### 添加新账号

```bash
python main.py add <账号名称> --eml <邮箱> --pwd <密码> [--visible]
```
- `--visible` 可选，使用可见浏览器，便于调试。

### 删除账号

```bash
python main.py delete <邮箱>
```

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

- 账号信息和状态保存在 `account_data` 目录下，请妥善保管。
- 邮箱推送需配置 SMTP 信息，建议使用专用邮箱。

## 故障排查

- 登录/签到/分享失败时，请查看 `account_data` 目录下的日志和截图。
- 若遇到网站结构变化，请根据报错信息调整选择器。
- 如有问题欢迎提交 issue。 