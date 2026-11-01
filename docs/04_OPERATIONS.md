# 接手与运行手册

## 当前暂停状态

最后一次人工停止已暂停 Codex 每小时 heartbeat，卸载并禁用 23 项 VideoCoding 后台服务，8 个 collector 全部停止。3000 dashboard 保留。此次交接没有恢复这些任务；原数据库的 automation_rules.mode=auto 是暂停前的策略配置，**不代表当前实际后台在运行**。

先建立只读环境，理解数据和任务。恢复发信需要用户明确指令；迁移同一批联系人时必须同时迁移已发送、未知、退订、hold、service、grant 和消息身份数据，不能只拿 contacts 表重新排队。

## 本地查看代码和统计

```bash
git status
python3 handoff/analyze_outreach.py --help
# 私有数据库路径由持有人单独提供，不从源码获取凭证
python3 handoff/analyze_outreach.py   --db /absolute/private/outreach.sqlite   --out /absolute/output/metrics.json   --private-out /absolute/private/creator-details.json
```

统计脚本只读 SQLite，不导入生产模块，不访问邮箱或产品 API。私有明细输出拒绝写入仓库目录。旧生产脚本的 dry run 可能仍初始化或改本地工作项，不作为只读工具使用。

## 安装离线开发依赖

```bash
node --version     # package.json 要求 >=22.13，原运行环境为 24.4.1
npm ci
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.test.mjs
npm run build
```

安装和构建不授权启用后台。测试已知结果见 09_VALIDATION；全量 Python 当前有 6 个旧 fixture 缺表错误，不能略过它们后宣称全仓通过。

## 空数据 UI 演示

```bash
# 空种子、无原始会话、无生产令牌；换端口避免占用原 3000
npm run dev -- --hostname 127.0.0.1 --port 3001
```

该副本 public/data/contacts_seed.json 为空。部分运营子页依赖完整 schema，不能把空库页面报错看成生产数据丢失。需要完整空库时，使用 handoff/schema-current.sql 在新建的隔离 SQLite 执行；Cloudflare D1 本地文件路径应以新环境实际创建的文件为准，不复制别人的 Miniflare 哈希或原库覆盖过去。Python 可通过 VIDEOCODING_DB 指向这个新库。

不把 dashboard 直接暴露到公网。当前 UI/API 是本机运营工具，身份、权限和服务边界尚需另行部署评审。

## 凭证防误用

副本 Python 的 Microsoft token、Google Keychain 密码、产品 Keychain 令牌入口默认拒绝访问；明确设 VIDEOCODING_ALLOW_LIVE_OPERATIONS=1 后才会继续原流程。Cloudflare Microsoft refresh 同样需要对应 binding。这个开关只是防误操作，并不是付款、恢复或批量发送授权，也不是全网络沙箱。

.env.example 展示 Python 环境配置，Python 不会自动读取 .env 文件；.dev.vars.example 展示本地 Worker bindings。不得把真实值提交。账单插件还需 VIDEOCODING_ENABLE_LOCAL_BILLING=1，默认不会加载；其私有 Stripe 文件和外部 reminder 模块未打包。

## 真正恢复前的具体顺序

1. 明确恢复范围和负责人：仅收信、仅采集或发送，不把一个动作扩大成全部开启。
2. 校验运营库与线索库的来源、时间、quick_check、schema、备份及写入者；避免两个实例同时向同一 mailbox 发送。
3. 恢复完整历史事实：accepted/unknown、提供商 Message-ID、DNC、82 个 hold、22 个服务 work、grant 幂等和固定到期。旧活动日不重排到今天。
4. 只读核验微软和 Google 授权与最新收发。已有恢复探针禁止再发；历史 unknown 的负搜索不能被当成没发。
5. 先刷新产品真实账号和必要会话。期间的新邮件、取消、退订、投诉会改变可发状态；所有旧 reviewed manifest 均不直接重跑。
6. 检查原 ET 时间窗口、700/50 通道上限、45–60/120 秒间隔、原锁和资格门槛。11 月美东夏令时切换必须依赖 America/New_York 时区库，不固定写北京时间。
7. 逐项启用经批准的后台服务，核对实际进程和日志；不要把 deploy/*.plist 通配批量加载。此副本中的路径、OAuth、隧道均需私下配置。
8. 若新限制出现，停受影响通道，走管理员流程。不轮换身份、不转额度、不循环探针。
9. 以当前日、原通道和当前合格库存报告；不承诺无库存或供应商受限时仍达标。

## 再次停止

先暂停 heartbeat 和健康恢复任务，再禁用调度的自动重启；核验当前发送是否在提交阶段，优先让已开始的提交落地并记录 accepted/unknown，随后停止其余任务。停后核对发送表没有遗留 sending，未知不改 failed，不删锁内事实。保持 dashboard 或只读报告不影响暂停外联。

## 同一业务复制与新业务复用

同一业务横向扩展必须共用或同步唯一发送事实与禁发状态，不能每台机器各自去重。新业务可以复用代码和指标体系，但预算、发件身份、联系人授权、产品承诺、码条款与额度都要独立定义，不能继承旧活动授权。不要把已有 accepted/unknown 联系人作为新实验重新发一次。
