# 0.3.2 Web设置与运行诊断验证

## 当前行为

数据管理页可编辑采集总开关、调用明细开关、保留天数与明细数量上限。保存调用原始 AstrBotConfig 对象的 save_config_async，或兼容同步 save_config；配置不是另存到页面缓存或插件自建影子文件。

接口已核对 [AstrBot 4.29.4 配置实现](<https://github.com/AstrBotDevs/AstrBot/blob/v4.29.4/astrbot/core/config/astrbot_config.py>)。异步保存必须明确确认提交，同步接口需正常完成。失败恢复本次修改的宿主值，运行配置保持不变。成功后启停采集包装、维护定时重扫并应用保留策略；若运行状态未完全应用，回执明确提示已保存但需要重载。

普通界面和顶部提示不展示内部方法计数。运行诊断默认关闭，展开后可看采集包装方法数、队列状态和后端实际返回的方法清单；不推测四个方法的具体名称。真实错误与统计覆盖告警仍然可见。

## 已执行验证

- [Web设置测试](<../tests/test_web_settings.py>)覆盖宿主引用、同步与异步保存、实际文件保存并重建读回、失败回滚、未确认提交拒绝、字段范围、登录、版本冲突、重载后的旧草稿、运行时启停和部分应用回执。
- [原装配测试](<../tests/test_integration.py>)、配置一致性和采集不变量测试通过，价格、旧历史与原采集路径不因表单改变而重新设计。
- [页面冒烟](<../tests/view_smoke.mjs>)覆盖真实设置值、四字段与版本回传、失败和缺回执、编辑保护、冲突还原、保存中的旧响应及默认关闭诊断。
- 设置文件样本在工作区隔离目录中创建，保存并读取后删除；没有修改真实 AstrBot 配置或系统权限。

浏览器视觉检查仍受当前环境缺少 bsk 工具限制。本轮验证宿主API契约和页面交互桩，尚未直接访问用户远程AstrBot会话。

## 复测与上机

~~~bash
python -B tests/test_web_settings.py
python -B tests/test_integration.py
python -B tests/test_config.py
python -B tests/test_invariants.py
python -B tests/test_views.py
node tests/view_smoke.mjs
~~~

安装完整0.3.2包后重载插件并强制刷新页面一次。随后在数据管理页修改设置并保存，确认刷新后仍保留；普通成功路径不需要再跳回插件配置。可在测试副本上再次重载以验证宿主配置持久化。

缩小保留范围会在统计保存时清理旧数据，需先备份。主开关关闭但明细开关开启时，不会擅自启动采集。若宿主没有提供配置保存接口，表单明确禁用保存，不假报成功。
