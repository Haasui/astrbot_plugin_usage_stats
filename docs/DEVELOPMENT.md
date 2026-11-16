# 开发与贡献

贡献代码或文档前，先确定改动面向的用户行为，并用对应的测试验证。问题反馈请走[项目 Issues](<https://github.com/Haasui/astrbot_plugin_usage_stats/issues>)，代码变更通过 Pull Request 提交。

## 本地准备

使用项目检出目录 astrbot_plugin_usage_stats，避免随意重命名；当前测试通过这个包名导入源码。源码模块职责见[架构说明](<ARCHITECTURE.md>)。

运行插件需要 AstrBot。统计、存储、解析和装配桩测试可脱离宿主执行，不需要为了跑它们启动真实机器人。前端测试使用 Node.js，建议使用当前 LTS；项目没有声明独立的最小 Node 版本。

[运行依赖](<../requirements.txt>)只列宿主要求；可选开发工具见[开发依赖](<../requirements-dev.txt>)，Ruff 配置见 [ruff.toml](<../ruff.toml>)，pytest 发现路径见 [pytest.ini](<../pytest.ini>)。

## 运行测试

完整本地验证应在项目根目录执行下面全部命令。现有测试包含自执行函数脚本与 unittest 类，单独运行 unittest discover 不会覆盖前者。

~~~bash
python -B tests/test_core.py
python -B tests/test_store.py
python -B tests/test_patch.py
python -B tests/test_invariants.py
python -B tests/test_config.py
python -B tests/test_contract.py
python -B tests/test_views.py
python -B -m unittest discover -s tests -p "test_*.py"
node --check views/usage/app.js
node --check views/usage/dashboard.js
node tests/dashboard_smoke.mjs
node tests/view_smoke.mjs
~~~

选择验证入口时可按下面的职责定位，不把测试文件存在当作已执行：

| 改动范围 | 主要检查 |
| --- | --- |
| Token、归因与文本输出 | [test_core.py](<../tests/test_core.py>) |
| provider 包装与取消 | [test_patch.py](<../tests/test_patch.py>) |
| 日桶合并与保存 | [test_store.py](<../tests/test_store.py>)、[取消聚合](<../tests/test_cancellation_counts.py>) |
| SQLite 查询、迁移与价格 | [test_event_store.py](<../tests/test_event_store.py>)、[test_pricing.py](<../tests/test_pricing.py>) |
| 公共目录解析与网络边界 | [test_price_catalog.py](<../tests/test_price_catalog.py>) |
| 宿主装配与设置 | [test_integration.py](<../tests/test_integration.py>)、[test_web_settings.py](<../tests/test_web_settings.py>) |
| 实际文件的部分提交恢复 | [test_recovery_disk.py](<../tests/test_recovery_disk.py>) |
| 调用日志 | [test_log_tail.py](<../tests/test_log_tail.py>) |
| 页面与图表 | [view_smoke.mjs](<../tests/view_smoke.mjs>)、[dashboard_smoke.mjs](<../tests/dashboard_smoke.mjs>)、[静态页面约束](<../tests/test_views.py>) |

文件 IO 用例需要可写的隔离测试目录。权限错误、跳过或缺少依赖不能记为测试通过，也不应通过修改真实数据目录来解决测试环境问题。

## 开发预览

~~~bash
python -B tools/preview_server.py --port 8766
~~~

打开 http://127.0.0.1:8766。也可用 node tools/preview.mjs 启动。预览使用模拟调用和内存 SQLite，不读取真实历史；公共价格更新会访问固定 Models.dev 地址。

预览没有 AstrBot 配置句柄，因此不会写真实宿主配置。Web 设置的持久化由宿主契约测试验证。页面冒烟使用最小 DOM，不能代替实际 CSS 排版、iframe 下载权限和真实 provider 集成验证。

若要用查询响应验证页面投影，可将纯模拟响应保存为 JSON，并设置 ASTRBOT_USAGE_FIXTURE 后运行页面冒烟。不要把真实用量或会话数据提交为公开夹具。

## 提交前检查

1. 说明要解决的具体问题、复现步骤和改动范围，避免顺带重写无关模块。
2. 为缺陷加入能暴露原问题的回归，再实现修复。涉及保存、重试、取消和迁移时检查重复计数与丢失路径。
3. 核对页面、聊天指令和数据聚合的口径；类型、接口和配置变化同步更新对应文档。
4. 在真实 AstrBot 上检查涉及的行为，尤其是启停/重载、流式结束、Bridge、主题、窄屏和下载。报告实际覆盖，不套用旧版本的测试结果。
5. 分享报错和截图前脱敏，不提交数据文件、会话标识、密钥、缓存或本机临时路径。

修改 provider 包装时，应保留请求/响应不变、记账故障隔离、默认关闭与卸载还原约束。与其他同样包装 provider 的插件共存行为需要在目标环境核对。

## 构建安装包

[构建工具](<../tools/build_package.py>)使用明确的文件白名单，先检查版本、许可与 Python 语法，再写入包并校验 CRC。

~~~bash
python -B tools/build_package.py
~~~

产物写到 _dist，文件名带元数据版本。同名文件已经存在时会报错，不自动覆盖。构建新版本前先确认版本及目标产物，不能为了绕过检查覆盖用户保留的包。

新增文档、运行模块或必要资源时，同步构建工具的 FILES 清单；[契约测试](<../tests/test_contract.py>)会检查当前版本候选包的必要运行文件。数据目录、日志、缓存和开发夹具不应入包。

## 文档维护

当前文档由[文档目录](<README.md>)导航。使用步骤写在使用指南，默认值和范围写在配置参考，统计定义与保存约束写在架构说明。版本验证与设计记录保留历史语境，不把旧测试计数改成新版本结论。

提交文档变更时检查相对链接、命令文件名、配置默认值和截图对应版本。第三方来源见[许可声明](<../THIRD_PARTY_NOTICES.md>)，项目许可见 [AGPL-3.0](<../LICENSE>)。
