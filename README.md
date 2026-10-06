# iamhc 自动签到脚本

自动登录 [api.hcnsec.cn](https://api.hcnsec.cn)并执行每日签到，签到后通过 Telegram 推送通知(可选)。

站点登录/签到接口开启了 Cloudflare Turnstile 人机验证，脚本会用 DrissionPage 接管真实 Chrome 打开登录页获取验证 token（自动通过；如遇交互式复选框则模拟点击），再调用 API 完成签到，支持多账户。


### 配置 Secrets

在仓库 **Settings → Secrets and variables → Actions** 中添加以下 Secrets：

| Secret 名称 | 说明 |
|-------------|------|
| `EMAIL` | 账户1 登录邮箱(必填) |
| `PASSWORD` | 账户1 登录密码(必填) |
| `EMAIL2` / `PASSWORD2` | 账户2 登录邮箱/密码(可选) |
| `EMAIL3` / `PASSWORD3` | 账户3 登录邮箱/密码(可选) |
| `EMAIL4` / `PASSWORD4` ... | 更多账户，最多支持到 `EMAIL9` / `PASSWORD9` |
| `TG_BOT_TOKEN` | Telegram Bot Token(可选) |
| `TG_CHAT_ID` | Telegram Chat ID(可选)  |

多账户按顺序逐个签到，某个账户失败不影响其他账户，最终只要有失败任务就会标记为失败；每个账户签到完成后单独推送一条 Telegram 通知。

### 手动触发

在仓库 **Actions** 页面选择 `iamhc Daily Checkin` 工作流，点击 **Run workflow** 即可手动触发。

运行失败时可在该次运行的 Artifacts 中下载 `turnstile-shots` 截图，查看当时页面上人机验证控件的状态，便于排查。

## 获取 Telegram Bot Token 和 Chat ID

1. 在 Telegram 中搜索 `@BotFather`，发送 `/newbot` 创建机器人，获取 **Bot Token**
2. 搜索 `@userinfobot`，发送任意消息，获取你的 **Chat ID**
3. 先给你的 Bot 发一条消息（激活会话），否则 Bot 无法主动推送
