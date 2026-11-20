# 每日自动晨报流程

时区为 Asia/Shanghai。

1. 07:23 由 GitHub Actions 定时取数，获取 Nasdaq 指数与成分名单以及整组个股日线。07:41 有第二次定时检查，已有当日成功交接时跳过重新取数。
2. 独立验算 ZIP、核对交易日、覆盖率和 manifest。发布 Release 后，工作流原子写入 `data/latest_morning_input.json`。
3. 08:00 原「纳指晨报」任务只读核对当天交接与对应的成功运行，复算 RSI、均线、回撤和相对表现，并核实 VIX、VXN、CNN 情绪、利率、估值和新闻。
4. 依照原有研究与风险规则，在原晨报对话推送完整分析与数据来源。

定时运行的请求 ID 为 `ndxtmc-daily-YYYY-MM-DD`，日期以对应 GitHub 运行创建时间换算为北京时间。iPhone 手动补跑使用该次运行的独立请求 ID。旧触发文件仅在该文件实际被本次提交更新时用于其原有方式。

08:00 为报告任务开始时间。GitHub 排队、取数和研究的用时会影响实际送达时间。

## 准入条件

- 仓库仍为 Public，使用标准免费 Linux 运行器，不新增付费 API 或服务器。
- 核对当日交接的 request_id、source_commit_sha 和 run_id 与实际成功运行相符。
- beijing_request_date 必须属于本轮日期；实际行情日期允许为最近已完成交易日，休市时明确说明。
- DAILY_READY、Quality Gate、校验结果和完整成分覆盖率全部通过。历史回放、日期冲突、缺失成分、审计错误不能进入正式最新数据分析。
- 指数源与个股供应商必须如实披露；不把 Yahoo 个股历史冒充 Nasdaq 官方个股历史。
- 不同数据包的内容不能拼接，不能把上一次成功的 latest 链接当成本次成功。
- 取数或校验失败时报告失败环节；如引用旧基准，只能显著标旧并降低结论确信度。

## 交接文件

JSON 包含 `data`（原 07_morning_report_input）、`manifest`、`validation`、`quality_report_markdown` 及 `index_history`（三条完整官方指数日期/收盘序列）。任务可用代码工具从 JSON 独立复算，无需等待用户上传 ZIP；ZIP 与审计包仍保留下载入口。

发布只更新这个数据文件，路径不在生成工作流的 push 监听范围内，不会形成自动运行循环。用户的持仓、私人研究提示词与 ChatGPT 对话不会写入 GitHub。
