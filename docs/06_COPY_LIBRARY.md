# 文案复盘与待审草案

## 历史正文的实际差异

从原 Sent 首发附近 ±30 秒的记录提取并替换称呼后，主要正文如下。计数是“匹配到该正文的 Sent 记录数”，不是效果分母，也不是完整所有历史邮件重新提供商核验。

## first_contact-v1

主要匹配 7612 条。

> Hi {{channel_name}}, I’m Alhazar from VideoCoding. I think it could be a useful fit for the kind of videos you create, and I’d love to explore a paid collaboration with you. You upload the talking-head cut you want to keep, and VideoCoding takes care of the rest—deciding where visuals are needed, creating the B-roll and motion graphics, and handling the captions, music, and sound design. Here’s the site: https://videocoding.ai If you’d like to test it on one of your own videos, reply and I’ll send you a code for one free month. If you like the result, we can discuss the promotion and your rate. Best, Alhazar Anwar

## first_contact-v2

主要匹配 2052 条。

> Hi {{channel_name}}, I’m Alhazar from VideoCoding. I think it could be a useful fit for the kind of videos you create, and I’d love to explore a paid collaboration with you. You upload the talking-head cut you want to keep, and VideoCoding takes care of the rest—deciding where visuals are needed, creating the B-roll and motion graphics, and handling the captions, music, and sound design. you can also edit it by downloading the source code of the video ,and edit it by using any vibe coding tool Here’s the site: https://videocoding.ai If you’d like to test it on one of your own videos, reply and I’ll send you a code for one free month. If you like the result, we can discuss the promotion and your rate. Best, Alhazar Anwar

## first_contact-v3

主要匹配 2223 条。

> Hi {{channel_name}}, I’m Alhazar from VideoCoding. I think it could be a useful fit for the kind of videos you create, and I’d love to explore a paid collaboration with you. You upload the talking-head cut you want to keep, and VideoCoding takes care of the rest—deciding where visuals are needed, creating the B-roll and motion graphics, and handling the captions, music, and sound design. Here’s the site: https://videocoding.ai If you’d like to test it on one of your own videos, reply and I’ll send you a code for one free month. If you like the result, we can discuss the promotion and your rate. Best, Alhazar Anwar

## first_contact_legacy_light-v1

主要匹配 17785 条。

> Hi {{channel_name}}, I’m Alhazar from VideoCoding. I’d like to explore a paid collaboration with you. VideoCoding turns a talking-head cut into a finished video with B-roll, motion graphics, captions, music, and sound design. You can see it here: https://videocoding.ai If you’d like to test it, reply and I’ll send you a creator code for one month of VideoCoding Pro at $0. After you try it on one real video, we can discuss the promotion format and your rate. Best, Alhazar Anwar VideoCoding

## 我们能得出的结论

v1 与 v3 的主体一致。v2 多出源代码下载/继续编辑描述，其 23.62% 七天回复率只能作为值得验证的假设，不能把改善归因到这一句。legacy_light 更短，但没有稳定优于长版的证据。知识类 t2v 版本仅 63 人、4 条七天回复，样本不足以形成通用结论。

四个版本都使用 paid collaboration 和先试用再谈报价的顺序，可能使预算先行的创作者误认为已有获批项目。历史“free month / $0”初邀并未总在第一封解释绑卡续费；后来发码信已补充，但仍造成了额外沟通成本。复制时应该修复预期管理，而不是只照抄回复率高的版本。

历史“up to 10 minutes”、节时、源码编辑、特定语言和素材效果必须重新由产品负责人核验，不能当成今天可直接承诺的能力。

## 草案 A 探索性商业沟通

以下为待业务和产品审核的草案，未写入生产模板、未发给任何人。

Subject: Exploring a future VideoCoding collaboration

Hi {{channel_name}},

I’m {{sender_name}} from VideoCoding. We’re exploring whether our video-editing workflow could fit your content. We don’t have an approved sponsored brief or an allocated budget for your channel yet.

Would you prefer to share your usual formats and rates, including the currency, or receive details about an optional private product evaluation? There is no obligation to test, publish, or reserve a slot.

If you prefer to wait until there is a concrete funded brief, that is completely fine.

Best,
{{sender_name}}

适用前提：确实处于探索阶段。若已有真实获批项目，应改写为准确的 brief 和预算，不用“暂无”掩盖已知信息。这个草案可能减少表面回复量，但更可能减少误解；需以合格回复和投诉率验证。

## 草案 B 已表达自愿测试意愿

Subject: VideoCoding private evaluation details

Hi {{name}},

Thanks for your interest in a private evaluation. It does not require a public post or commit either of us to a paid collaboration.

The coupon route may require payment details and starts a subscription that renews at the price shown at checkout after the discounted month unless you cancel before renewal. Please review those terms before choosing it.

If you prefer a no-card evaluation, please share the exact email used for your verified VideoCoding account. We will check eligibility and any existing access first; access has not been activated by this message.

If you prefer to discuss a funded brief before testing, we can leave the evaluation on hold.

Best,
{{sender_name}}

这是路线选择草案，不含新发码或开通确认。真正发码时必须原样保留批准模板中的激活步骤、$0 核对、首月折扣、绑卡、续费和取消披露；真正免卡确认必须独立核验账号 ID、非 Stripe、真实 grant 和固定 UTC 到期后才能发送。

## 报价确认要覆盖什么

明确收到的币种、价格对应格式、时长、平台、条数、包含项、修改轮次、付款方式、制作周期起算条件和权利范围。没有批准就直接说明未接受费用、未预订档期和未授权制作；不要编市场平均价或用 general budget 上限假装个人 allocation。

单独 $ 必须问币种；FROM 是起价；YT + Reel 是总包；30 天广告使用权另加价不是单视频价格；发布间隔不是制作周期。已说过的信息不能再反复索取。对方只说 thanks/Alright/Ok 时读全历史判断，避免机械确认回信形成循环。

## 投诉和服务回复

承认已经提供的具体材料，只问解决问题还缺的必要证据；不让重复购买、重传或重测来绕过故障，不编已经升级给团队、已经退款或已经修好。服务保持 pending 并暂停推广，直到真正解决后人工关闭。账单权限不足是内部待办，不应反复催客户或重复催同一个已知权限阻塞。
