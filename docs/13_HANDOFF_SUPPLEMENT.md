# VideoCoding 外联系统补充交接说明（2026-11-09）

## 一、范围
本仓库是 localhost:3000 博主外联与邮箱管理系统的脱敏交接，当前 main 提交 ea9662e6c3d1980145c75faf7ea64ddb06fc9c85。它不是 VideoCoding 官网及全部产品后端的完整迁移包。原外联、同步、回复、采集均应保留暂停，获取数据和凭证不等于获准恢复。

## 二、已准备的私有数据包
文件：PRIVATE-videocoding-operations-20261101.tar.gz
大小：317414672 bytes（约317 MB / 303 MiB）
SHA-256：bfb80e704046e71976a11e57503d1e54cab99e0a191a2ea2187b7713366ad533
2026-11-09 重新核对文件哈希与 manifest 一致。按负责人追加要求，原始包封装为 AES-256 加密 ZIP，作为 GitHub Release 附件提供；密码单独交付，原始明文包不上传。
解压后的主要位置：
private/operations-packet/databases/outreach.sqlite
private/operations-packet/databases/lead-discovery.sqlite
private/operations-packet/manifest.json
private/operations-packet/README-PRIVATE.txt
private/operations-packet/operations-evidence/
private/operations-packet/runtime-analysis/
private/creator-details.json
private/protected-state.json

两库通过 SQLite backup API 从只读来源一致性备份，非仅拷贝主文件而忽略 WAL。凭证设置已清空，明确的文本凭证已脱敏，完整修改项见 manifest；因此旧审核上下文不能直接重新执行。原活动日、收发事实、未知、禁发、hold、service、grant 等业务保护保留。
运营观察水位截至2026-10-20T18:18:57Z，不能作为今天已刷新邮箱或产品状态的证明。导入时检查并停用快照内部 automation_rules/modes 和新机器的所有调度；源机器被 launchd 禁用的事实不会自动跟随 SQLite 导入。

## 三、导入必须核对的基线
contacts：47901行。
creator_initial明确接受：30551；首次delivery_unknown：34，另保留独立历史未知回复。
reviewed_contact_holds：82 active；reviewed_service_cases：22 pending work、17联系人。
reviewed_cardless_operations：59行；另有83条grant confirmed审计，不能直接相加作为独立开通人数，必须按精确产品账号及幂等键重建。
inbox_events：10085行；outreach_sent_messages：44171行；outreach_conversations：38663行。这些是各表行数，不能相加成独立邮件数或会话人数。
3310是截至观察终点有候选人工回复的联系人数量，并非数据库只有3310条历史邮件。
2920是在完整七天观察样本28912中七天内有候选人工回复的联系人数，并非只有2920段聊天。
30551是提供商接受，不是已证实实际送达。
导入之后必须保持accepted/unknown不可重发、grant不可重开、hold和服务工单触发器有效；只导入contacts和send_log不足以接管完整工作流。

## 四、凭证与运行环境
公开仓库和私有包均不提供可直接使用的Microsoft refresh token、Google应用专用密码或产品管理令牌。Microsoft需匹配租户、client ID、应用配置、回调及现有权限，由账号负责人重新授权或通过批准的安全渠道配置。Google原通道为creator@outreach.videocoding.ai，既有SMTP/IMAP配置与密码需单独配置。产品账号核验/人工免卡API也有独立权限；恢复发信不自动授权开通或账单操作。
Microsoft与Google原发送身份和700/50额度、ET08–21窗口、45–60秒/120秒间隔不得迁移时改变；不要让两台机器同时消费旧队列。
代码含macOS Keychain、launchd及fcntl锁。Windows部署需要实际验证兼容层、凭证存储、进程调度与共享去重/间隔，不能以数据库成功导入替代运行验收。
截图所提import_private_outreach_db.py、edge.env和cutover脚本不在本次交接仓库里，可能是接手方新建或其他项目文件；原作者尚未审核其行为，不能据名称认定会保留所有保护。

## 五、官网、DNS、R2、Stripe/Resend/Google Cloud
理解或开发3000外联系统不要求先把videocoding.ai官网A记录切到新主机，也不要求先迁R2视频。若另有官网迁移任务，请独立列出范围、旧新数据库、任务队列、对象存储、认证、支付回调、HTTPS和回滚验收。
配置中出现ADMIN_USER_EMAIL或Cloudflare Account ID不能证明该人拥有域名、R2、Stripe或其他平台管理员权限，必须在对应平台核实；不从这份交接猜测归属。
只读DNS核查显示videocoding.ai的NS为Cloudflare，根域及www对外返回Cloudflare代理IP，MX为Microsoft。公开DNS不能验证控制台背后的源站是否为截图中两个47.251.*地址中的任何一个。media的归属/依赖和R2的11.75GB数量未在本次核实。
不要凭截图承诺“10秒无缝、零停机、完全不影响邮箱或视频”。DNS变更、源站健康、证书、数据库增量一致性、后台任务单一执行和回滚需要分别验证。Cloudflare代理记录对外返回代理IP；TTL也不能当作实际源站切换完成时间的保证。
参考：https://developers.cloudflare.com/dns/proxy-status/
参考：https://developers.cloudflare.com/dns/manage-dns-records/reference/ttl/

## 六、下一步
请先在 handoff-data-20261109 Release 下载加密包，向负责人取得解压密码，校验 SHA-256 并解压，在隔离环境只读核对schema、上述计数和保护状态，列明拟用的导入脚本、Windows适配和剩余权限需求。当前不发送测试信、不恢复旧manifest、不修改官网DNS或公开后台。需要接管线上运营时，由负责人明确恢复范围后再读取最新提供商与产品事实并验收。
