# 本机 Stripe 扣款名单

入口：http://localhost:3000/billing；运营首页侧栏「自动扣款」。
沿用 ai.videocoding.dashboard 服务和现有 vinext dev，不另开服务。

- `build/billing-local.mjs` 在 Vite Node 进程提供 `/api/billing`，仅在本机开发模式启用。不会被打包进 Cloudflare Worker，也不适用于线上部署。
- Stripe 密钥只从 `~/.videocoding/stripe-live.env` 的 `STRIPE_SECRET_KEY` 读取（运行环境中的同名变量可覆盖），不传到浏览器、不放入源码。
- 分页读取 Stripe 全部订阅，按 VideoCoding overseas 元数据及已核对的历史订阅 ID 限定项目；用户姓名/邮箱优先采用最新 Stripe 客户信息。
- 历史 ID 和用户资料补充来源：`~/Reports/videocoding-billing-2026-09-07/billing-source.json`。新增订阅依赖 `metadata.edition=overseas` 和 `metadata.userId`，无需更新历史文件。
- 每次成功同步将快照存到 `~/.videocoding/billing-dashboard-cache.json`（0600）。历史文件及缓存都不应放入 public、源码或远程仓库。
- 页面打开期间每 60 秒轮询；服务按需刷新、合并并发请求，手动刷新有 10 秒保护间隔。读取失败保留旧名单并显示同步异常和真实最后成功时间。
- 自动续费：active/trialing + charge_automatically，排除周期末取消、收款暂停和不晚于周期结束的计划取消。其他记录也显示在全部名单中。
- 金额采用 Stripe upcoming invoice.amount_due，失败显示待确认，不用标价代替。时间为下一期预计周期结束（试用使用 trial_end），非扣款成功承诺。
- 国家仅在 Stripe tax.location.source=ip_address 且存在 tax.ip_address 时使用；不把账单地址冒充 IP 国家。
- `/api/billing` 只接受本机同源 GET。无邮件发送、修改订阅或扣款操作。

校验：`node --test tests/billing-local.test.mjs`；`npm run build`。
