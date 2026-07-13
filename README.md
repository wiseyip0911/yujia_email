# SCI 投稿管理平台

这是 SCI 投稿管理平台的第一版可运行工程骨架。当前实现重点是跑通稳定业务软件的端到端主链路：

`邮件采集/导入 -> AI 结构化建议 -> 人工审核确认 -> 稿件事件写入 -> 提醒/日报 -> 金蝶同步批次 -> 审计追溯`

产品定位：

- 最终产品是稳定的传统业务软件，不是 AI workflow / agent 编排应用。
- AI 只作为邮件理解、字段提取、证据摘录和相似稿件匹配建议模块。
- 正式状态写入、权限、审计、金蝶同步和数据覆盖由确定的软件逻辑控制。

## 快速启动

```bash
python3 backend/server.py
```

然后打开：

```text
http://127.0.0.1:8000
```

默认数据库文件：

```text
data/sci_platform.sqlite3
```

## 常用接口

```text
GET  /api/health
GET  /api/dashboard
POST /api/jobs/fetch-emails
GET  /api/review-tasks
POST /api/review-tasks/{id}/confirm
GET  /api/manuscripts
GET  /api/reports/daily
POST /api/exports/kingdee-csv
GET  /api/audit-logs
```

## 邮箱导入与连接测试

读取 `.xlsx` 需要使用内置 Python，因为系统 Python 没有 `openpyxl`。

先做 dry-run，验证导入、去重和服务商分类：

```bash
/Users/yujia/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tools/mailbox_import_and_test.py \
  --xlsx /Users/yujia/Desktop/在投项目邮箱密码.xlsx \
  --dry-run
```

执行保守 IMAP 连接测试：

```bash
/Users/yujia/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tools/mailbox_import_and_test.py \
  --xlsx /Users/yujia/Desktop/在投项目邮箱密码.xlsx \
  --timeout 15 \
  --delay 0.8
```

脚本会导入邮箱元数据和项目映射，在 SQLite 里记录连接测试结果，并在 `data/connection_tests/` 下输出脱敏 CSV/JSON 报告。密码只从工作簿读取并用于登录测试，不写入数据库或报告文件。

## Microsoft Outlook/Hotmail OAuth

Outlook/Hotmail 不走邮箱密码或客户端授权码。平台使用 Microsoft OAuth 授权码流 + PKCE 获取 token，再通过 IMAP XOAUTH2 验证收件箱。

Microsoft Entra 应用注册建议：

- Supported account types: Accounts in any organizational directory and personal Microsoft accounts
- Redirect URI: `http://127.0.0.1:8000/api/oauth/microsoft/callback`
- Delegated permission/scope: `https://outlook.office.com/IMAP.AccessAsUser.All`
- Also request: `openid profile email offline_access`

启动前配置：

```bash
export MICROSOFT_CLIENT_ID="你的 Application (client) ID"
export MICROSOFT_REDIRECT_URI="http://127.0.0.1:8000/api/oauth/microsoft/callback"
# 如果你的应用注册为 confidential web app，再设置：
export MICROSOFT_CLIENT_SECRET="你的 client secret"

python3 backend/server.py
```

配置完成后，在“邮箱”页的 Outlook/Hotmail 记录上点击 `Microsoft 授权`。授权成功后，平台会保存 OAuth token 并用 XOAUTH2 只读打开 INBOX 做连接验证。

当前原型把 token 存在本地 SQLite，适合本机开发验证；正式部署需要迁移到加密凭据库或 KMS。

## 测试

```bash
python3 -m unittest discover -s tests
```

## 当前阶段

当前版本是零外部依赖实现，便于快速验证主链路。后续可以在不改变核心业务模型的前提下迁移到：

- FastAPI
- React + TypeScript
- PostgreSQL
- Celery + Redis
