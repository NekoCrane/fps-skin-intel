# 武器外观采集流水线

目标：正常周更由Python直接完成，脚本不调用任何模型API；模型只处理新增且无法按规则确定的例外。历史资料不设年份限制，优先发布最新官方资讯。网页继续沿用本仓库原有页面。

## 执行入口

在仓库根目录运行：

```powershell
# 查看执行计划，不联网、不写入
py -3 scripts/run_weapon_weekly.py --plan

# 正常周更：发现、目录同步、官方资讯合并、校验、构建、推送、线上核对
py -3 -X utf8 scripts/run_weapon_weekly.py --publish

# 仅验证三角洲的缓存复建，不联网、不发布
py -3 -X utf8 scripts/run_weapon_weekly.py --games delta-force --offline --skip-build

# 只检查指定游戏的官网资讯，不重跑大型历史目录
py -3 -X utf8 scripts/run_weapon_weekly.py --news-only --games call-of-duty cod-mobile apex valorant --skip-build

# 强制完整目录检查；每月第一个周一自动采用此模式
py -3 -X utf8 scripts/run_weapon_weekly.py --full --publish
```

首次需要Python 3.10+、Node.js、curl、Git及现有采集器依赖Pillow、requests、Windows truststore。凭据继续由本机Git登录管理，脚本不保存口令。发布使用weapon-skins分支及相邻的fps-skin-publish/gh-pages独立checkout，检查远程仓库与分支；源代码须先提交，发布区须干净。

## 获取和整理方式

| 资源 | 机器处理 | 模型参与范围 |
|---|---|---|
| Valorant-API、社区JSON、图鉴 | 原采集器读取武器/皮肤ID、等级、炫彩，按稳定ID去重 | 新增身份冲突才复核 |
| COD、CODM、Apex、VAL官方新闻 | 获取公开索引→读取真实文章链接→解析标题、日期、图片和外观关键词→合并最多30条官方资讯 | 模板变动或缺失日期/图片进入例外；不编造字段 |
| B站官方投稿 | 固定账号ID发现新BVID→核作者→读取标题和简介→规则识别单件武器；三角洲复用已有具名拆分器 | 未具名枪型、跨语言对应和视频画面核验 |
| CFM、CFHD、CODM国服、端游、Apex目录 | 复用原有专用脚本，正常刷新或断点续跑，失败恢复原游戏快照 | 采集失败仅报告原因，不让模型反复盲试 |
| 官方CFHD活动页 | 文本变化指纹监测；暂不从月份或页面修订日猜发布时间 | 页面变化且没有确定的文章结构时，提供有界摘录 |
| 图片、GIF和视频 | 原采集器验证图片解码/尺寸/哈希，GIF检查帧数，保留出处；视频保留原站链接 | 脚本没有观看视频，也不会自动写“已看过” |

信源、官方账号、标题过滤和采集命令集中在`data/weapon-skins/source-registry.json`。CFM使用已核账号315554376（穿越火线），只接受明确带CF手游/枪战王者/CFM标识的投稿，避免混入端游资讯。CFHD活动页为变化监测，不能称为全量官方资讯解析。

## 缓存与限频

- 新增官网资讯获取支持ETag和Last-Modified条件请求；304读取保留的原始内容并校验哈希。同一URL在一轮中仅请求一次，跨COD端手游共享官网索引缓存。源不支持条件请求时仍需下载，不宣称零流量。
- 三角洲默认只更新新BVID及最近14天的元数据，旧记录复用既有证据；每月完整复查。14天是重复检查窗口，不是收录限制。首次机器缺缓存时，可复用已提交历史元数据；不能把缓存复用说成新联网检查。
- B站列表接口返回412/429/错误码时停止该路径，记录失败，不做签名、身份或验证码绕行。官网失败不清空已有资讯，某游戏失败不阻断其余游戏。
- 图片缓存仍由各原采集器维护；尚未把所有历史采集器迁移成HTTP条件请求。原来的逐页面解析是CPU和网络任务，本身不消耗模型额度。

## 输出与异常

所有运行日志在被Git忽略的`local-only/weapon-skins/weekly/`：

| 文件 | 内容 |
|---|---|
| last-run.json | 七款结果、新增/修改数量、信源状态、请求与字节统计、校验、发布和线上核对 |
| source-health.json | 每个资讯源最近尝试与最近成功时间 |
| review-queue.json | 持久异常队列，按来源、原因和内容指纹去重 |
| model-batch.json | 本轮最多5条新增待判断事项，每条最多1200字摘录；不包含整库或长日志 |
| 日期目录/*.log | 各原脚本完整日志，仅失败时按需读取 |

同一内容指纹的异常不会每周重复提交模型；队列中的`status`可标为resolved/ignored，确定的对应关系应补进规则或具名证据。网络失败的`requiresModel=false`，不会为重试网络而调用模型。脚本没有任何LLM SDK、密钥或付费接口。

退出码0为成功（或离线验证）；3表示部分信源仍有缺口，成功结果可发布但不能宣称全部最新；1为校验/发布等失败，2为并发或中断锁。强制终止时锁可能保留，先核对没有仍在运行的同一流水线后再清理，避免重复发布。

## 无模型定时执行

```powershell
# 查看任务注册计划
.\scripts\install_weapon_weekly_task.ps1 -WhatIf

# 注册每周一北京时间11:00的本机计划任务
.\scripts\install_weapon_weekly_task.ps1
```

任务名为`NekoCrane-FpsSkinIntel-Weekly`。使用当前Windows用户、普通权限、不保存密码；本机须开机并登录、网络可用，错过时间由系统在条件恢复后补跑。重复实例忽略，最长运行3小时。注册并核对成功后再暂停旧的Codex周更，避免双跑和模型调度费用。计划任务本身只运行Python，不会自动唤醒模型；需要复核时由用户按需读取小型异常包。

## 验证

```powershell
py -3 -X utf8 scripts/test_weapon_weekly.py
py -3 -X utf8 scripts/test_weapon_delta_bilibili.py
node scripts/validate_weapon_skins.cjs
```

覆盖条件请求和Windows换行、限频熔断、不覆盖成功缓存、文章主体证据、跨站链接拒绝、资讯保留、异常去重和单游戏失败回滚。发布前构建容量检查，发布后核对线上manifest及所选游戏压缩数据的实际字节；GitHub Pages尚未部署完时有限等待，超时如实报告。
