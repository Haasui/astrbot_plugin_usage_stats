# EasyCLIProxyAPI 用量统计复用与迁移清单

> 第 2-4 节记录 0.2.8 改造前的核查基线，第 5 节为迁移提案。0.3.0 的现行行为见 [使用说明](<../README.md>) 与 [验证清单](<VERIFY-0.3.0.md>)。

**面向 AstrBot 插件维护者的源码核查与实施提案。**

| 项目 | 内容 |
| --- | --- |
| 状态 | 源码核查结论；迁移方案待维护者确认 |
| 负责人 | 插件维护者 |
| 范围 | 使用记录的总览、筛选、分析、请求明细与价格统计 |
| 保持不变 | 本轮不修改插件运行代码、配置默认值或历史数据 |
| 关联 | [架构说明](<ARCHITECTURE.md>)、[调用日志设计](<FEATURE-UX-DESIGN.md>)、[验证清单](<VERIFY.md>) |

## 1. 结论与证据范围

建议先复用总览布局、SVG 图表算法和筛选交互，再扩展逐调用存储与性能、费用统计。当前按天保存的汇总可以支撑日趋势和三类 Token 构成，无法还原过去的小时分布、逐调用耗时或独立思考 Token。

上游证据锁定 [EasyCLIProxyAPI 提交](<https://github.com/router-for-me/EasyCLIProxyAPI/commit/97a7d9b76b2462a15076ebaab1719b996885af5d>)。核查日期为 2026-11-11。以下上游链接均固定到该提交；本地依据是当前工作区源码，包含已有未提交改动。

“源码事实”表示已读取实现；“实测”表示运行了列出的本地检查；“建议”表示拟实施行为。没有运行上游 Rust 测试、桌面应用或 AstrBot 真机。截图用于确认目标区块，不构成像素级复刻或供应商账单准确性的证据。

### 1.1 保留的插件约束

现有集成经 [AstrBot 页面 bridge](<../views/usage/app.js#L1-L10>) 取数，模型调用采集由 [provider 包装和定时重扫](<../main.py#L305-L341>) 完成。迁移保留插件归因、重入保护、记账异常隔离和卸载还原。页面刷新调度与 provider 定时重扫是两条独立路径。

界面确认操作继续使用受限 iframe 可用的页面交互。[视图测试](<../tests/test_views.py#L1-L26>) 禁止阻塞式 confirm、alert 和 prompt。费用、日志和额外字段的启用方式需要单独确认，不随视觉重做改变默认值。

## 2. 源码定位与复用清单

下表的“移植”指保留算法或交互，再连接插件的数据与 DOM；不表示相关功能已经存在。

| 功能 | 固定源码 | 复用程度 | AstrBot 适配 |
| --- | --- | --- | --- |
| 六项指标与双栏总览 | [UsageStatGrid / OverviewView](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src/pages/UsageRecordsPage.tsx#L943-L1047>) | 布局与层级可借鉴 | 指标仅展示可证实的数据，保持独立缺失态 |
| 全局筛选与候选项 | [buildQueries / executeLoadData](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src/pages/UsageRecordsPage.tsx#L282-L540>) | 复用状态组织方式 | 来源映射为插件，独立保留 provider 与 model 标识；不引入 API 密钥采集 |
| 时间分桶、补零、Top6 与其他 | [buildUsageTrendSeries](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src/services/usageTrend.ts#L425-L560>) | 纯函数可移植 | 日汇总先按天处理；细粒度分桶依赖新增数据 |
| 时间粒度与刻度 | [chooseTrendBucket](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src/services/usageTrend.ts#L183-L192>)、[刻度与定位函数](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src/services/usageTrend.ts#L228-L342>) | 算法可移植 | 明确统计时区与起止边界，避免把本地时间键当 UTC |
| 堆叠柱状图 | [UsageTrend](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src/pages/UsageRecordsPage.tsx#L1086-L1426>) | SVG 几何、图例、tooltip、键盘交互可移植 | 改为现有网页 DOM；图例显隐只影响图表，不改变全局查询 |
| Token 圆环与构成条 | [TokenComposition](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src/pages/UsageRecordsPage.tsx#L1431-L1525>)、[calculateTokenComposition](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src/services/usageMetrics.ts#L26-L52>) | SVG 算法可移植 | 当前使用未缓存输入、缓存读取、输出三项；缺失的写入字段不补成已知零 |
| 模型和来源分析 | [UsageAnalysisView](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src/pages/UsageAnalysisView.tsx#L29-L207>) | 比较、占比和展开交互可借鉴 | 保留插件维度；上游来源后处理依赖凭证登记表，不能直接复制 |
| 请求明细 | [EventsView](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src/pages/UsageEventsView.tsx#L466-L671>) | 分页、列显隐、列宽与持久化可借鉴 | 需要结构化逐调用数据；上游普通表格不自带表头排序、虚拟滚动或详情抽屉 |
| 刷新与防旧响应覆盖 | [加载与调度入口](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src/pages/UsageRecordsPage.tsx#L282-L439>)、[refreshScheduler](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src/services/refreshScheduler.ts#L1-L83>) | 最新请求胜出与后台合并可借鉴 | 对每个视图维护真实 loading；隐藏可见性需要宿主信号或页面能力验证 |
| CSV 导出 | [CSV 转义](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src/pages/UsageEventsView.tsx#L165-L179>)、[筛选全量导出](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src/pages/UsageEventsView.tsx#L681-L706>) | 保留编码、转义和公式注入防护 | Tauri 保存对话框改为浏览器下载或后端导出；明确全量与当前页区别 |
| 数据接口 | [usage 服务](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src/services/usage.ts#L186-L225>) | 类型和职责可参考 | Tauri invoke 全部替换为 AstrBot Web API，不移植桌面运行时 |

### 2.1 样式与布局边界

总览使用 [整体缩放](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src/pages/UsageRecordsPage.tsx#L1050-L1083>)：scale=min(1,width/900,height/420)。该比例会连同文字和点击目标一起缩小。窄屏纵排由 [viewport 媒体查询](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src/styles/responsive-shell-usage.css#L198-L235>) 控制；宽 iframe 内的窄栏不能仅靠这一条规则适配。

样式入口包含 [多文件级联](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src/styles/index.css#L1-L25>)。筛选和表格还受后续样式影响，仅复制基础样式无法重现最终外观。迁移建议使用插件专属作用域和容器查询，图表适配宽度，卡片自然换行；保留可读字号与图表最小高度。

## 3. 指标与筛选的真实口径

以下是上游公式。插件侧的拟议行为见第 5 节，不能直接把两个系统同名指标当作同一批请求。

### 3.1 计数、性能与缓存

令 Q 为筛选后的事件行，N 为其行数。TTFT 指首 Token 延迟。TPS 的有效样本还需满足：成功、未取消、generate 非零、输出大于零，以及合法耗时。

| 指标 | 固定版本行为 | 迁移时需明确的区别 |
| --- | --- | --- |
| 请求总数 | COUNT(*) | 事件行可能共享 request_id，不能无条件解释为唯一 HTTP 请求数 |
| Token 总量 | Σ数据库 total_tokens | 不在概览查询中从输入、输出和思考重建 |
| 成功率 | S/(S+F)×100%；S 为 failed=0，F 为 failed!=0 且 canceled=0；分母为零时返回零 | 正常采集取消被排除；异常导入组合 failed=0,canceled=1 仍计入 S |
| TPS | 1000×Σ有效样本输出/Σ有效样本耗时毫秒 | 是时长加权比值，不是单条 TPS 算术平均，也不是并发墙钟总吞吐 |
| TPS 的 TTFT 模式 | 从总耗时扣除首 Token 时间，剩余窗口至少 100ms | 总耗时模式只要求正耗时；单条前端函数没有后台的状态过滤参数 |
| RPM / TPM | 请求数或 Token 数除以窗口分钟数，窗口最少 1 分钟 | 缺起止时间时使用匹配事件首末时间；不是最近一分钟滚动值 |
| 平均延迟 | Q 中所有 latency_ms 之和/N | 包含失败、取消和零耗时行，不只包含 TPS 有效样本 |
| 缓存命中率 | min(Σ缓存读取/Σ输入,1)，输入为零时返回零；接口返回 0-1 | 分母包含缓存输入，展示时乘 100 |
| 圆环缓存读取占比 | 缓存读取/构成总量×100% | 分母包含输出，所以与顶部缓存命中率不同 |

依据：[概览 SQL](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src-tauri/src/usage.rs#L3014-L3046>)、[聚合公式](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src-tauri/src/usage.rs#L3173-L3193>)、[窗口分钟数](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src-tauri/src/usage.rs#L4957-L4970>)、[前端单条速度与缓存率](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src/services/usageMetrics.ts#L54-L110>)。

### 3.2 Token 规范化与费用

上游分别处理输入、输出、思考、缓存读取和缓存写入。不同协议的包含关系由 [Token 规范化](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src-tauri/src/usage/token.rs#L52-L254>) 决定；Claude 输入可能需要加回缓存，部分协议的思考 Token 需要折入输出。旧数据和异常值有兼容分支，不能假定每条记录都严格满足同一恒等式。

构成图先计算 max(0,输入-缓存读取-缓存写入)，再与两类缓存和输出组成圆环。思考作为单独注记，不作为第五块重复相加。该构成函数的分母由四块重建，不直接采用数据库 total_tokens。

[基础费用公式](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src-tauri/src/usage.rs#L3367-L3383>)按未缓存输入、缓存读取、缓存写入和输出分别计价，再除以每百万 Token 单位。上游另有 [长上下文及服务档位倍率](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src-tauri/src/usage.rs#L3294-L3336>)。当前插件缺这些计价元数据，无法原样承诺相同估价。

[pricedRequests](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src-tauri/src/usage.rs#L3270-L3290>)只要求匹配到 price 对象，未逐项检查实际用到的缓存价是否已配置。缺项数值零仍可能算“已计价”。[明细读取](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src-tauri/src/usage.rs#L4874-L4889>)按当前价表重算费用，价格修改会改变历史展示；这不是账单快照。

### 3.3 查询、时间与分页

上游页面通过 Tauri 调用 Rust 查询本地 SQLite 逐事件表，表结构见 [usage_events](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src-tauri/src/usage.rs#L1247-L1381>)。[迁移逻辑](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src-tauri/src/usage.rs#L909-L969>)移除了 event_key 唯一约束，不能依靠该键保证外部请求去重。

上游多个查询共用 [build_usage_filter](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src-tauri/src/usage.rs#L2923-L2997>)。时间支持有效 RFC3339，start/end 为双闭区间；非法时间被忽略。模型等文本字段采用精确匹配，显示模型优先 alias；来源下拉实际传 upstream。失败筛选排除取消，取消有独立状态。

[后端趋势](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src-tauri/src/usage.rs#L3078-L3155>)先按本地半小时和显示模型聚合，前端再补空桶、重分桶。该本地时间键没有时区偏移，系统时区改变或夏令时回拨存在分桶边界风险。相邻闭区间查询也需防止端点重复。

[明细分页](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src-tauri/src/usage.rs#L4825-L4898>)按 timestamp_ms DESC,id DESC 排序，page_size 夹在 1-5000，越界页返回最后一页。导出循环累计全部匹配行，持续写入时没有筛选快照保证，也没有有界流式内存策略。

来源分析还有 [登记表后处理](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/src-tauri/src/usage/sources.rs#L293-L389>)：丢弃不能匹配的上游身份，再按标签合并。由此得到的 upstreams 合计可能小于其他维度。迁移应按插件稳定标识聚合，显示名只负责展示。

## 4. 本地能力与迁移前置问题

### 4.1 数据能力

| 能力 | 当前工作区事实 | 所需扩展 |
| --- | --- | --- |
| Token 与日汇总 | [Usage](<../stats.py#L21-L50>)保存未缓存输入、缓存输入、输出；[存储](<../store.py#L1-L24>)按插件×天×模型 | 可先做三类构成和日趋势；旧日汇总只保留原粒度 |
| 全局插件与模型筛选 | [API](<../main.py#L483-L494>)的 plugin/model 仅生成下钻序列 | 统一查询条件，让卡片、图表、分析和明细共享范围 |
| 成功与失败 | [Bucket](<../stats.py#L94-L108>)保存已记录 Call 的 calls/failed；[结果判定](<../main.py#L713-L718>)依赖响应对象 | 先补齐异常覆盖，再明确取消与失败口径 |
| 单次耗时 | [包装器](<../patch.py#L111-L153>)测量整个调用或流消费；[Shape](<../stats.py#L53-L69>)含 ms | 持久化耗时、样本数及可选 TTFT，不能把日志尾部均值称为全范围平均 |
| 明细 | [日志 API](<../main.py#L456-L474>)提供可选、有界文本尾部 | 建立结构化逐调用存储、分页和统一过滤；日志关闭或截断须明确显示 |
| 思考与缓存写入 | [usage_from](<../stats.py#L436-L453>)只映射三类 Token | 核实 AstrBot 各 provider 的原始字段及包含关系，再增加独立字段 |
| 估价 | 当前 Usage、Bucket 与 API 没有价表或金额字段 | 配置单位、币种、价格来源和缺项状态；覆盖信息独立于费用数值 |

### 4.2 已确认的口径问题

以下问题适合作为功能迁移的前置回归项，本轮未修复。

- 缓存“已上报零命中”标记会在序列化中丢失。[Bucket.to_dict](<../stats.py#L110-L119>)没有输出 cache_reported，而 [snapshot](<../store.py#L61-L68>)通过它转换。实测 1 次明确上报零命中的调用，往返后标记由 1 变 0，显示从 0% 变为缺失态。每次 [视图合并](<../main.py#L561-L570>)都经过该转换，影响不只限于重启后的历史。
- [merge](<../store.py#L91-L100>)把 cache_reported 加入序列化 total。实测两条各 15 Token、cache_reported=1 的单元合并，存储 total 为 32；[as_buckets](<../store.py#L148-L157>)从三类 Token 重建后总量为 30。因此这是存储派生字段不一致，不能据此断言页面总量已增加。
- [非流式包装](<../patch.py#L157-L181>)在正常返回后记账，直接抛异常不会记录。这是静态源码结论。成功率目前只能描述已记录调用；新增成功率卡之前需要覆盖协程、同步和返回 awaitable 的异常路径，并保留异常向上抛出的行为。
- [上一周期下界](<../main.py#L496-L501>)取 now-2×days，而 [当前自然日下界](<../stats.py#L208-L218>)取 today-(days-1)。静态推导前周期含 N+1 个自然日；环比迁移需加入固定时间边界用例。

内存复现使用 Python 导入现有统计层，构造 Usage(10,0,5,1)，执行 snapshot→as_buckets；合并复现向 merge 传两份相同计数单元。检查未修改业务数据或插件源码。

## 5. 建议的迁移顺序与验收

### 5.1 总览与基础交互

> **Why & What：保留插件网页结构**
>
> **What：**在现有网页中移植 SVG 数据算法和交互，保留 AstrBot bridge。
>
> **Why：**上游图表没有大型图表库依赖，当前插件也已有页面与测试。该选择不能补出缺失的历史字段。
>
> **Alternatives considered：**整体迁入 React 可直接复用组件结构，但同时增加构建和运行时依赖，Tauri 接口仍需替换。
>
> **Fallback：**维持现有统计页，只替换构成图和趋势图；不改变命令输出。

目标是把筛选、指标、趋势和构成组织成截图中的层级。涉及 [页面结构](<../views/usage/index.html>)、[交互逻辑](<../views/usage/app.js>)、[样式](<../views/usage/style.css>)和 [视图冒烟测试](<../tests/view_smoke.mjs>)。拟新增的纯算法模块可命名为 usage_metrics.js 与 usage_trend.js，置于现有页面目录。

验收场景：空数据、已知零值、缺失缓存字段、全部缓存输入、模型超过六个、日期缺口、图例显隐、左右键与 Home/End/Escape、亮暗主题。隐藏模型后只改变图表及 tooltip，不改变顶部全局指标。页面以 360px、768px 和宽屏容器验证重排，避免整体缩小文字。

验证命令沿用 node tests/view_smoke.mjs，并新增纯算法测试。真机验收需要 AstrBot 页面 bridge、窄屏和主题截图；DOM 桩测试不替代这些检查。

### 5.2 查询与结构化历史

> **Why & What：细粒度历史单独存储**
>
> **What：**为新增调用建立可查询的结构化事件存储，兼容读取现有日汇总；建议使用 Python 标准库 SQLite。
>
> **Why：**自定义时间、结果筛选、逐调用性能和请求明细需要日汇总之外的信息。事件存储增加写入、索引、裁剪和迁移成本。
>
> **Alternatives considered：**扩展小时桶可以支持小时趋势且数据较少，但仍不足以提供逐调用明细和任意时间过滤。
>
> **Fallback：**保留日汇总统计，页面明确历史粒度与覆盖范围；新增存储不可用时不阻断模型调用。

实施目标涉及 [记账与 API](<../main.py>)、[调用模型](<../stats.py>)、[现有存储](<../store.py>)和拟新增的 event_store.py。先修复第 4.2 节的可回归问题，再定义新记录的来源插件、provider、model、完成时间、结果、用量可用性、耗时和记录 ID。旧聚合与新事件不得在重叠期间重复相加，也不得用虚构事件展开旧日汇总。

建议查询在服务端校验 start/end，采用含时区时间戳与半开区间 [start,end)，非法范围返回明确错误。统一 filter 对象供总览、分析、明细和导出使用；候选项只受时间范围影响。请求结果采用成功、失败、取消和未知状态，并记录每个指标的有效样本数。

验收场景：日界与相邻区间不重数、插件与模型独立组合筛选、异常和取消只计一次、重启和重复保存不翻倍、旧汇总与新事件交界不重复、写盘失败仍能返回原模型结果。测试放在 [统计用例](<../tests/test_core.py>)、[补丁用例](<../tests/test_patch.py>)、[存储用例](<../tests/test_store.py>)及拟新增的事件存储测试中。运行对应 Python 测试脚本；新数据库用临时样本，不使用真实历史。

### 5.3 性能、价格与导出

新增性能聚合时，第 3.1 节的分子、分母和有效样本规则需成为测试断言。缺 TTFT 的旧记录只支持总耗时模式；无有效样本显示缺失态。只有请求量与时间范围已知时才计算窗口 RPM，并在说明中写出范围。

估价建议先实现可配置的每百万 Token 单价。显示名与模型匹配键分离，价格缺项和显式免费分别保存。维护者需决定是否引入服务档位、长上下文倍率及历史价表版本；未支持这些规则时明确标注估算范围。

导出保留公式注入防护，使用有界后端导出或固定上限的浏览器 Blob。持续写入下的导出采用稳定截止点和游标；页面需提示范围、截断和敏感字段。验收涵盖 CSV 引号/换行、中文、以公式符号开头的字段、筛选一致性、取消导出与超限反馈。

涉及 [API](<../main.py>)、[页面逻辑](<../views/usage/app.js>)、拟新增价表模块和事件存储测试。费用单元测试需区分缺价、零价、缓存单价缺失和价格修改后的历史行为；导出测试需验证不重复、不漏页与内存上限。具体保存入口仍需在 AstrBot iframe 中运行验证。

## 6. 许可与来源保留

固定版本的 [上游许可证](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/LICENSE#L1-L21>)是 MIT，版权声明为 Copyright (c) 2026 Router-For.ME。该许可证允许复制、修改和分发源码，要求复制件或实质性部分包含原版权声明及许可声明。

当前插件采用 [AGPL-3.0](<../LICENSE>)。引入 MIT 源码时保留现有插件许可证，并随发行包保留 MIT 组件声明；不要把上游代码的版权声明改成插件作者。具体复用文件、来源提交、修改说明与完整许可文本建议记录在新增的 THIRD_PARTY_NOTICES.md，打包检查应确认它被包含。

逐文件直接移植时记录来源路径和固定提交，保留原有版权头。仅借鉴功能、独立实现的部分区分记录，避免把整个插件标成上游源码。图标、字体、图片及其他依赖分别检查授权；顶层 MIT 不能替代第三方素材许可。本轮只增加核查文档，没有复制上游实现或素材到运行目录。

## 7. 验证记录与待确认项

### 7.1 已执行的本地检查

以下结果来自当前工作区，非上游测试，也非 AstrBot 真机验证。Python 为 3.14.7，Node 为 24.21.0。

| 命令 | 结果 | 覆盖限制 |
| --- | --- | --- |
| python tests/test_core.py | 25 组通过 | 纯逻辑与格式化 |
| python tests/test_store.py | 18 组通过，5 组 IO 跳过 | 临时目录不可写，不能声称落盘验证通过 |
| python tests/test_patch.py | 9 组通过 | 未覆盖第 4.2 节全部异常路径；有 asyncio 弃用警告 |
| python tests/test_invariants.py | 16 组通过 | 源码结构约束 |
| python tests/test_config.py | 9 组通过 | 配置一致性 |
| python tests/test_contract.py | 7 组通过 | 本地声明和打包约束 |
| python tests/test_log_tail.py | 9 组运行，1 组错误 | calls.log 写入临时目录时 PermissionError，退出码 1 |
| python tests/test_views.py | 2 组通过 | 源码约束 |
| node tests/view_smoke.mjs | 通过 | 最小 DOM 桩，不是实际布局或宿主权限验证 |

日志错误发生在测试创建的系统临时目录，位于工作区外。未修改目录权限，也未切换执行环境重试该命令。剩余页面检查已单独执行。完整 IO 验证需要可写且获授权的测试目录。

### 7.2 维护者确认

- 插件维护者确认首批功能范围：总览与日趋势，或同时建设结构化历史；这决定是否新增数据库和数据保留配置。
- 插件维护者确认成本口径：币种、倍率、价表来源，以及历史按当前价重算还是保存当时价格版本。
- 插件维护者确认明细隐私与默认值：会话标识、provider 标识、错误信息、逐调用保留天数及导出权限。
- 实施者完成旧历史兼容设计后，维护者审核跨存储边界的去重与降级规则；有样本和测试后再启用新统计。

源码事实与本地复现覆盖第 2-4 节，实施选择集中在第 5 节。第 7.2 节的确认项由插件维护者决定。
