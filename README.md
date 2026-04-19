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

### 添加新账号
由于网站的自动登录校验（如滑动验证/风控）目前较难稳定通过，此方法可能会报错或无法完成登录。
```bash
python main.py add <账号名称> --eml <邮箱> --pwd <密码>
```

更推荐的做法是使用手动登录并保存登录态：

```bash
python main.py login <账号名称>
```

执行后会打开可见浏览器，你需要手动完成登录和人机验证；登录成功后按回车键保存登录状态。

成功后可用以下命令确认账号已加入：

```bash
python main.py list
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

- 账号信息和状态保存在 `account_data` 目录下，请妥善保管。
- 邮箱推送需配置 SMTP 信息，建议使用专用邮箱。

## 故障排查

- 如果修改了本地网络代理，可能出现登录/签到/分享失败，需要重新登录全部账号
- 登录/签到/分享失败时，请查看 `account_data` 目录下的日志和截图。
- 若遇到网站结构变化，请根据报错信息调整选择器。
- 如有问题欢迎提交 issue。 