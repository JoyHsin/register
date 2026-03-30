# OpenAI 自动注册

## 功能亮点

- **自定义域名邮箱**：通过 Cloudflare 邮件路由 + QQ IMAP 实现私有化收件，无需公共临时邮箱。
- **OTP 自动重发**：等待超时后自动重新触发验证码发送，应对 OpenAI 发信延迟。
- **CPA 集成**：自动上传 token、清理失效账号、按目标数量控制循环。
- **Sub2API 集成**：支持全局 Admin API Key 直接上传。
- **全环境变量配置**：所有参数均可通过 `.env` 文件配置，无需命令行参数即可启动。

---

## 快速开始

### 1. 安装依赖

```bash
cd openai-register
uv sync              # 基础功能
uv sync --extra cpa  # 若需要 CPA 清理（依赖 aiohttp）
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入你的实际配置
```

### 3. 启动

```bash
uv run python openai_register.py
```

就这样，无需任何命令行参数。

---

## .env 配置说明

完整配置模板见 [.env.example](.env.example)，下面按模块说明：

### 邮箱配置（必填，使用自定义域名方案时）

```env
MAIL_PROVIDER="custom"           # 使用自定义域名 + QQ IMAP

# Cloudflare 转发的目标域名，邮箱格式：{随机}{SUFFIX}@DOMAIN
CUSTOM_EMAIL_DOMAIN="yourdomain.com"
CUSTOM_EMAIL_SUFFIX="_bot"       # 后缀，留空则纯随机
CUSTOM_EMAIL_RANDOM_LENGTH="6"   # 随机前缀长度（默认 6）

# QQ 邮箱 IMAP（Cloudflare 转发收件箱）
QQ_IMAP_USER="yourqq@qq.com"
QQ_IMAP_PASS="your_qq_auth_code" # 授权码，非 QQ 密码！
```

> 如何获取 QQ 授权码：QQ 邮箱 → 设置 → 账户 → 开启 IMAP/SMTP → 生成授权码

### CPA 配置

```env
CPA_BASE_URL="https://your-cpa-host.com"
CPA_TOKEN="your_cpa_password"    # CPA 后台登录密码
CPA_UPLOAD="true"                # 注册成功后自动上传
CPA_CLEAN="true"                 # 自动清理失效账号
CPA_TARGET_COUNT="100"           # 目标有效 token 数，达到后自动等待
PRUNE_LOCAL="true"               # 上传成功后删除本地文件

# 以下有合理默认值，一般不用改
CPA_WORKERS="1"
CPA_TIMEOUT="12"
CPA_RETRIES="1"
CPA_USED_THRESHOLD="95"
```

### CPA Codex OAuth（方案 1）

用于不再本地硬走 OpenAI 登录，而是改成：
1. 向 CPA 申请一条 Codex 授权链接
2. 在浏览器里完成 OpenAI 登录授权
3. 把浏览器地址栏里的 `http://localhost:1455/auth/callback?...` 整段回调 URL 提交回 CPA
4. 由 CPA 生成并落库 Codex 凭证

常用方式：

```bash
# 方式 A：脚本发起授权，手动粘贴 localhost 回调 URL
uv run python openai_register.py --cpa-codex-oauth

# 方式 A2：脚本自动监听 localhost:1455，浏览器回跳后自动提交给 CPA
uv run python openai_register.py --cpa-codex-oauth --cpa-oauth-open-browser

# 方式 B：只打印授权链接并轮询状态，不阻塞等待输入
uv run python openai_register.py --cpa-codex-oauth --cpa-oauth-no-prompt

# 方式 C：你已经拿到了 localhost 回调 URL，直接提交
uv run python openai_register.py \
  --cpa-oauth-callback-url 'http://localhost:1455/auth/callback?code=...&state=...'

# 方式 D：继续轮询某个已有 state
uv run python openai_register.py --cpa-oauth-state your_state_here
```

可选环境变量：

```env
CPA_OAUTH_POLL_INTERVAL="5"
CPA_OAUTH_TIMEOUT="900"
CPA_OAUTH_OPEN_BROWSER="false"
CPA_OAUTH_NO_PROMPT="false"
CPA_OAUTH_LISTEN="true"
CPA_OAUTH_LISTEN_HOST="localhost"
CPA_OAUTH_LISTEN_PORT="1455"
```

### 运行行为

```env
EMAIL_TIMEOUT="900"          # 等待邮件最长秒数（默认 900 = 15 分钟）
OTP_RESEND_INTERVAL="300"    # 超过多少秒没收到则自动重发（默认 300 = 5 分钟）
SLEEP_MIN="20"               # 两次注册最小间隔秒数
SLEEP_MAX="45"               # 两次注册最大间隔秒数
```

### 代理（可选）

```env
HTTP_PROXY="http://127.0.0.1:7890"
HTTPS_PROXY="http://127.0.0.1:7890"
```

---

## Cloudflare 邮件路由配置

1. 在 Cloudflare 控制台 → **Email Routing** → 启用
2. 添加 Catch-all 规则：`*@yourdomain.com` → 转发到 `yourqq@qq.com`
3. QQ 邮箱开启 IMAP，填入授权码

---

## 命令行参数（可选覆盖）

所有参数都有对应的环境变量默认值。只在需要临时覆盖时使用：

```bash
# 只跑一次
uv run python openai_register.py --once

# 调整等待时间
uv run python openai_register.py --email-timeout 1200 --otp-resend-interval 240

# 临时指定代理
uv run python openai_register.py --proxy http://127.0.0.1:7890

# 修改 CPA 目标数
uv run python openai_register.py --cpa-target-count 200
```

查看所有参数：

```bash
uv run python openai_register.py --help
```

---

## 常见问题

**Q: 脚本提示 `CUSTOM_EMAIL_DOMAIN 未配置`**  
A: 检查 `.env` 文件是否存在且 `CUSTOM_EMAIL_DOMAIN` 有值。`.env` 需放在 `openai-register/` 目录下。

**Q: 一直提示 `共匹配 0 个候选码`，收不到验证码**  
A: 检查 Cloudflare Email Routing 活动日志，确认邮件是否被转发到 QQ 邮箱。若 Cloudflare 已转发但脚本没收到，检查 `QQ_IMAP_PASS` 是否是授权码（非 QQ 密码）。

**Q: `Cookie 中无 workspaces` 错误**  
A: 新账号首次登录时正常现象，脚本会自动走多层兜底逻辑，通常可以自动恢复。若仍重试失败，可能是 OpenAI 临时限流。

**Q: `CPA_CLEAN` 需要额外依赖**  
A: 运行 `uv sync --extra cpa` 安装 `aiohttp`。

---

## 输出位置

| 文件 | 说明 |
|------|------|
| `tokens/accounts.txt` | 账号密码，格式：`email----password` |
| `tokens/token_<email>_<timestamp>.json` | 完整 token JSON |

> `tokens/` 已加入 `.gitignore`，不会提交到仓库。

---

## 注意事项

- 需能访问 `https://auth.openai.com`，代理地区建议避开 CN / HK。
- `MAIL_PROVIDER=auto` 时若无自定义域名配置，会回退到公共临时邮箱（稳定性较低）。
- `CPA_TOKEN` 填写的是 **CPA 后台登录密码**，不是 API Key。
- `CPA_BASE_URL` 只填到端口，不带后台页面路径：`http://1.2.3.4:8317` ✅，`http://1.2.3.4:8317/management.html#/` ❌
