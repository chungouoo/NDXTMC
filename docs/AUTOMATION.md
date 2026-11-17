# 每日自动晨报流程

开始日期：2026-11-18。时区：Asia/Shanghai。

每天 08:00 由原「纳指晨报」任务启动，顺序执行：

1. 更新 `.github/ndxtmc-trigger.json`；同一北京时间日期按 request_id 去重。
2. 运行 GitHub 工作流，获取 Nasdaq 指数与名单，按当前策略获取整组个股数据。
3. 独立验算 ZIP、核对当期日期/覆盖率/manifest，发布 Release。
4. 工作流原子发布 `data/latest_morning_input.json`，明确 READY、触发身份、提交、run_id、Release、实际交易日和供应商。
5. 同一个晨报任务等待此轮成功后读取 JSON，独立复算 RSI/均线/回撤/相对表现，并核实 VIX、VXN、CNN情绪、利率、估值和新闻。
6. 依照原有研究与风险规则，在原晨报对话推送完整分析和数据来源链接。

08:00 是开始时间；完成时间取决于数据源与研究耗时。单独的 08:00 数据包任务合并停用，避免重复触发；其他晨报任务保持原配置。

## 准入条件

- 仓库仍为 Public，使用标准免费 Linux 运行器，不新增付费 API 或服务器。
- 只能接受本轮 request_id、source_commit_sha 和 run_id 匹配的交接文件。
- beijing_request_date 必须属于本轮日期；实际行情日期允许为最近已完成交易日，休市时明确说明。
- DAILY_READY、Quality Gate、校验结果和完整成分覆盖率全部通过。历史回放、日期冲突、缺失成分、审计错误不能进入正式最新数据分析。
- 指数源与个股供应商必须如实披露；不把 Yahoo 个股历史冒充 Nasdaq 官方个股历史。
- 不同数据包的内容不能拼接，不能把上一次成功的 latest 链接当成本次成功。
- 取数或校验失败时报告失败环节；如引用旧基准，只能显著标旧并降低结论确信度。

## 交接文件

JSON 包含 `data`（原 07_morning_report_input）、`manifest`、`validation`、`quality_report_markdown` 及 `index_history`（三条完整官方指数日期/收盘序列）。任务可用代码工具从 JSON 独立复算，无需等待用户上传 ZIP；ZIP 与审计包仍保留下载入口。

发布只更新这个数据文件，路径不在生成工作流的 push 监听范围内，不会形成自动运行循环。用户的持仓、私人研究提示词与 ChatGPT 对话不会写入 GitHub。
