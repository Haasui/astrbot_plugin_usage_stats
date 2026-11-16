# 0.3.0 验证与上机检查

## 验证范围

当前自动化覆盖统计纯逻辑、SQLite 查询、AstrBot 装配桩与网页 DOM 桩。它不证明真实供应商的 usage、首 Token 时间或账单准确性，也不替代 AstrBot 实际页面的视觉与权限检查。

源码入口见 [装配层](<../main.py>)、[明细存储与查询](<../event_store.py>)、[采集包装器](<../patch.py>)、[统计模型](<../stats.py>)、[页面](<../views/usage/index.html>)。历史上机记录保存在 [旧验证记录](<VERIFY.md>)。

## 自动化命令

在插件目录运行以下命令。需要 Python 和 Node，不需要安装 AstrBot、pytest 或图表依赖。

~~~bash
python tests/test_core.py
python tests/test_store.py
python tests/test_patch.py
python tests/test_invariants.py
python tests/test_config.py
python tests/test_contract.py
python tests/test_log_tail.py
python tests/test_views.py
python tests/test_event_store.py
python tests/test_cancellation_counts.py
python tests/test_integration.py
python tests/test_recovery_disk.py
node --check views/usage/app.js
node --check views/usage/dashboard.js
node tests/dashboard_smoke.mjs
node tests/view_smoke.mjs
~~~

| 覆盖 | 检查位置 | 可观察结果 |
| --- | --- | --- |
| 缓存标记、三类 Token 与未知字段 | [统计测试](<../tests/test_core.py>) | 缓存上报状态往返保留，思考和写入不重复加总 |
| 同步、协程、流式失败与取消 | [包装器测试](<../tests/test_patch.py>) | 原异常不变，每次调用仅记一次，super 与独立任务不互相误抑制 |
| 日桶、取消与合并 | [存储测试](<../tests/test_store.py>)、[取消计数测试](<../tests/test_cancellation_counts.py>) | 计数与三类 Token 跨快照和聚合路径一致 |
| 过滤、半开时间、分页、价格和导出 | [查询测试](<../tests/test_event_store.py>) | 缺价不当免费；零价合法；导出只含匹配明细并限制 5000 行 |
| 默认值、鉴权、队列重试与重置 | [装配测试](<../tests/test_integration.py>) | 默认不保存明细；未登录不能访问新接口或保存价格；部分失败不误报完成 |
| SQLite 已提交、JSON 替换失败后的重启 | [真实文件恢复测试](<../tests/test_recovery_disk.py>) | 旧纯日历史与新明细均保留，重复读取不增加计数 |
| 筛选竞态、图表、价格草稿与日志状态 | [页面冒烟](<../tests/view_smoke.mjs>)、[图表算法冒烟](<../tests/dashboard_smoke.mjs>) | 旧响应不覆盖新选择，隐藏系列不改变全局 KPI，价格编辑不被刷新覆盖 |

## 已执行结果

上列 12 个 Python 脚本共 179 项用例通过，无跳过、失败或错误，包括实际文件的 JSON 原子替换失败和 SQLite 重启恢复。JavaScript 两个源码语法检查、图表算法冒烟与页面 DOM 冒烟均通过，测试输出标记与退出码已核对。Git diff --check 通过。

模拟 HTTP 预览检查通过：252 次总调用中，按模型筛选为 92 次（其中 80 条明细）；失败筛选为 8 次；单次调用的自定义时间窗为 1 次；导出为 80 条匹配明细。非法时间和负价格返回 400，显式零价显示零费用。样例价表已恢复，预览服务已停止。这些数量来自开发夹具，不代表用户真实用量。

## 执行环境与边界

Python 测试使用 Python 3.14.7。受限 Windows 子进程与临时子目录权限会导致部分 IO 用例无法执行。需要获准的执行环境和可写的隔离测试目录，不能把跳过或权限错误计作通过。

测试在获准执行环境中直接启动 Python 3.14.7 和 Node 24.21.0，文件样本位于工作区隔离目录 .test-tmp。没有修改系统权限或安装依赖。测试结果依据命令退出码和测试摘要。

当前没有真实 AstrBot 实例和 BrowserSkill 的 bsk 程序，无法完成宿主浏览器截图验收。最小 DOM 桩不包含浏览器的 CSS 排版引擎，也不证明下载权限或可访问性树在宿主中一致。ruff 和 pytest 未安装；以上命令使用项目自带的标准库脚本。

## 上机验收

1. 备份插件数据目录中的 usage.json 与 events.db，保持两份数据来自同一次备份。先用测试实例或备份副本验证升级。
2. 默认配置启用插件但关闭 detail_history。确认旧日汇总可见，小时性能和逐调用明细明确显示覆盖不足。
3. 开启 detail_history 并重载，完成成功、失败、流式取消各一笔调用。确认各计一次，取消不计入成功率分母；记录运行期间的未上报字段。
4. 对比聊天命令与未加条件的同日总览。组合插件、模型、provider 和结果筛选，确认总览、分析、明细和导出范围一致。
5. 测试今天与跨日自定义区间，统计日界为 UTC+8；自定义区间结束时刻不包含在内。结果筛选和子日过滤应说明被排除的旧汇总。
6. 在 360px、768px 和宽屏容器分别检查明暗主题、标签导航、图例、长模型名、表格横滚和键盘操作，确认文字没有随整页缩小。
7. 编辑价格时触发刷新，确认草稿仍在。分别保存缺价和零价，确认费用与覆盖信息不同；价格单位为 USD / 百万 Token。
8. 导出 CSV，验证中文、换行、引号和公式前缀。确认实际 iframe 允许下载，文件不含消息、会话标识、错误正文或密钥；文本日志导出另有会话标识提示。
9. 对测试数据执行 /usage_clear，确认仅移除未保存调用及对应明细；执行两步重置，确认日汇总与明细清空而价格、文本日志保留。测试写盘失败时必须显示部分失败。
10. 在测试副本上模拟明细已写入、JSON 尚未提交后退出进程。重启后确认恢复调用只出现一次，并验证再次保存和重启不会累加。

## 数据与故障注意事项

非空 v1 实验明细库缺少共同提交证明，无法判断哪些调用已计入 JSON。插件保留原文件并只显示日汇总，不自动合并；迁移前需要备份并核对两份数据。空 v1 明细库可以保留价表并升级。

日汇总通过 event_checkpoint 引用 SQLite 中的提交证明。仅还原其中一个文件可能导致证明缺失或旧历史不完整。遇到证明不匹配，保留原文件并恢复同批次备份；不要手动删除证明表来强行去重。

保存失败时，内存调用和恢复所需明细可以暂时超过目标上限。此时需要处理数据目录权限、磁盘空间或数据库错误。未提交到任一文件的内存调用无法保证在进程异常退出后恢复。
