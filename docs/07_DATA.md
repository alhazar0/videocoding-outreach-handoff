# 数据口径与迁移

## 分析可复现性

handoff/analyze_outreach.py 只用 sqlite3 mode=ro + PRAGMA query_only，读取 contacts、首次 send log、inbox、profile 和跟进记录。报告的观察截止取 Microsoft 与 Google 同步水位较早值；若水位缺失则退回最后一封已知来信。日期不自动延伸到今天。

首发必须 status=sent 且有可解析 sent_at；按 contact_id 的 creator_initial 唯一记录。去除首发前已有候选人工来往的联系人，再以 168 小时成熟窗计算七天回复率。response elapsed 从首发接受时间计算，七天回复率分母包含没有回复的人；中位时间只在已回复者计算。24h/48h 与七天率使用相同成熟分母。

旧无时区 sent_at 按 UTC 解释，数量单列在 metrics.json；它是历史格式假设，并非证明每条原始记录自带 UTC。非成熟群体仍可统计截至水位的累计回复，但不能与成熟七天率混用。

自动通知剔除规则在脚本中公开；它不替代逐条人工标注。interested 是路由标签，不直接等于合格商业机会。contact 分类、语言和粉丝是现存值，没有完整发送时快照，因此分群结果属于观察性关联。

## 核心表

| 表 | 关键键与作用 | 不能如何使用 |
|---|---|---|
| contacts | id；unique email；DNC、原通道和计划、分类 | 单独复制后重新从头发 |
| outreach_send_log | unique(contact_id,campaign_kind)；活动日、provider、template、结果 | sent 当实际收件，unknown 当 failed |
| outreach_sent_messages | 提供商 message id、conversation、recipient、完整正文、sent time | 单靠 Sent 证明对方看到 |
| inbox_events | provider id / internet message id、全文、类型、关联 contact | preview 当完整上下文，系统回信当兴趣 |
| outreach_work_items | unique inbox event；审核任务和草稿 | pending 自动等于未发送 |
| approved_reply_batch_log / safe_workflow_reply_log / coupon_reply_log | 具体发送 attempt 与结果 | 直接相加成独立邮件数；各表可能重叠 |
| creator_followups | source_kind/source_id/sequence 唯一，原通道与状态 | 无视最新来信继续跟进 |
| creator_profiles | 精确注册邮箱、product ID、核验状态、任务、码、报价 | “test claimed”当完成，查无当删除 |
| reviewed_cardless_operations | work_id、幂等 key、grant 状态 | 仅数审计事件就算独立 grant 人数 |
| reviewed_contact_holds | contact_id、active、理由 + 触发器 | 自动解除暂停 |
| reviewed_service_cases | work_id、contact_id、pending + 触发器 | 以已回复或订阅标签结案 |
| outreach_templates / automation_rules | 文案、批准状态、规则模式 | 模板名 winner 或 enabled 当新授权 |
| outreach_settings | 配置和同步水位，也可能含 OAuth token | 直接导出到 Git |
| outreach_audit_log | 操作、对象、证据和时间 | 把重复 audit 当独立结果 |
| lead_provenance / activity cache | 来源、身份与活动证据 | 只按邮箱存在就发 |

## 产品与 coupon

保守的真实完成口径要求 usage_source=product_outreach_api、usage_verification_status=completed、account_status=verified、product_user_id 非空，并按 product_user_id 去重。每个账号采用最后一次有效 profile 核验；累计任务不是当前批次新增，更不是收入。

当前代码在 found=false 时保留部分历史任务计数，故不能直接 SUM 全部 usage_completed_tasks。核销字段中 not_checked 必须保留未知；coupon_redemption_verified_at 当前只是本地同步时间，需新增源事件字段才能计算可靠核销。

reviewed_cardless_operations 目前 59 行成功状态，另有 83 条 reviewed_cardless_grant_confirmed 审计事件以及其他 grant 历史路径，不能简单相加；必须用精确产品账号 + 幂等 key + 后台确认重建唯一 grant ledger。原始证据和到期时间在私有操作目录，不进代码仓库。

## 迁移数据包要求

正式迁移是单独权限范围。需 SQLite 一致性备份（含 WAL 处理）、线索库、附件引用与完整消息、审计证据、hold/service 触发器、所有已接受与不明发送、已 grant 的精确账号/幂等/到期和原发信通道。密钥独立安全交付，不随备份明文分享。

迁移前后核对主键数量、unique 约束、未知 34 条首次和独立未知回复、active holds 82、pending service 22 work/17 contact，以及所有已接受 manifest 不再次进入执行。以上是这次快照基线；若源库变化，以新的精确比较为准。

仓库外 private/creator-details.json 提供使用排名、统计逐联系人行、持久 hold 和服务 case 明细供获授权同事查找；单独这一份不是完整迁移数据包。

此次另整理私有 operations-packet：包含运营库、线索库的一致性 SQLite 备份，已保存的完整轮次证据、runtime analysis、原运维脚本和配置参考。数据库中的凭证设置被清空，备份经过 VACUUM，表行数和文件哈希在私有 manifest 中。它保留事实与保护状态，不是可直接启动的生产镜像。没有保存的远端媒体、完整外部产品/账单后端和实时权限不在包内；具体边界见 11_HANDOFF_CHECKLIST。原始包最初仅保留本地，2026-11-09 按用户要求通过 GitHub Release 交付 AES-256 加密副本；密码和解密数据不进 Git，见 12_DATA_PACKAGE。

补查还保留三个原页面直接读取的私有数据文件：2026-09-09T20:49:56Z 的 coupon audit（456 行邮件审核对象）、2026-09-06T20:09:09.733Z 的账单来源（184 行），以及 2026-10-28T07:36:13.328Z 的账单缓存（414 行）。前者是发码/回信分类，不是用码核销流水；后两者是各自时间点的账单记录，不是当前交易全貌，也没有自动关联到某位博主的推广效果。它们未并入截至 10 月 20 日的外联统计，不能用较新缓存日期延长邮件观察窗。
