# VideoCoding 博主外联项目交接

这是一套运行在本机 localhost:3000 的博主外联与合作管理系统，用于寻找合适创作者、建立联系、安排自愿产品评估、核实实际使用、收集报价，并把真正可执行的付费合作交给业务负责人决定。

本仓库是 **2026-11-01 整理的脱敏代码副本和复盘**，由项目负责人授权临时公开，供同事及其 AI 下载、理解和继续开发。邮件和产品证据停在 **2026-10-20**，不代表服务器恢复后的当前用户状态。用户此前已要求停止；此次代码交接与公开发布**没有恢复发送、同步或采集**。

仓库地址：https://github.com/alhazar0/videocoding-outreach-handoff 。第一次接手请先读 [完整交接清单与 AI 接手指令](docs/11_HANDOFF_CHECKLIST.md)，再读 [我对项目的判断](docs/10_MY_ASSESSMENT.md)。公开代码、私有运营证据和外部权限分开交付，不能只下载代码就对旧名单重新发送。

**2026-11-09 补充交接：** 数据包通过 [GitHub Release 下载](https://github.com/alhazar0/videocoding-outreach-handoff/releases/tag/handoff-data-20261109)，采用 AES-256 加密 ZIP，解压密码由项目负责人单独提供。`git clone` 和仓库的 Source code ZIP 不包含 Release 附件。请同时阅读 [下载与解压说明](docs/12_DATA_PACKAGE.md) 和 [给接手 AI 的补充说明](docs/13_HANDOFF_SUPPLEMENT.md)。

## 先看结论

- 联系人 47,901；首次联系获提供商接受 30,551；首次提交结果不明 34。接受不等于实际收到。
- 有完整七天观察窗的新联系人 28,912，候选人工回复 2,920，七天回复率 10.10%。自动通知已做规则筛查，仍需要抽样人工复核。
- AI 与科技类七天回复率 17.95%，创意与媒体类 6.64%；优先结合回复概率、实际完成任务和合作成本分配精力。
- 文案 v2/v3 的原始表现较高，但 v1/v3 的主要正文相同，不能认定是版本号或短文案带来提升。
- 后台核验过的 240 个不同产品账号累计完成 474 个任务；这不是优惠码使用次数，也不是本次邮件归因。
- 7,174 个 profile 中，6,997 的核销状态未知、177 被标记为未核销、没有已核销记录。现在无法可靠回答“谁的 coupon 带来最多核销”。
- 当前数据库里 pending 1,696 项，其中有受保护服务工单；82 个持久暂停联系人必须保留。

## 阅读顺序

1. [项目和商业目标](docs/01_PROJECT.md)
2. [详细数据复盘](docs/02_RESULTS.md)
3. [代码架构和目录](docs/03_ARCHITECTURE.md)
4. [接手与运行手册](docs/04_OPERATIONS.md)
5. [优化顺序和实验设计](docs/05_OPTIMIZATION.md)
6. [文案分析和待审草案](docs/06_COPY_LIBRARY.md)
7. [数据口径与迁移](docs/07_DATA.md)
8. [已知问题和历史修复](docs/08_KNOWN_ISSUES.md)
9. [验证结果](docs/09_VALIDATION.md)
10. [我的判断：值得复制什么、应当停止什么](docs/10_MY_ASSESSMENT.md)
11. [完整交接清单、验收和 AI 接手指令](docs/11_HANDOFF_CHECKLIST.md)
12. [加密数据包下载、解压与校验](docs/12_DATA_PACKAGE.md)
13. [数据库、凭证与官网迁移的补充说明](docs/13_HANDOFF_SUPPLEMENT.md)

AI 接手先读 [AGENTS.md](AGENTS.md)。[handoff/metrics.json](handoff/metrics.json) 是机器可读汇总，[handoff/analyze_outreach.py](handoff/analyze_outreach.py) 可对私有数据库只读复算。

## 仓库范围

保留 dashboard、API、采集、队列、双通道发信、收信、完整会话审阅、产品核验、人工免卡、服务保护、数据库结构和回归测试。包含后来未提交到旧 Git 的重要修复；不沿用带联系人种子的旧 Git 历史。

真实数据库、联系人种子、邮件正文、附件、生产发送 manifest、完整禁发和 grant 证据通过加密 Release 附件交接，解密后的数据和密码不提交 Git。OAuth、Stripe、产品令牌和邮箱密码不随代码或数据包交付，需独立配置。仓库的空种子不会复现真实名单；部署模板中的 OPERATOR、OAuth ID 和隧道主机是占位值。

这是便于学习和继续开发的代码交接，不是零配置生产镜像。Python 标准凭证入口默认禁用，账单插件默认关闭；这些防误操作开关不构成网络沙箱。正式迁移需独立完成私有数据与状态保护验收。
