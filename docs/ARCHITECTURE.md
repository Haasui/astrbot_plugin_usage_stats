# ARCHITECTURE · astrbot_plugin_usage_stats

分层、跨层契约、决策表、官方扩展点对照。

## 分层

```
attribute.py  归因层   从调用栈模块名挑插件 / 从局部变量捞会话   纯逻辑，不 import astrbot.*
stats.py      统计层   用量累加、模型聚合、天 × 模型折叠          纯逻辑，不 import astrbot.*
event_store.py 明细层  SQLite 逐调用记录、价格、查询与提交证明       纯标准库，不依赖 AstrBot
store.py      落盘层   计数序列化、合并、滚动清理                 纯标准库，只 import 同层 stats
patch.py      越界层   类级补丁与精确还原                       不 import astrbot.*（目标类由装配层传入）
main.py       装配层   装补丁 / 记账 / 重入守卫 / 指令           依赖 AstrBot
```

纯逻辑层不 import 任何 `astrbot.*`，因此能脱离本体跑 `tests/test_core.py`。这是分层换来的唯一实际收益，别破坏它。

## 跨层契约

| 契约 | 形态 | 由谁满足 |
| :--- | :--- | :--- |
| 归因输入 | `Iterable[str]`，模块名由内向外 | `main._scan_call_stack()` |
| 会话输入 | `Iterable[object]`，各层局部变量由内向外 | 同上 |
| 补丁目标 | `[(类, 方法名)]` | `main` 传 `Provider`，`patch.collect_targets()` 展开 |
| 包装器注入 | `make_wrapper(record)` 返回工厂 | 记账函数由 `main` 提供，`patch` 不知道账记到哪 |
| 记账失败 | 一律吞掉，只加计数 | `main._record` 的外层 try/except |

## 决策表

| 决定 | 取值 | 依据 |
| :--- | :--- | :--- |
| 补丁打在类还是实例 | **类** | `provider/manager.py:812` 每次 `load_provider` 都造新实例，实例级补丁在重载后失效。已核实 `dynamic_import_provider`（`:414`）用的是普通 `from ... import`，全文件没有 `importlib.reload` / `import_module` —— 类对象稳定，不会被打补丁之后换掉 |
| 为什么还要**定时重扫** | 插件初始化时 provider 子类可能**尚未被导入**，`Provider.__subclasses__()` 是空的，一处都挂不上 | 挂不上就收不到调用，也就**没有任何事件能触发重扫**。所以重扫必须**时间驱动**（`_refresh_loop`，默认 60 秒），不能挂在记账路径上 |
| 重扫怎么取基类 | **每次 `importlib.import_module` 重新取** | 不留 import 时的引用。目前类对象稳定，这一步是给未来上保险 |
| 用量从哪读 | **`LLMResponse.usage`** | 它就是 AstrBot 内部 `openai_source._extract_usage()` 的产物（`:687`、`:951`）。读返回对象是公开稳定的面；去挂钩那个**私有**方法会多一处会断的地方 |
| 缓存「上报了没有」怎么判 | **命中数 > 0 就算上报**，`raw_completion` 只当补充证据 | 流式响应的 `raw_completion` 常常取不到，只认它会把每次调用都记成「没上报」，于是命中率整列「—」，而旁边「缓存」列明明有数字。命中数不会凭空出现，它本身才是硬证据 |
| 打基类还是子类 | **递归 `__subclasses__()`，逐个打自己 `__dict__` 里定义的方法** | `provider.py:100` 的基类方法体是 `...`，子类覆写会遮蔽它 |
| 同步方法还是异步生成器 | **按 `inspect.isasyncgenfunction` 分流** | `text_chat_stream` 返回 `AsyncGenerator`（`provider.py:139`），`await` 它会 TypeError |
| 归因用配置还是调用栈 | **调用栈** | 靠配置区分要求用户给每个插件单配 provider，不通用 |
| `super()` 链 | **包装器 ContextVar 绑定消费任务；装配层另保留记账重入守卫** | 嵌套调用只记一次，独立任务和跨任务消费流不互相误抑制 |
| 会话聚合与否 | **只收集，不聚合** | 采集几乎免费（栈本来就要走），聚合与展示会撑长 `/usage` |
| 落盘按天还是按小时 | **按天** | 账单按天算，而且键少一个数量级 |
| 落盘带不带模型维度 | **带**（插件 → 天 → 模型） | 「谁在用贵模型」需要它；不带的话重启后 `/usage` 只剩插件总量 |
| 保存口径 | **只读基线 + 本次内存全量，每次重算** | 幂等：重复保存不会翻倍（`tests/test_store.py` 有专门用例） |

## 官方扩展点对照

| 用到的 | 位置 | 用途 |
| :--- | :--- | :--- |
| `@register` / `Star` | `main.py` | 插件注册 |
| `@filter.command` | `main.py` | `/usage`、`/usage_clear` |
| `context.register_web_api` | `main._register_view()` | WebUI 视图的数据接口 `/{插件名}/stats` |
| `window.AstrBotPluginPage` bridge | `views/usage/index.html` | 前端取数（SDK 来自 `/api/plugin/page/bridge-sdk.js`） |
| `logger` | `main.py` | 启动/卸载摘要与 debug 明细 |

## 越界点

本插件是全项目唯一包装 provider 的。官方钩子所见范围不含插件发起的模型调用——`context.llm_generate` 直接调 `provider.text_chat`（`astrbot/core/star/context.py:208`），不经过 filter 流水线。

代价由三条纪律偿还：**只读不改**（不修改请求与响应、不参与决策）、**默认关闭**（`enabled` 为 false 时不装任何补丁）、**卸载严格还原**（按「类 + 方法名」写回原函数，绝不用 `delattr`）。

与同样触碰 provider 的插件共存时需实测，见 [`VERIFY.md`](VERIFY.md) D6。

## 日汇总与明细的共同提交

[日汇总存储](<../store.py>)保存按插件、日期和模型聚合的 JSON，[明细存储](<../event_store.py>)保存可选调用记录与手工价格。[装配层](<../main.py>)负责按同一快照发布两者的包含关系。

1. 采集调用进入内存日汇总；开启 detail_history 时，稳定 event_id 同时进入明细队列。
2. 明细按 event_id 幂等写入 SQLite。写入成功的部分从队列移除，未写入的部分继续等待重试。
3. SQLite 准备提交证明，记录当前明细及待写队列的 ID。待写 ID 也需要记录，因为 JSON 可能先于该明细的重试成功。
4. 日汇总通过同目录临时文件原子替换，连同 event_checkpoint 一起发布。失败时保留原日文件、原证明与内存调用。
5. JSON 成功后更新内存基线，释放旧证明并裁剪已归入日汇总的明细。提交前不裁剪唯一的恢复日志。

重启时读取 JSON 引用的证明，把已写入 SQLite、却不在该证明中的调用恢复为待保存记录，保持原 event_id。它们不增加本次启动的新调用计数。查询只从日桶中扣除能够证明已包含的明细，剩余数据保留日粒度。

[真实文件恢复测试](<../tests/test_recovery_disk.py>)覆盖 SQLite 提交后 JSON 原子替换失败及再次重启；[装配测试](<../tests/test_integration.py>)还覆盖日汇总先提交、明细延迟重试。备份必须同时保留两个文件。

## 查询与界面边界

新统计页通过 stats 的 view=dashboard 或 view=export 分支取数，统一使用插件、原始模型名、provider、结果和时间条件。查询采用含时区起止时间及半开区间，日界固定为 UTC+8。页面的图例显隐只是显示状态，不改变全局统计条件。

旧日汇总不伪造逐调用时间。结果或子日时间筛选无法精确匹配的旧汇总会被排除并报告数量。性能仅使用有效明细样本，未知字段保留缺失态。新统计与导出、价格保存及重置均由登录用户调用；文本日志仍保留原有独立开关和有界尾读。

明细存储只保存统计白名单字段，不包含会话标识、消息正文、密钥或错误正文。默认关闭的文本日志是另一条数据路径，可能含会话标识。重置统计保留手工价格与文本日志。
