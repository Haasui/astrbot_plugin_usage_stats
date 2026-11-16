# 用量统计

按发起调用的插件、日期和模型统计 token 用量、调用次数、失败数与缓存 token。可用聊天指令或 AstrBot 插件页查看汇总和趋势。

- AstrBot：**4.11.0 或更高版本**
- 仓库：[astrbot_plugin_usage_stats](https://github.com/Haasui/astrbot_plugin_usage_stats)
- 数据保存在插件数据目录；默认保留 60 天
- WebUI 需要宿主提供 AstrBotPluginPage bridge；不支持插件页面的宿主仍可使用聊天指令

## 安装与启用

在 AstrBot WebUI 的 **插件管理** 中安装并加载插件，打开 **用量统计 → 数据管理**，启用用量采集并保存。发送一次模型请求后用 /usage 查看统计。安装或升级后先按宿主提示重载插件，日常修改本页采集设置可直接保存应用。

插件默认关闭。启用后会在 provider 方法上安装类级包装，以统计插件直接发起的模型调用。它不修改模型请求或响应；记账异常不会中断对话。与其他同样包装 provider 的插件同时使用时，建议在实际环境中验证共存情况。

## 查看统计

| 指令 | 说明 |
| --- | --- |
| /usage（别名 用量、usg） | 按模型汇总调用数、token 和缓存用量 |
| /usage full | 展开插件与模型维度，查看失败数和图片数 |
| /usage day | 查看今天的数据 |
| /usage week | 查看最近 7 天的数据 |
| /usage status | 查看运行状态、补丁数和保存情况 |
| /usage help | 查看指令帮助 |
| /usage_clear | 清空未保存的内存调用及对应明细，不删除已保存历史 |
| /usage_reset | 清空内存和已保存历史 |

/usage 合并已保存历史和当前运行数据；/usage status 的记录数只统计当前运行期间，两者口径不同。

## WebUI

在插件详情页打开 **用量统计** 视图：

- **总览**：调用数、TPS、Token、成功率、缓存命中率和估算费用；支持按模型或插件堆叠趋势、Token 构成和样本覆盖说明。
- **统一筛选**：今天、近 7 天、近 30 天、全部或自定义起止时间，可叠加插件、模型、provider 和成功/失败/取消条件。筛选应用于总览、分析、明细和导出。
- **分析**：按插件、模型或 provider 查看排行，支持排序和点击名称筛选。
- **请求明细**：分页、列显隐和列宽设置；CSV 导出最多 5000 条匹配明细，不含旧日汇总。
- **价格统计**：按真实使用的模型展示调用数、Token、估算成本与各项单价；支持模型搜索和分页。点击「更新价格」同步 Models.dev 公共参考目录，也可编辑单条手工覆盖。未配价格的已用模型仍会显示。
- **数据管理**：直接修改并保存采集总开关、调用明细、保留天数和明细上限。设置写回 AstrBot 配置，保存后应用；内部运行信息默认折叠，重置需要两次确认。
- 开启调用日志后，可在页面查看最近最多 5000 条记录（单次最多扫描 8 MiB）；超过上限时会提示截断。日志默认关闭，含会话标识但不含消息正文，导出文件请妥善保管。界面适配窄屏、亮色和暗色主题，也支持键盘操作。

## 归因与数据

插件从模型调用栈中识别发起调用的插件；未识别到插件时归为主对话。统计数据按插件、日期和模型聚合。token 数以 provider 返回的 usage 信息为依据；provider 未提供用量时，相应字段可能缺失或显示为未知，不能据此当作供应商账单金额。

默认保存汇总统计，不保存消息正文。启用 call_log 后，独立日志会记录请求构成长度、用量、耗时和会话标识，不记录正文；启用 debug 后，AstrBot 主日志也会包含会话标识。请依据隐私要求决定是否启用详细日志，并限制插件数据目录和主日志的访问权限。

可在数据管理页开启结构化明细（detail_history）。关闭时仍可查看日汇总；旧历史无法恢复为逐请求记录，按结果或子日时间过滤时会排除无法精确匹配的旧汇总，并显示排除数量。统计日界固定为 UTC+8，自定义区间使用含时区时间，结束时刻不包含在内。

升级保留原日汇总。若遇到缺少提交证明的旧实验版明细库，插件会保留原文件并降级到日汇总，不自动猜测重叠关系；请备份后核对迁移。SQLite 明细通过提交证明与日汇总去重；明细已写入而日汇总未提交的调用会在重启时恢复。备份或迁移数据目录时，应一起保存 usage.json 与 events.db，不要只还原其中一份。仍在内存队列、尚未提交到任一文件的调用在进程异常退出时可能丢失。

## 费用与统计口径

价格使用 USD / 百万 Token。手工 provider/模型精确覆盖优先，其次是手工通用价格，再使用 Models.dev 中精确模型 ID 对应的公共参考价；不做模型名模糊匹配。必需单价缺失的记录标记为未计价。当前估价不包含汇率、税费、服务档位或长上下文倍率，不能替代供应商账单。

Token 总量沿用 AstrBot 归一化的未缓存输入、缓存输入和输出三项。思考与缓存写入作为原始参考项单列，不再次加进总量。无字段显示缺失态，明确上报零值才显示 0。缓存命中率和圆环构成占比使用不同分母。

公共价格在「价格统计」页由用户点击更新，服务器直接请求 https://models.dev/api.json，不上传本地模型名、用量或凭证。更新失败保留上次目录，手工覆盖不会被同步改写。目录中没有对应 ID 的模型仍可手工设价；日汇总也可估价，不要求开启 detail_history。

成功率排除已识别的取消。TPS 使用成功且有有效耗时的明细样本，按总输出除以总耗时计算；平均延迟也只覆盖可用明细。页面会标明样本数和旧汇总覆盖范围。

## 配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| enabled | false | 启用 provider 统计包装；关闭时不安装补丁 |
| memory_cap | 20000 | 触发保存的内存记录阈值；保存失败时保留记录，可暂超阈值；0 表示不限 |
| keep_days | 60 | 汇总数据保留天数；0 表示不按天清理 |
| save_every | 50 | 每累计多少条记录触发保存；其他生命周期节点也会保存 |
| refresh_interval | 60 秒 | 重新扫描 provider 子类的间隔；0 关闭定时扫描 |
| call_log | false | 写入独立调用日志文件，不包含请求正文 |
| call_log_lines | 20000 | 调用日志行数上限；0 表示不裁剪 |
| detail_history | false | 保存结构化调用时间、结果、用量和耗时，用于细粒度趋势和请求明细；不保存会话标识 |
| detail_max_events | 20000 | 明细目标条数上限，限制在 1-200000；日汇总提交后裁剪，故障恢复期间可暂超目标 |
| debug | false | 在 AstrBot 主日志中输出调用明细，包含会话标识 |

本页设置保存失败时保留原运行配置；如果宿主配置已保存但运行状态未完整应用，页面会明确提示重载插件，不把它当成已生效。

修改归因规则不会重新分类已保存记录。需要重新统计时，使用 /usage_reset 清除日汇总与结构化明细；手工价格和可选文本日志保留。

## 排障

- **统计一直为零：**确认 enabled 已开启，检查启动日志中的补丁数量，并等待 refresh_interval 扫描 provider。
- **插件调用归到主对话：**部分调用栈可能无法识别插件模块，未识别的调用会归入主对话。
- **图表与指令数字不一致：**确认选择的时间范围；页面和指令的筛选范围不同会显示不同总量。
- **需要重置统计：**/usage_clear 只清本次运行的内存数据；/usage_reset 才会清除保存的历史。

## 开发与许可证

架构说明见 [架构文档](docs/ARCHITECTURE.md)。真机检查项见 [验证清单](docs/VERIFY.md)。调用日志尾读与状态设计见 [功能与 UI/UX 设计](docs/FEATURE-UX-DESIGN.md)。EasyCLIProxyAPI 统计模块的源码依据与适配范围见 [复用与迁移清单](<docs/EASYCLI-USAGE-MIGRATION.md>)。在插件目录执行：

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
python tests/test_pricing.py
python tests/test_web_settings.py
python -m unittest discover -s tests -p test_price_catalog.py
node tests/dashboard_smoke.mjs
node tests/view_smoke.mjs
~~~

开发预览使用模拟调用和内存 SQLite，不读取真实 AstrBot 历史：

~~~bash
python tools/preview_server.py --port 8766
~~~

在浏览器打开 http://127.0.0.1:8766。也可运行 node tools/preview.mjs。预览不替代 AstrBot 真机验证，验证边界见 [0.3.2 验证记录](<docs/VERIFY-0.3.2.md>)。

许可证：AGPL-3.0。设计参考与来源说明见 [第三方来源](<THIRD_PARTY_NOTICES.md>)。
