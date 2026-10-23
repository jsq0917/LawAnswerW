-- ============================================================================
-- 高频问答对种子数据（决策点 Q8：60 条）
--   劳动法向 60 条（12 类 × 5）+ 民法典向 5 条 = 65 条
--   覆盖 §6.4 结构化链路的全部话题；口语化变体即 BM25 ngram 的索引来源
-- 幂等：按 question_std 判存，重复执行不产生重复行（保留 hit_count）
-- 注意：本文件字符串内不允许出现分号（init_db 按分号切分语句）
-- ============================================================================

SET NAMES utf8mb4;

-- ---------------------------------------------------------------- 试用期（5）
INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '试用期', '试用期最长可以约定多久？', '["试用期能设多长时间","试用期一般多长","试用期时间上限","试用期可以约定六个月吗"]',
 '试用期最长可以约定多久？ 试用期能设多长时间 试用期一般多长 试用期时间上限 试用期可以约定六个月吗',
 '试用期上限与劳动合同期限挂钩：劳动合同期限三个月以上不满一年的，试用期不得超过一个月；一年以上不满三年的，试用期不得超过二个月；三年以上固定期限和无固定期限劳动合同，试用期不得超过六个月。同一用人单位与同一劳动者只能约定一次试用期；以完成一定工作任务为期限或期限不满三个月的合同，不得约定试用期。（《中华人民共和国劳动合同法》第十九条）',
 '[{"law":"中华人民共和国劳动合同法","article":"第十九条"}]', 'kb_labor', 'labor_law', '试用期,时长,上限'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '试用期最长可以约定多久？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '试用期', '试用期工资可以随便定吗？', '["试用期工资有标准吗","试用期工资打八折合法吗","试用期工资能低于正式工资吗","试用期工资最低多少"]',
 '试用期工资可以随便定吗？ 试用期工资有标准吗 试用期工资打八折合法吗 试用期工资能低于正式工资吗 试用期工资最低多少',
 '不可以随便定。试用期工资不得低于本单位相同岗位最低档工资或劳动合同约定工资的百分之八十，并不得低于用人单位所在地的最低工资标准，三个条件须同时满足。（《中华人民共和国劳动合同法》第二十条）',
 '[{"law":"中华人民共和国劳动合同法","article":"第二十条"}]', 'kb_labor', 'labor_law', '试用期,工资,百分之八十,最低工资'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '试用期工资可以随便定吗？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '试用期', '试用期用人单位能随意解除劳动合同吗？', '["试用期被辞退合法吗","试用期公司可以随便开除人吗","试用期解除合同有什么限制","试用期说不要就不要了怎么办"]',
 '试用期用人单位能随意解除劳动合同吗？ 试用期被辞退合法吗 试用期公司可以随便开除人吗 试用期解除合同有什么限制 试用期说不要就不要了怎么办',
 '不能。试用期中只有劳动者出现第三十九条情形（被证明不符合录用条件、严重违纪等）或第四十条第一、二项情形时，用人单位才能解除劳动合同，且解除时应向劳动者说明理由；否则属于违法解除，劳动者可要求继续履行合同或按二倍经济补偿标准主张赔偿金。（《中华人民共和国劳动合同法》第二十一条、第四十八条、第八十七条）',
 '[{"law":"中华人民共和国劳动合同法","article":"第二十一条"},{"law":"中华人民共和国劳动合同法","article":"第四十八条"},{"law":"中华人民共和国劳动合同法","article":"第八十七条"}]', 'kb_labor', 'labor_law', '试用期,解除,辞退,违法解除'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '试用期用人单位能随意解除劳动合同吗？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '试用期', '试用期可以不签劳动合同吗？', '["试用期需要签合同吗","先试用再签合同可以吗","试用期算劳动关系吗","只试工不签合同违法吗"]',
 '试用期可以不签劳动合同吗？ 试用期需要签合同吗 先试用再签合同可以吗 试用期算劳动关系吗 只试工不签合同违法吗',
 '不可以。已建立劳动关系的，应当自用工之日起一个月内订立书面劳动合同；试用期包含在劳动合同期限内，劳动合同仅约定试用期的，试用期不成立，该期限直接视为劳动合同期限。（《中华人民共和国劳动合同法》第十条、第十九条第四款）',
 '[{"law":"中华人民共和国劳动合同法","article":"第十条"},{"law":"中华人民共和国劳动合同法","article":"第十九条"}]', 'kb_labor', 'labor_law', '试用期,劳动合同,书面合同,一个月'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '试用期可以不签劳动合同吗？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '试用期', '同一劳动者可以多次约定试用期吗？', '["换岗还要再试用一次吗","续签合同能再约定试用期吗","二次入职能约定试用期吗","试用期能约定两次吗"]',
 '同一劳动者可以多次约定试用期吗？ 换岗还要再试用一次吗 续签合同能再约定试用期吗 二次入职能约定试用期吗 试用期能约定两次吗',
 '不能。同一用人单位与同一劳动者只能约定一次试用期；劳动合同续订、岗位调整或再次入职的，均不得另行约定试用期。（《中华人民共和国劳动合同法》第十九条第二款）',
 '[{"law":"中华人民共和国劳动合同法","article":"第十九条"}]', 'kb_labor', 'labor_law', '试用期,一次,续签,调岗'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '同一劳动者可以多次约定试用期吗？');

-- ---------------------------------------------------------------- 加班费（5）
INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '加班费', '平时加班费怎么算？', '["工作日加班几倍工资","平时晚上加班有加班费吗","延时加班怎么算","加班一点五倍是工作日吗"]',
 '平时加班费怎么算？ 工作日加班几倍工资 平时晚上加班有加班费吗 延时加班怎么算 加班一点五倍是工作日吗',
 '平时（工作日）延长工作时间的，用人单位应支付不低于本人小时工资的百分之一百五十的加班工资。计算基数一般按劳动合同约定的正常工时工资确定，且折算后不得低于当地最低工资标准。（《中华人民共和国劳动法》第四十四条第一项）',
 '[{"law":"中华人民共和国劳动法","article":"第四十四条"}]', 'kb_labor', 'labor_law', '加班费,工作日,一点五倍,延时加班'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '平时加班费怎么算？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '加班费', '周末加班必须支付双倍工资吗？', '["周六上班一定要双倍吗","双休日加班怎么算","周末加班能调休代替吗","休息日加班费标准"]',
 '周末加班必须支付双倍工资吗？ 周六上班一定要双倍吗 双休日加班怎么算 周末加班能调休代替吗 休息日加班费标准',
 '休息日安排劳动者工作又不能安排补休的，支付不低于本人日或小时工资的百分之二百的工资报酬；用人单位在合理期限内安排补休的，可以不再支付百分之二百的加班工资。（《中华人民共和国劳动法》第四十四条第二项）',
 '[{"law":"中华人民共和国劳动法","article":"第四十四条"}]', 'kb_labor', 'labor_law', '周末,双休日,加班费,二倍,补休'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '周末加班必须支付双倍工资吗？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '加班费', '法定节假日加班怎么算？', '["春节国庆加班几倍工资","法定假日加班是三倍吗","节假日上班有三薪吗","法定节假日加班费标准"]',
 '法定节假日加班怎么算？ 春节国庆加班几倍工资 法定假日加班是三倍吗 节假日上班有三薪吗 法定节假日加班费标准',
 '法定休假日安排劳动者工作的，支付不低于本人日或小时工资的百分之三百的工资报酬，且不得以补休替代，法定节假日加班必须支付三倍工资。（《中华人民共和国劳动法》第四十四条第三项）',
 '[{"law":"中华人民共和国劳动法","article":"第四十四条"}]', 'kb_labor', 'labor_law', '法定节假日,加班费,三倍,三薪'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '法定节假日加班怎么算？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '加班费', '每月加班时长有上限吗？', '["一个月最多加班多少小时","加班时间有没有限制","每天加班不能超过几小时","加班三十六小时是什么规定"]',
 '每月加班时长有上限吗？ 一个月最多加班多少小时 加班时间有没有限制 每天加班不能超过几小时 加班三十六小时是什么规定',
 '有。一般每日加班不得超过一小时；因特殊原因需要延长的，在保障劳动者健康的前提下每日不得超过三小时、每月累计不得超过三十六小时。用人单位不得强迫或者变相强迫劳动者加班。（《中华人民共和国劳动法》第四十一条、《中华人民共和国劳动合同法》第三十一条）',
 '[{"law":"中华人民共和国劳动法","article":"第四十一条"},{"law":"中华人民共和国劳动合同法","article":"第三十一条"}]', 'kb_labor', 'labor_law', '加班,上限,三十六小时,时限'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '每月加班时长有上限吗？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '加班费', '公司强制加班怎么办？', '["被强迫加班怎么维权","不想加班公司强制怎么办","长期强制加班能辞职吗","强制加班去哪投诉"]',
 '公司强制加班怎么办？ 被强迫加班怎么维权 不想加班公司强制怎么办 长期强制加班能辞职吗 强制加班去哪投诉',
 '用人单位应当严格执行劳动定额标准，不得强迫或者变相强迫劳动者加班（《中华人民共和国劳动合同法》第三十一条）。劳动者可向劳动监察投诉（电话12333）；因强迫加班或未依法支付加班费而解除劳动合同的，还可主张经济补偿（《中华人民共和国劳动合同法》第三十八条、第四十六条）。',
 '[{"law":"中华人民共和国劳动合同法","article":"第三十一条"},{"law":"中华人民共和国劳动合同法","article":"第三十八条"},{"law":"中华人民共和国劳动合同法","article":"第四十六条"}]', 'kb_labor', 'labor_law', '强制加班,维权,投诉,劳动监察,经济补偿'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '公司强制加班怎么办？');

-- ------------------------------------------------------------ 经济补偿（5）
INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '经济补偿', '哪些情况下离职有经济补偿？', '["什么情况可以拿经济补偿","离职补偿的法定情形","被辞退有补偿吗","协商解除有补偿吗"]',
 '哪些情况下离职有经济补偿？ 什么情况可以拿经济补偿 离职补偿的法定情形 被辞退有补偿吗 协商解除有补偿吗',
 '劳动者因用人单位过错解除合同（拖欠工资、未缴社保等，第三十八条）、用人单位提出并协商一致解除、用人单位依法预告解除（第四十条）、经济性裁员、合同到期终止（除单位维持或提高条件续订而劳动者不同意的情形外）等，用人单位均应支付经济补偿（第四十六条）。劳动者主动辞职且单位无过错的，没有经济补偿。',
 '[{"law":"中华人民共和国劳动合同法","article":"第三十八条"},{"law":"中华人民共和国劳动合同法","article":"第四十六条"}]', 'kb_labor', 'labor_law', '经济补偿,情形,离职,辞退,协商解除'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '哪些情况下离职有经济补偿？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '经济补偿', '经济补偿金按什么标准计算？', '["经济补偿怎么算","N是怎么算出来的","离职补偿一年补几个月","经济补偿金计算方式"]',
 '经济补偿金按什么标准计算？ 经济补偿怎么算 N是怎么算出来的 离职补偿一年补几个月 经济补偿金计算方式',
 '按劳动者在本单位工作的年限计算：每满一年支付一个月工资；六个月以上不满一年的按一年计算；不满六个月的支付半个月工资。月工资指劳动合同解除或终止前十二个月的平均应发工资。（《中华人民共和国劳动合同法》第四十七条第一款、第三款）',
 '[{"law":"中华人民共和国劳动合同法","article":"第四十七条"}]', 'kb_labor', 'labor_law', '经济补偿,N,计算,平均工资,月工资'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '经济补偿金按什么标准计算？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '经济补偿', '高收入者的经济补偿有封顶吗？', '["工资很高补偿有上限吗","经济补偿三倍封顶什么意思","月薪五万补偿怎么算","经济补偿封顶规则"]',
 '高收入者的经济补偿有封顶吗？ 工资很高补偿有上限吗 经济补偿三倍封顶什么意思 月薪五万补偿怎么算 经济补偿封顶规则',
 '有。月工资高于用人单位所在直辖市、设区的市级人民政府公布的上年度职工月平均工资三倍的，按职工月平均工资三倍的数额支付，且支付年限最高不超过十二年。（《中华人民共和国劳动合同法》第四十七条第二款）',
 '[{"law":"中华人民共和国劳动合同法","article":"第四十七条"}]', 'kb_labor', 'labor_law', '经济补偿,封顶,三倍,十二年'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '高收入者的经济补偿有封顶吗？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '经济补偿', '协商一致解除劳动合同有经济补偿吗？', '["协商解除有补偿吗","协议离职能要补偿吗","谈妥离职怎么算补偿","协商一致解除要不要赔钱"]',
 '协商一致解除劳动合同有经济补偿吗？ 协商解除有补偿吗 协议离职能要补偿吗 谈妥离职怎么算补偿 协商一致解除要不要赔钱',
 '用人单位向劳动者提出并与劳动者协商一致解除劳动合同的，用人单位应当支付经济补偿；由劳动者主动提出协商解除的，一般没有经济补偿。（《中华人民共和国劳动合同法》第三十六条、第四十六条第二项）',
 '[{"law":"中华人民共和国劳动合同法","article":"第三十六条"},{"law":"中华人民共和国劳动合同法","article":"第四十六条"}]', 'kb_labor', 'labor_law', '协商解除,经济补偿,第三十六条'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '协商一致解除劳动合同有经济补偿吗？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '经济补偿', '劳动合同到期单位不续签有补偿吗？', '["合同到期不续约有赔偿吗","到期公司不续签怎么办","固定期限合同到期有补偿吗","合同到期补偿标准"]',
 '劳动合同到期单位不续签有补偿吗？ 合同到期不续约有赔偿吗 到期公司不续签怎么办 固定期限合同到期有补偿吗 合同到期补偿标准',
 '除用人单位维持或提高劳动合同约定条件续订劳动合同而劳动者不同意续订的情形外，固定期限劳动合同到期终止的，用人单位应当支付经济补偿。（《中华人民共和国劳动合同法》第四十四条第一项、第四十六条第五项）',
 '[{"law":"中华人民共和国劳动合同法","article":"第四十四条"},{"law":"中华人民共和国劳动合同法","article":"第四十六条"}]', 'kb_labor', 'labor_law', '合同到期,不续签,经济补偿'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '劳动合同到期单位不续签有补偿吗？');

-- ------------------------------------------------------------ 竞业限制（5）
INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '竞业限制', '竞业限制期限最长多久？', '["竞业协议能签几年","竞业限制有时间限制吗","竞业禁止最长多长时间","离职后多久不能去同行"]',
 '竞业限制期限最长多久？ 竞业协议能签几年 竞业限制有时间限制吗 竞业禁止最长多长时间 离职后多久不能去同行',
 '竞业限制期限由双方约定，但最长不得超过二年，超过二年的约定超出部分无效；期限从劳动合同解除或终止时起算。（《中华人民共和国劳动合同法》第二十三条第二款、第二十四条第二款）',
 '[{"law":"中华人民共和国劳动合同法","article":"第二十三条"},{"law":"中华人民共和国劳动合同法","article":"第二十四条"}]', 'kb_labor', 'labor_law', '竞业限制,期限,二年'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '竞业限制期限最长多久？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '竞业限制', '竞业限制期间用人单位要给补偿吗？', '["竞业期间有工资吗","竞业补偿金必须给吗","履行竞业限制有钱拿吗","竞业限制补偿标准"]',
 '竞业限制期间用人单位要给补偿吗？ 竞业期间有工资吗 竞业补偿金必须给吗 履行竞业限制有钱拿吗 竞业限制补偿标准',
 '应当给。约定竞业限制的，用人单位应当在竞业限制期限内按月给予劳动者经济补偿；标准有约定从约定，没有约定或约定不明的，司法实践中一般按离职前十二个月平均工资的百分之三十且不低于当地最低工资标准认定。（《中华人民共和国劳动合同法》第二十三条）',
 '[{"law":"中华人民共和国劳动合同法","article":"第二十三条"}]', 'kb_labor', 'labor_law', '竞业限制,补偿,按月,百分之三十'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '竞业限制期间用人单位要给补偿吗？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '竞业限制', '哪些人需要签竞业限制协议？', '["普通员工要签竞业吗","竞业限制针对哪些人","全员竞业协议有效吗","什么人受竞业限制约束"]',
 '哪些人需要签竞业限制协议？ 普通员工要签竞业吗 竞业限制针对哪些人 全员竞业协议有效吗 什么人受竞业限制约束',
 '竞业限制的人员限于用人单位的高级管理人员、高级技术人员和其他负有保密义务的人员。用人单位与不了解、不接触商业秘密的普通员工约定竞业限制的，该约定对劳动者不发生效力。（《中华人民共和国劳动合同法》第二十四条第一款）',
 '[{"law":"中华人民共和国劳动合同法","article":"第二十四条"}]', 'kb_labor', 'labor_law', '竞业限制,高管,保密,适用范围'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '哪些人需要签竞业限制协议？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '竞业限制', '违反竞业限制要付违约金吗？', '["跳槽到竞品要赔钱吗","违反竞业限制违约金多少","竞业违约怎么赔","违反竞业协议后果"]',
 '违反竞业限制要付违约金吗？ 跳槽到竞品要赔钱吗 违反竞业限制违约金多少 竞业违约怎么赔 违反竞业协议后果',
 '劳动者违反竞业限制约定的，应当按照约定向用人单位支付违约金；违约金约定过高的，仲裁或诉讼中可以请求予以酌减。给用人单位造成损失的，还应承担赔偿责任。（《中华人民共和国劳动合同法》第二十三条第二款、第九十条）',
 '[{"law":"中华人民共和国劳动合同法","article":"第二十三条"},{"law":"中华人民共和国劳动合同法","article":"第九十条"}]', 'kb_labor', 'labor_law', '竞业限制,违约金,违约,赔偿'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '违反竞业限制要付违约金吗？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '竞业限制', '单位不支付竞业补偿金协议还有效吗？', '["公司不给竞业补偿怎么办","竞业协议公司不付钱能解除吗","没有竞业补偿还要遵守吗","竞业补偿拖欠怎么处理"]',
 '单位不支付竞业补偿金协议还有效吗？ 公司不给竞业补偿怎么办 竞业协议公司不付钱能解除吗 没有竞业补偿还要遵守吗 竞业补偿拖欠怎么处理',
 '劳动合同法规定竞业限制期内用人单位应按月支付经济补偿；因用人单位原因未支付经济补偿达三个月，劳动者请求解除竞业限制约定的，人民法院应予支持。（《中华人民共和国劳动合同法》第二十三条）',
 '[{"law":"中华人民共和国劳动合同法","article":"第二十三条"}]', 'kb_labor', 'labor_law', '竞业限制,解除,未支付补偿'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '单位不支付竞业补偿金协议还有效吗？');

-- ------------------------------------------------------------ 劳务派遣（5）
INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '劳务派遣', '劳务派遣岗位有什么限制？', '["什么岗位能用派遣","派遣工能用在正式岗吗","劳务派遣三性是什么","派遣用工条件"]',
 '劳务派遣岗位有什么限制？ 什么岗位能用派遣 派遣工能用在正式岗吗 劳务派遣三性是什么 派遣用工条件',
 '劳务派遣用工只能在临时性（存续时间不超过六个月）、辅助性（为主营业务岗位提供服务）、替代性（职工脱产学习、休假期间可他人替代）的工作岗位上实施，且用工单位使用被派遣劳动者数量不得超过其用工总量的一定比例。（《中华人民共和国劳动合同法》第六十六条）',
 '[{"law":"中华人民共和国劳动合同法","article":"第六十六条"}]', 'kb_labor', 'labor_law', '劳务派遣,三性,临时性,辅助性,替代性'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '劳务派遣岗位有什么限制？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '劳务派遣', '派遣员工受伤谁来赔？', '["派遣工工伤谁负责","劳务派遣连带责任","派遣单位用工单位责任划分","派遣员工权益谁保障"]',
 '派遣员工受伤谁来赔？ 派遣工工伤谁负责 劳务派遣连带责任 派遣单位用工单位责任划分 派遣员工权益谁保障',
 '劳务派遣单位是用人单位，应当为被派遣劳动者缴纳工伤保险；用工单位给被派遣劳动者造成损害的，劳务派遣单位与用工单位承担连带赔偿责任。（《中华人民共和国劳动合同法》第五十八条、第九十二条）',
 '[{"law":"中华人民共和国劳动合同法","article":"第五十八条"},{"law":"中华人民共和国劳动合同法","article":"第九十二条"}]', 'kb_labor', 'labor_law', '劳务派遣,连带责任,工伤'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '派遣员工受伤谁来赔？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '劳务派遣', '派遣员工能和正式工同工同酬吗？', '["派遣工工资比正式工低合法吗","同工同酬对派遣适用吗","派遣岗待遇有差别吗","派遣工奖金怎么发"]',
 '派遣员工能和正式工同工同酬吗？ 派遣工工资比正式工低合法吗 同工同酬对派遣适用吗 派遣岗待遇有差别吗 派遣工奖金怎么发',
 '被派遣劳动者享有与用工单位的劳动者同工同酬的权利。用工单位应当按照同工同酬原则，对被派遣劳动者与本单位同类岗位的劳动者实行相同的劳动报酬分配办法；无同类岗位的，参照用工单位所在地相同或者相近岗位劳动者的劳动报酬确定。（《中华人民共和国劳动合同法》第六十三条）',
 '[{"law":"中华人民共和国劳动合同法","article":"第六十三条"}]', 'kb_labor', 'labor_law', '劳务派遣,同工同酬,待遇'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '派遣员工能和正式工同工同酬吗？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '劳务派遣', '劳务派遣协议必须书面约定吗？', '["派遣协议要书面的吗","口头派遣协议有效吗","派遣合同怎么写","派遣协议必备内容"]',
 '劳务派遣协议必须书面约定吗？ 派遣协议要书面的吗 口头派遣协议有效吗 派遣合同怎么写 派遣协议必备内容',
 '劳务派遣单位应当与被派遣劳动者订立二年以上的固定期限劳动合同，按月支付劳动报酬；与用工单位订立书面劳务派遣协议，载明派遣岗位、人员数量、派遣期限、劳动报酬和社会保险费的数额与支付方式等。被派遣劳动者无工作期间，派遣单位应按不低于当地最低工资标准按月支付报酬。（《中华人民共和国劳动合同法》第五十八条、第五十九条、第六十二条）',
 '[{"law":"中华人民共和国劳动合同法","article":"第五十八条"},{"law":"中华人民共和国劳动合同法","article":"第五十九条"},{"law":"中华人民共和国劳动合同法","article":"第六十二条"}]', 'kb_labor', 'labor_law', '劳务派遣,协议,二年,书面'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '劳务派遣协议必须书面约定吗？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '劳务派遣', '用工单位能把派遣工再转出去吗？', '["能转派遣吗","二次派遣合法吗","派遣单位再外包可以吗","转包派遣工怎么办"]',
 '用工单位能把派遣工再转出去吗？ 能转派遣吗 二次派遣合法吗 派遣单位再外包可以吗 转包派遣工怎么办',
 '不可以。用工单位不得将被派遣劳动者再派遣到其他用人单位；劳务派遣单位也不得以非全日制用工形式招用被派遣劳动者。（《中华人民共和国劳动合同法》第六十二条、第五十八条）',
 '[{"law":"中华人民共和国劳动合同法","article":"第五十八条"},{"law":"中华人民共和国劳动合同法","article":"第六十二条"}]', 'kb_labor', 'labor_law', '劳务派遣,再派遣,转派遣'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '用工单位能把派遣工再转出去吗？');

-- ---------------------------------------------------------------- 裁员（5）
INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '裁员', '公司什么情况下可以经济性裁员？', '["什么情况能批量裁员","经济性裁员的条件","公司可以裁员吗","裁员的法定情形"]',
 '公司什么情况下可以经济性裁员？ 什么情况能批量裁员 经济性裁员的条件 公司可以裁员吗 裁员的法定情形',
 '需要裁减人员二十人以上或不足二十人但占企业职工总数百分之十以上的，用人单位提前三十日向工会或全体职工说明情况、听取意见并向劳动行政部门报告后，可以裁减人员。法定情形包括依照企业破产法重整、生产经营发生严重困难、企业转产或重大技术革新及经营方式调整经变更合同后仍需裁减、订立合同所依据的客观经济情况发生重大变化致使合同无法履行。（《中华人民共和国劳动合同法》第四十一条第一款）',
 '[{"law":"中华人民共和国劳动合同法","article":"第四十一条"}]', 'kb_labor', 'labor_law', '经济性裁员,条件,程序'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '公司什么情况下可以经济性裁员？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '裁员', '裁员时哪些员工应当优先留用？', '["裁员先裁谁有规定吗","优先留用人员有哪些","老员工裁员受保护吗","裁员留用顺序"]',
 '裁员时哪些员工应当优先留用？ 裁员先裁谁有规定吗 优先留用人员有哪些 老员工裁员受保护吗 裁员留用顺序',
 '裁减人员时应优先留用：与本单位订立较长期限固定期限劳动合同的；订立无固定期限劳动合同的；家庭无其他就业人员、有需要扶养的老人或者未成年人的。用人单位六个月内重新招用人员的，应当通知被裁减人员，并在同等条件下优先招用。（《中华人民共和国劳动合同法》第四十一条第二款、第三款）',
 '[{"law":"中华人民共和国劳动合同法","article":"第四十一条"}]', 'kb_labor', 'labor_law', '裁员,优先留用,无固定期限'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '裁员时哪些员工应当优先留用？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '裁员', '经济性裁员要走什么程序？', '["裁员要先通知工会吗","裁员程序合法流程","批量裁员要报备吗","裁员要提前多久说"]',
 '经济性裁员要走什么程序？ 裁员要先通知工会吗 裁员程序合法流程 批量裁员要报备吗 裁员要提前多久说',
 '需裁减二十人以上或不足二十人但占职工总数百分之十以上的，应提前三十日向工会或者全体职工说明情况，听取工会或职工意见后，将裁减人员方案向劳动行政部门报告，方可裁减人员。未履行法定程序的，可能被认定为违法解除。（《中华人民共和国劳动合同法》第四十一条第一款）',
 '[{"law":"中华人民共和国劳动合同法","article":"第四十一条"}]', 'kb_labor', 'labor_law', '裁员,程序,工会,三十日'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '经济性裁员要走什么程序？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '裁员', '被裁员有经济补偿吗？', '["裁掉有赔偿吗","经济性裁员补偿标准","被优化有N吗","裁员补偿怎么拿"]',
 '被裁员有经济补偿吗？ 裁掉有赔偿吗 经济性裁员补偿标准 被优化有N吗 裁员补偿怎么拿',
 '有。用人单位依照第四十一条规定裁减人员的，应按第四十七条标准支付经济补偿（每满一年支付一个月工资）；用人单位未提前三十日向工会或职工说明并报告的，还可能构成违法解除，需按二倍标准支付赔偿金。（《中华人民共和国劳动合同法》第四十六条第四项、第四十七条、第八十七条）',
 '[{"law":"中华人民共和国劳动合同法","article":"第四十六条"},{"law":"中华人民共和国劳动合同法","article":"第四十七条"}]', 'kb_labor', 'labor_law', '裁员,经济补偿,N,赔偿'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '被裁员有经济补偿吗？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '裁员', '公司说经营困难就可以随时裁员吗？', '["经营困难能随便裁人吗","效益不好可以裁员吗","公司亏损裁员合法吗","什么算经营严重困难"]',
 '公司说经营困难就可以随时裁员吗？ 经营困难能随便裁人吗 效益不好可以裁员吗 公司亏损裁员合法吗 什么算经营严重困难',
 '不能随时裁。需达到生产经营发生严重困难等法定情形，并履行提前三十日说明情况、听取意见、向劳动行政部门报告的程序；不符合条件或未履行程序的，属于违法解除，劳动者可要求继续履行合同或按二倍经济补偿标准主张赔偿金。（《中华人民共和国劳动合同法》第四十一条、第四十八条、第八十七条）',
 '[{"law":"中华人民共和国劳动合同法","article":"第四十一条"},{"law":"中华人民共和国劳动合同法","article":"第四十八条"},{"law":"中华人民共和国劳动合同法","article":"第八十七条"}]', 'kb_labor', 'labor_law', '裁员,经营困难,违法解除,二倍赔偿'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '公司说经营困难就可以随时裁员吗？');

-- ------------------------------------------------------------ 三期保护（5）
INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '三期保护', '三期女职工能被辞退吗？', '["怀孕了公司能辞退吗","孕期产期哺乳期受保护吗","三期女员工不能辞退吗","哺乳期被辞退怎么办"]',
 '三期女职工能被辞退吗？ 怀孕了公司能辞退吗 孕期产期哺乳期受保护吗 三期女员工不能辞退吗 哺乳期被辞退怎么办',
 '孕期、产期、哺乳期女职工，用人单位不得依照第四十条（无过失性辞退）、第四十一条（经济性裁员）的规定解除劳动合同；但存在第三十九条情形（严重违纪等）的除外。违法解除的，女职工可要求继续履行合同或主张赔偿金。（《中华人民共和国劳动合同法》第四十二条第四项、第四十八条、第八十七条）',
 '[{"law":"中华人民共和国劳动合同法","article":"第四十二条"},{"law":"中华人民共和国劳动合同法","article":"第四十八条"},{"law":"中华人民共和国劳动合同法","article":"第八十七条"}]', 'kb_labor', 'labor_law', '三期,孕期,产假,哺乳期,辞退'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '三期女职工能被辞退吗？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '三期保护', '产假有多少天？', '["产假几天","产假九十八天怎么来的","生育假和产假一样吗","产假包括法定节假日吗"]',
 '产假有多少天？ 产假几天 产假九十八天怎么来的 生育假和产假一样吗 产假包括法定节假日吗',
 '女职工生育享受不少于九十八天产假，其中产前可以休假十五天；难产、生育多胞胎的按规定增加产假。各地人口与计划生育条例普遍另有生育奖励假，具体天数以当地规定为准。（《中华人民共和国劳动法》第六十二条、《女职工劳动保护特别规定》第七条）',
 '[{"law":"中华人民共和国劳动法","article":"第六十二条"}]', 'kb_labor', 'labor_law', '产假,九十八天,生育'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '产假有多少天？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '三期保护', '三期期间劳动合同到期怎么办？', '["孕期合同到期能终止吗","哺乳期合同到期怎么办","合同到期时怀孕了","三期合同顺延"]',
 '三期期间劳动合同到期怎么办？ 孕期合同到期能终止吗 哺乳期合同到期怎么办 合同到期时怀孕了 三期合同顺延',
 '劳动合同期满，女职工在孕期、产期、哺乳期的，劳动合同应当顺延至相应情形消失时终止，用人单位不得以合同到期为由终止三期女职工的劳动合同。（《中华人民共和国劳动合同法》第四十五条）',
 '[{"law":"中华人民共和国劳动合同法","article":"第四十五条"}]', 'kb_labor', 'labor_law', '三期,合同到期,顺延,终止'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '三期期间劳动合同到期怎么办？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '三期保护', '孕期不能适应原劳动怎么办？', '["怀孕了做不动重活怎么办","孕期能调岗吗","孕晚期劳动保护","怀孕夜班能拒绝吗"]',
 '孕期不能适应原劳动怎么办？ 怀孕了做不动重活怎么办 孕期能调岗吗 孕晚期劳动保护 怀孕夜班能拒绝吗',
 '不得安排女职工在怀孕期间从事国家规定的第三级体力劳动强度的劳动和孕期禁忌从事的劳动；对怀孕七个月以上的女职工，不得安排其延长工作时间和夜班劳动。不能胜任原劳动的，应当根据医务部门的证明予以减轻劳动量或者安排其他能够适应的劳动。（《中华人民共和国劳动法》第六十一条）',
 '[{"law":"中华人民共和国劳动法","article":"第六十一条"}]', 'kb_labor', 'labor_law', '孕期,劳动保护,调岗,夜班'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '孕期不能适应原劳动怎么办？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '三期保护', '哺乳期有哪些特殊保护？', '["哺乳期有什么待遇","哺乳假每天多久","哺乳期能安排加班吗","未满一岁婴儿母亲保护"]',
 '哺乳期有哪些特殊保护？ 哺乳期有什么待遇 哺乳假每天多久 哺乳期能安排加班吗 未满一岁婴儿母亲保护',
 '不得安排哺乳未满一周岁婴儿的女职工从事国家规定的第三级体力劳动强度的劳动和哺乳期禁忌从事的其他劳动，不得安排其延长工作时间和夜班劳动；用人单位应当在每班劳动时间内为其安排一至两次哺乳时间，每次三十分钟，哺乳时间算作劳动时间。（《中华人民共和国劳动法》第六十三条、《女职工劳动保护特别规定》第九条）',
 '[{"law":"中华人民共和国劳动法","article":"第六十三条"}]', 'kb_labor', 'labor_law', '哺乳期,哺乳时间,劳动保护'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '哺乳期有哪些特殊保护？');

-- ---------------------------------------------------------------- 工伤（5）
INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '工伤', '哪些情形应当认定为工伤？', '["工伤怎么认定","什么情况算工伤","上班受伤算工伤吗","工伤认定范围"]',
 '哪些情形应当认定为工伤？ 工伤怎么认定 什么情况算工伤 上班受伤算工伤吗 工伤认定范围',
 '在工作时间和工作场所内因工作原因受到事故伤害的；工作时间前后在工作场所内从事与工作有关的预备性或收尾性工作受到事故伤害的；在工作时间和工作场所内因履行工作职责受到暴力等意外伤害的；患职业病的；因工外出期间由于工作原因受到伤害或者发生事故下落不明的；在上下班途中受到非本人主要责任的交通事故或城市轨道交通、客运轮渡、火车事故伤害的，应当认定为工伤。（《工伤保险条例》第十四条）',
 '[{"law":"工伤保险条例","article":"第十四条"}]', 'kb_labor', 'labor_law', '工伤,认定,范围'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '哪些情形应当认定为工伤？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '工伤', '工伤停工留薪期工资怎么发？', '["工伤休息期间有工资吗","停工留薪期多久","工伤期间工资标准","受伤不能上班工资怎么办"]',
 '工伤停工留薪期工资怎么发？ 工伤休息期间有工资吗 停工留薪期多久 工伤期间工资标准 受伤不能上班工资怎么办',
 '职工因工作遭受事故伤害或者患职业病需要暂停工作接受工伤医疗的，在停工留薪期内原工资福利待遇不变，由所在单位按月支付；停工留薪期一般不超过十二个月，伤情严重或者情况特殊经设区的市级劳动能力鉴定委员会确认可以适当延长，但延长不得超过十二个月。（《工伤保险条例》第三十三条）',
 '[{"law":"工伤保险条例","article":"第三十三条"}]', 'kb_labor', 'labor_law', '工伤,停工留薪,工资'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '工伤停工留薪期工资怎么发？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '工伤', '工伤后单位能解除劳动合同吗？', '["受了工伤会被辞退吗","工伤期间能辞退员工吗","伤残员工合同到期怎么办","工伤后解除合同限制"]',
 '工伤后单位能解除劳动合同吗？ 受了工伤会被辞退吗 工伤期间能辞退员工吗 伤残员工合同到期怎么办 工伤后解除合同限制',
 '职工在本单位患职业病或者因工负伤并被确认丧失或者部分丧失劳动能力的，用人单位不得依照第四十条、第四十一条的规定解除劳动合同；有此类情形的，劳动合同期满也应当顺延至相应情形消失时终止。违法解除的，职工可主张赔偿金。（《中华人民共和国劳动合同法》第四十二条第二项、第四十五条、第八十七条）',
 '[{"law":"中华人民共和国劳动合同法","article":"第四十二条"},{"law":"中华人民共和国劳动合同法","article":"第四十五条"}]', 'kb_labor', 'labor_law', '工伤,解除,劳动能力,保护'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '工伤后单位能解除劳动合同吗？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '工伤', '单位没缴社保发生工伤怎么办？', '["没交社保工伤谁赔","未缴工伤保险怎么办","没社保受工伤能赔吗","社保断缴工伤待遇"]',
 '单位没缴社保发生工伤怎么办？ 没交社保工伤谁赔 未缴工伤保险怎么办 没社保受工伤能赔吗 社保断缴工伤待遇',
 '应当参加工伤保险而未参加的用人单位职工发生工伤的，由该用人单位按照工伤保险待遇项目和标准支付费用；用人单位补缴应当缴纳的工伤保险费、滞纳金后，新发生的费用由工伤保险基金和用人单位按规定支付。（《工伤保险条例》第六十二条）',
 '[{"law":"工伤保险条例","article":"第六十二条"}]', 'kb_labor', 'labor_law', '工伤,未缴社保,单位支付'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '单位没缴社保发生工伤怎么办？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '工伤', '哪些情形视同工伤？', '["工作岗位突发疾病算工伤吗","抢险救灾受伤算工伤吗","哪些情况视同工伤","猝死算工伤吗"]',
 '哪些情形视同工伤？ 工作岗位突发疾病算工伤吗 抢险救灾受伤算工伤吗 哪些情况视同工伤 猝死算工伤吗',
 '在工作时间和工作岗位突发疾病死亡或者在四十八小时之内经抢救无效死亡的；在抢险救灾等维护国家利益、公共利益活动中受到伤害的；职工原在军队服役因战、因公负伤致残已取得革命伤残军人证，到用人单位后旧伤复发的，视同工伤，旧伤复发的享受除一次性伤残补助金以外的工伤保险待遇。（《工伤保险条例》第十五条）',
 '[{"law":"工伤保险条例","article":"第十五条"}]', 'kb_labor', 'labor_law', '视同工伤,突发疾病,四十八小时'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '哪些情形视同工伤？');

-- ---------------------------------------------------------------- 社保（5）
INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '社保', '用人单位必须给员工缴社保吗？', '["公司一定要交社保吗","上班单位不缴社保违法吗","社保是强制缴纳的吗","入职必须交五险吗"]',
 '用人单位必须给员工缴社保吗？ 公司一定要交社保吗 上班单位不缴社保违法吗 社保是强制缴纳的吗 入职必须交五险吗',
 '是强制的。用人单位和劳动者必须依法参加社会保险，缴纳社会保险费；用人单位无故不缴纳社会保险费的，由劳动行政部门责令其限期缴纳，逾期不缴的可以加收滞纳金。（《中华人民共和国劳动法》第七十二条、第一百条）',
 '[{"law":"中华人民共和国劳动法","article":"第七十二条"},{"law":"中华人民共和国劳动法","article":"第一百条"}]', 'kb_labor', 'labor_law', '社保,强制,五险'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '用人单位必须给员工缴社保吗？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '社保', '试用期要缴社保吗？', '["试用期有五险吗","试用期不交社保合法吗","试用期社保什么时候开始交","入职当月缴社保吗"]',
 '试用期要缴社保吗？ 试用期有五险吗 试用期不交社保合法吗 试用期社保什么时候开始交 入职当月缴社保吗',
 '要。劳动关系自用工之日起建立，试用期包含在劳动合同期限内；用人单位应当自用工之日起三十日内为职工向社会保险经办机构申请办理社会保险登记，试用期不得作为不缴社保的理由。（《中华人民共和国劳动法》第七十二条、《中华人民共和国劳动合同法》第十九条）',
 '[{"law":"中华人民共和国劳动法","article":"第七十二条"},{"law":"中华人民共和国劳动合同法","article":"第十九条"}]', 'kb_labor', 'labor_law', '试用期,社保,三十日'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '试用期要缴社保吗？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '社保', '可以让员工签自愿放弃社保协议吗？', '["自愿不交社保协议有效吗","放弃社保声明有法律效力吗","签了不缴社保协议怎么办","员工自愿弃保合法吗"]',
 '可以让员工签自愿放弃社保协议吗？ 自愿不交社保协议有效吗 放弃社保声明有法律效力吗 签了不缴社保协议怎么办 员工自愿弃保合法吗',
 '无效。缴纳社会保险费是用人单位和劳动者的法定义务，具有强制性，双方约定或劳动者单方承诺自愿放弃社保的，不影响劳动者依法要求补缴的权利；用人单位未依法缴纳的，劳动者还可据此解除劳动合同并主张经济补偿。（《中华人民共和国劳动法》第七十二条、《中华人民共和国劳动合同法》第三十八条、第四十六条）',
 '[{"law":"中华人民共和国劳动法","article":"第七十二条"},{"law":"中华人民共和国劳动合同法","article":"第三十八条"},{"law":"中华人民共和国劳动合同法","article":"第四十六条"}]', 'kb_labor', 'labor_law', '放弃社保,无效,补缴,经济补偿'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '可以让员工签自愿放弃社保协议吗？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '社保', '社保缴费基数怎么确定？', '["社保基数按什么算","社保缴费基数标准","社保可以按最低标准交吗","五险一金基数怎么定"]',
 '社保缴费基数怎么确定？ 社保基数按什么算 社保缴费基数标准 社保可以按最低标准交吗 五险一金基数怎么定',
 '社保缴费基数一般按职工本人上年度月平均工资确定，新入职员工按起薪当月工资核定；本人工资低于当地上年度职工月平均工资百分之六十的按百分之六十核定，高于百分之三百的按百分之三百核定。用人单位应按实际工资申报，不足额缴纳的劳动者可要求补缴。（《中华人民共和国劳动法》第七十二条）',
 '[{"law":"中华人民共和国劳动法","article":"第七十二条"}]', 'kb_labor', 'labor_law', '社保,缴费基数,平均工资'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '社保缴费基数怎么确定？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '社保', '公司不缴社保员工能辞职并要补偿吗？', '["不交社保能离职吗","未缴社保辞职有补偿吗","公司不缴社保怎么维权","社保断缴能要赔偿吗"]',
 '公司不缴社保员工能辞职并要补偿吗？ 不交社保能离职吗 未缴社保辞职有补偿吗 公司不缴社保怎么维权 社保断缴能要赔偿吗',
 '能。用人单位未依法为劳动者缴纳社会保险费的，劳动者可以解除劳动合同，并要求用人单位按第四十七条标准支付经济补偿；同时可向社保经办机构或劳动监察投诉，要求补缴。（《中华人民共和国劳动合同法》第三十八条第三项、第四十六条第一项、第四十七条）',
 '[{"law":"中华人民共和国劳动合同法","article":"第三十八条"},{"law":"中华人民共和国劳动合同法","article":"第四十六条"},{"law":"中华人民共和国劳动合同法","article":"第四十七条"}]', 'kb_labor', 'labor_law', '未缴社保,解除,经济补偿,投诉'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '公司不缴社保员工能辞职并要补偿吗？');

-- ------------------------------------------------------------ 仲裁时效（5）
INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '仲裁时效', '劳动争议仲裁时效是多久？', '["劳动仲裁有效期多久","仲裁时效一年从哪天算","劳动争议多久内必须仲裁","仲裁有时效限制吗"]',
 '劳动争议仲裁时效是多久？ 劳动仲裁有效期多久 仲裁时效一年从哪天算 劳动争议多久内必须仲裁 仲裁有时效限制吗',
 '劳动争议申请仲裁的时效期间为一年，自当事人知道或者应当知道其权利被侵害之日起计算。劳动关系存续期间因拖欠劳动报酬发生争议的，不受一年仲裁时效期间的限制；但劳动关系终止的，应当自劳动关系终止之日起一年内提出。（《中华人民共和国劳动争议调解仲裁法》第二十七条）',
 '[{"law":"中华人民共和国劳动争议调解仲裁法","article":"第二十七条"}]', 'kb_labor', 'labor_law', '仲裁时效,一年,劳动报酬'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '劳动争议仲裁时效是多久？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '仲裁时效', '仲裁时效会中断吗？', '["仲裁时效能重新计算吗","时效中断的情形","仲裁时效中止中断","过了时效还能仲裁吗"]',
 '仲裁时效会中断吗？ 仲裁时效能重新计算吗 时效中断的情形 仲裁时效中止中断 过了时效还能仲裁吗',
 '会。一方向对方主张权利、向有关部门请求权利救济或者对方同意履行义务的，仲裁时效中断，从中断时起时效期间重新计算；因不可抗力或者有其他正当理由的，仲裁时效中止，从中止时效的原因消除之日起继续计算。（《中华人民共和国劳动争议调解仲裁法》第二十七条第二款、第三款）',
 '[{"law":"中华人民共和国劳动争议调解仲裁法","article":"第二十七条"}]', 'kb_labor', 'labor_law', '仲裁时效,中断,中止'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '仲裁时效会中断吗？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '仲裁时效', '拖欠工资的仲裁时效是多久？', '["欠薪多久内能仲裁","拖欠工资有追溯期吗","在职期间欠薪时效","离职后欠薪仲裁时效"]',
 '拖欠工资的仲裁时效是多久？ 欠薪多久内能仲裁 拖欠工资有追溯期吗 在职期间欠薪时效 离职后欠薪仲裁时效',
 '劳动关系存续期间因拖欠劳动报酬发生争议的，劳动者申请仲裁不受一年仲裁时效期间的限制；但劳动关系终止的，应当自劳动关系终止之日起一年内提出。（《中华人民共和国劳动争议调解仲裁法》第二十七条第四款）',
 '[{"law":"中华人民共和国劳动争议调解仲裁法","article":"第二十七条"}]', 'kb_labor', 'labor_law', '拖欠工资,仲裁时效,在职'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '拖欠工资的仲裁时效是多久？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '仲裁时效', '劳动仲裁收费吗？', '["申请仲裁要交钱吗","劳动仲裁费用谁承担","仲裁免费吗","仲裁费多少钱"]',
 '劳动仲裁收费吗？ 申请仲裁要交钱吗 劳动仲裁费用谁承担 仲裁免费吗 仲裁费多少钱',
 '不收费。劳动争议仲裁不收取费用，仲裁委员会经费由财政予以保障；对仲裁裁决不服向法院起诉的，诉讼费按劳动争议案件规定收取。（《中华人民共和国劳动争议调解仲裁法》第五十三条）',
 '[{"law":"中华人民共和国劳动争议调解仲裁法","article":"第五十三条"}]', 'kb_labor', 'labor_law', '仲裁,免费,不收费'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '劳动仲裁收费吗？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '仲裁时效', '对劳动仲裁结果不服怎么办？', '["仲裁输了能起诉吗","不服仲裁裁决怎么办","仲裁后多久内起诉","终局仲裁能上诉吗"]',
 '对劳动仲裁结果不服怎么办？ 仲裁输了能起诉吗 不服仲裁裁决怎么办 仲裁后多久内起诉 终局仲裁能上诉吗',
 '追索劳动报酬、工伤医疗费、经济补偿或者赔偿金，不超过当地月最低工资标准十二个月金额的争议等一裁终局裁决，劳动者不服的可自收到裁决书之日起十五日内向人民法院提起诉讼；其他裁决不服的，也可在十五日内起诉，期满不起诉的裁决即发生法律效力。（《中华人民共和国劳动争议调解仲裁法》第四十七条、第四十八条）',
 '[{"law":"中华人民共和国劳动争议调解仲裁法","article":"第四十七条"},{"law":"中华人民共和国劳动争议调解仲裁法","article":"第四十八条"}]', 'kb_labor', 'labor_law', '仲裁,起诉,十五日,一裁终局'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '对劳动仲裁结果不服怎么办？');

-- ------------------------------------------------------------ 合同订立（5）
INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '合同订立', '入职后多久内必须签劳动合同？', '["多久签合同","入职一个月内签合同吗","不签合同多久算违法","用工后什么时候签合同"]',
 '入职后多久内必须签劳动合同？ 多久签合同 入职一个月内签合同吗 不签合同多久算违法 用工后什么时候签合同',
 '已建立劳动关系未同时订立书面劳动合同的，应当自用工之日起一个月内订立；用人单位自用工之日起满一年不与劳动者订立书面劳动合同的，视为用人单位与劳动者已订立无固定期限劳动合同。（《中华人民共和国劳动合同法》第十条、第十四条第三款）',
 '[{"law":"中华人民共和国劳动合同法","article":"第十条"},{"law":"中华人民共和国劳动合同法","article":"第十四条"}]', 'kb_labor', 'labor_law', '劳动合同,订立,一个月'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '入职后多久内必须签劳动合同？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '合同订立', '公司不签劳动合同有什么后果？', '["不签合同怎么赔","没签合同有二倍工资吗","未签书面合同处罚","不签合同仲裁能赢吗"]',
 '公司不签劳动合同有什么后果？ 不签合同怎么赔 没签合同有二倍工资吗 未签书面合同处罚 不签合同仲裁能赢吗',
 '用人单位自用工之日起超过一个月不满一年未与劳动者订立书面劳动合同的，应当向劳动者每月支付二倍的工资（最长十一个月）；满一年未订立的，视为已订立无固定期限劳动合同，应当立即补订书面合同。（《中华人民共和国劳动合同法》第八十二条第一款、第十四条第三款）',
 '[{"law":"中华人民共和国劳动合同法","article":"第八十二条"},{"law":"中华人民共和国劳动合同法","article":"第十四条"}]', 'kb_labor', 'labor_law', '未签合同,二倍工资,第八十二条'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '公司不签劳动合同有什么后果？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '合同订立', '劳动合同应当具备哪些条款？', '["合同必备条款有哪些","劳动合同要写什么内容","合同里必须有什么","劳动合同条款清单"]',
 '劳动合同应当具备哪些条款？ 合同必备条款有哪些 劳动合同要写什么内容 合同里必须有什么 劳动合同条款清单',
 '应当具备：用人单位的名称、住所和法定代表人或者主要负责人；劳动者的姓名、住址和居民身份证号码；劳动合同期限；工作内容和工作地点；工作时间和休息休假；劳动报酬；社会保险；劳动保护、劳动条件和职业危害防护；法律、法规规定应当纳入劳动合同的其他事项。（《中华人民共和国劳动合同法》第十七条第一款）',
 '[{"law":"中华人民共和国劳动合同法","article":"第十七条"}]', 'kb_labor', 'labor_law', '劳动合同,必备条款,第十七条'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '劳动合同应当具备哪些条款？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '合同订立', '劳动合同什么时候生效？', '["合同签字就生效吗","劳动合同生效时间","没签字合同有效吗","电子劳动合同生效吗"]',
 '劳动合同什么时候生效？ 合同签字就生效吗 劳动合同生效时间 没签字合同有效吗 电子劳动合同生效吗',
 '劳动合同由用人单位与劳动者协商一致，并经用人单位与劳动者在劳动合同文本上签字或者盖章生效；劳动合同文本由用人单位和劳动者各执一份。双方实际履行了主要义务且对方接受的，合同亦成立生效。（《中华人民共和国劳动合同法》第十六条、第十七条第二款）',
 '[{"law":"中华人民共和国劳动合同法","article":"第十六条"}]', 'kb_labor', 'labor_law', '劳动合同,生效,签字'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '劳动合同什么时候生效？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '合同订立', '只约定试用期不签正式合同可以吗？', '["只签试用期合同合法吗","试用期合同有法律效力吗","单独试用合同怎么处理","试用合同算劳动合同吗"]',
 '只约定试用期不签正式合同可以吗？ 只签试用期合同合法吗 试用期合同有法律效力吗 单独试用合同怎么处理 试用合同算劳动合同吗',
 '不可以。试用期包含在劳动合同期限内；劳动合同仅约定试用期的，试用期不成立，该期限为劳动合同期限，应按该期限享受正式员工待遇并履行正式合同义务。（《中华人民共和国劳动合同法》第十九条第四款）',
 '[{"law":"中华人民共和国劳动合同法","article":"第十九条"}]', 'kb_labor', 'labor_law', '试用期,劳动合同,仅约定试用期'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '只约定试用期不签正式合同可以吗？');

-- ------------------------------------------------------------ 合同解除（5）
INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '合同解除', '员工辞职需要提前多久通知？', '["辞职要提前说吗","离职提前几天申请","正式员工辞职提前多久","试用期辞职提前几天"]',
 '员工辞职需要提前多久通知？ 辞职要提前说吗 离职提前几天申请 正式员工辞职提前多久 试用期辞职提前几天',
 '劳动者提前三十日以书面形式通知用人单位，可以解除劳动合同；试用期内提前三日通知即可解除，无需用人单位批准。（《中华人民共和国劳动合同法》第三十七条）',
 '[{"law":"中华人民共和国劳动合同法","article":"第三十七条"}]', 'kb_labor', 'labor_law', '辞职,提前通知,三十日,三日'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '员工辞职需要提前多久通知？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '合同解除', '公司拖欠工资员工可以直接走人吗？', '["欠薪能直接离职吗","拖欠工资怎么被迫离职","公司欠薪能马上走吗","欠薪离职要补偿吗"]',
 '公司拖欠工资员工可以直接走人吗？ 欠薪能直接离职吗 拖欠工资怎么被迫离职 公司欠薪能马上走吗 欠薪离职要补偿吗',
 '可以。用人单位未及时足额支付劳动报酬或者未依法缴纳社会保险费等情形下，劳动者可以解除劳动合同，无需提前三十日通知，并可按第四十六条第一项主张经济补偿。（《中华人民共和国劳动合同法》第三十八条、第四十六条）',
 '[{"law":"中华人民共和国劳动合同法","article":"第三十八条"},{"law":"中华人民共和国劳动合同法","article":"第四十六条"}]', 'kb_labor', 'labor_law', '拖欠工资,被迫解除,经济补偿'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '公司拖欠工资员工可以直接走人吗？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '合同解除', '公司违法辞退员工怎么赔偿？', '["违法辞退怎么赔","无故被辞退赔偿标准","二倍赔偿是怎么算的","违法解除劳动合同赔偿"]',
 '公司违法辞退员工怎么赔偿？ 违法辞退怎么赔 无故被辞退赔偿标准 二倍赔偿是怎么算的 违法解除劳动合同赔偿',
 '用人单位违反本法规定解除或者终止劳动合同的，劳动者要求继续履行劳动合同的，用人单位应当继续履行；劳动者不要求继续履行或者劳动合同已经不能继续履行的，用人单位应当依照经济补偿标准的二倍向劳动者支付赔偿金。（《中华人民共和国劳动合同法》第四十八条、第八十七条）',
 '[{"law":"中华人民共和国劳动合同法","article":"第四十八条"},{"law":"中华人民共和国劳动合同法","article":"第八十七条"}]', 'kb_labor', 'labor_law', '违法解除,赔偿金,二倍,第四十八条'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '公司违法辞退员工怎么赔偿？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '合同解除', '离职时公司扣押工资怎么办？', '["离职工资被扣怎么办","公司拖着不给离职薪资","离职经济补偿什么时候给","扣押工资怎么维权"]',
 '离职时公司扣押工资怎么办？ 离职工资被扣怎么办 公司拖着不给离职薪资 离职经济补偿什么时候给 扣押工资怎么维权',
 '用人单位应当在解除或者终止劳动合同时出具证明，并在十五日内为劳动者办理档案和社会保险关系转移手续；依照本法有关规定应当向劳动者支付经济补偿的，在办结工作交接时支付。逾期不支付的，由劳动行政部门责令按应付金额百分之五十以上百分之一百以下的标准加付赔偿金。（《中华人民共和国劳动合同法》第五十条、第八十五条）',
 '[{"law":"中华人民共和国劳动合同法","article":"第五十条"},{"law":"中华人民共和国劳动合同法","article":"第八十五条"}]', 'kb_labor', 'labor_law', '离职,工资,扣押,加付赔偿金'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '离职时公司扣押工资怎么办？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '合同解除', '离职单位拒不开离职证明怎么办？', '["公司不给开离职证明","没有离职证明能入职吗","离职证明必须开吗","拒开离职证明违法吗"]',
 '离职单位拒不开离职证明怎么办？ 公司不给开离职证明 没有离职证明能入职吗 离职证明必须开吗 拒开离职证明违法吗',
 '用人单位应当在解除或者终止劳动合同时出具解除或者终止劳动合同的证明。未出具的，劳动者可向劳动行政部门投诉；因此给劳动者造成损害的（如无法入职新单位的工资损失），应当承担赔偿责任。（《中华人民共和国劳动合同法》第五十条第一款、第八十九条）',
 '[{"law":"中华人民共和国劳动合同法","article":"第五十条"},{"law":"中华人民共和国劳动合同法","article":"第八十九条"}]', 'kb_labor', 'labor_law', '离职证明,拒不开具,赔偿'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '离职单位拒不开离职证明怎么办？');

-- ------------------------------------------------- 民法典向（5，验证跨法链路）
INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '民事合同', '定金和订金有什么区别？', '["定金能退吗","订金和定金一样吗","定金罚则是什么","交定金后悔了怎么办"]',
 '定金和订金有什么区别？ 定金能退吗 订金和定金一样吗 定金罚则是什么 交定金后悔了怎么办',
 '定金是担保方式：给付定金的一方不履行债务或者履行债务不符合约定，致使不能实现合同目的的，无权请求返还定金；收受定金的一方不履行债务或者履行债务不符合约定，致使不能实现合同目的的，应当双倍返还定金；定金不得超过主合同标的额的百分之二十。订金通常视为预付款，不适用定金罚则。（《中华人民共和国民法典》第五百八十六条、第五百八十七条）',
 '[{"law":"中华人民共和国民法典","article":"第五百八十六条"},{"law":"中华人民共和国民法典","article":"第五百八十七条"}]', 'kb_civil', 'civil_code', '定金,订金,定金罚则,担保'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '定金和订金有什么区别？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '婚姻家庭', '诉讼离婚法院什么情况会判离？', '["起诉离婚能判离吗","什么条件法院判离婚","感情破裂怎么认定","离婚诉讼判离标准"]',
 '诉讼离婚法院什么情况会判离？ 起诉离婚能判离吗 什么条件法院判离婚 感情破裂怎么认定 离婚诉讼判离标准',
 '人民法院审理离婚案件，应当进行调解；感情确已破裂且调解无效的，应当准予离婚。法定情形包括重婚或者与他人同居，实施家庭暴力或者虐待、遗弃家庭成员，有赌博、吸毒等恶习屡教不改，因感情不和分居满二年等。经判决不准离婚后双方又分居满一年一方再次起诉的，应当准予离婚。（《中华人民共和国民法典》第一千零七十九条）',
 '[{"law":"中华人民共和国民法典","article":"第一千零七十九条"}]', 'kb_civil', 'civil_code', '离婚,感情破裂,诉讼离婚'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '诉讼离婚法院什么情况会判离？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '遗产继承', '法定继承的顺序是怎样的？', '["遗产谁先继承","第一顺序继承人有哪些","法定继承人顺序","父母子女配偶继承顺序"]',
 '法定继承的顺序是怎样的？ 遗产谁先继承 第一顺序继承人有哪些 法定继承人顺序 父母子女配偶继承顺序',
 '遗产按照下列顺序继承：第一顺序为配偶、子女、父母；第二顺序为兄弟姐妹、祖父母、外祖父母。继承开始后由第一顺序继承人继承，第二顺序继承人不继承；没有第一顺序继承人继承的，由第二顺序继承人继承。（《中华人民共和国民法典》第一千一百二十七条）',
 '[{"law":"中华人民共和国民法典","article":"第一千一百二十七条"}]', 'kb_civil', 'civil_code', '继承,顺序,第一顺序'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '法定继承的顺序是怎样的？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '侵权责任', '被侵权人可以主张哪些赔偿？', '["侵权赔偿包括什么","人身伤害能赔哪些","侵权损害赔偿范围","精神损失能赔吗"]',
 '被侵权人可以主张哪些赔偿？ 侵权赔偿包括什么 人身伤害能赔哪些 侵权损害赔偿范围 精神损失能赔吗',
 '侵害他人造成人身损害的，应当赔偿医疗费、护理费、交通费、营养费、住院伙食补助费等为治疗和康复支出的合理费用，以及因误工减少的收入；造成残疾的还应当赔偿辅助器具费和残疾赔偿金，造成死亡的还应当赔偿丧葬费和死亡赔偿金；造成严重精神损害的，被侵权人有权请求精神损害赔偿。（《中华人民共和国民法典》第一千一百七十九条、第一千一百八十三条）',
 '[{"law":"中华人民共和国民法典","article":"第一千一百七十九条"},{"law":"中华人民共和国民法典","article":"第一千一百八十三条"}]', 'kb_civil', 'civil_code', '侵权,赔偿范围,精神损害'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '被侵权人可以主张哪些赔偿？');

INSERT INTO qa_pairs (category, question_std, question_variants, search_text, answer, law_refs, kb_id, intent, keywords)
SELECT '民间借贷', '民间借贷利率司法保护上限是多少？', '["借钱利息多少合法","民间借贷利率上限","高利贷怎么界定","借款利息法院支持多少"]',
 '民间借贷利率司法保护上限是多少？ 借钱利息多少合法 民间借贷利率上限 高利贷怎么界定 借款利息法院支持多少',
 '出借人请求借款人按照约定支付利息的，人民法院保护的利率上限为合同成立时一年期贷款市场报价利率（LPR）的四倍；借贷双方约定的利率超过该上限的，超过部分的利息约定无效，借款人可以请求返还已支付的超过部分利息。（《中华人民共和国民法典》第六百八十条）',
 '[{"law":"中华人民共和国民法典","article":"第六百八十条"}]', 'kb_civil', 'civil_code', '民间借贷,利率,LPR四倍'
WHERE NOT EXISTS (SELECT 1 FROM qa_pairs WHERE question_std = '民间借贷利率司法保护上限是多少？');
