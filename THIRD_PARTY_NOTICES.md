# 第三方来源与许可

## EasyCLIProxyAPI 设计参考

统计页的信息层级、筛选方式与图表交互参考 [router-for-me/EasyCLIProxyAPI](<https://github.com/router-for-me/EasyCLIProxyAPI>) 的使用记录模块，源码核查固定于 [97a7d9b76b2462a15076ebaab1719b996885af5d](<https://github.com/router-for-me/EasyCLIProxyAPI/commit/97a7d9b76b2462a15076ebaab1719b996885af5d>)。

当前 [统计查询](<event_store.py>)、[页面逻辑](<views/usage/app.js>)与[图表算法](<views/usage/dashboard.js>)为本插件独立实现，没有移植上游 React/Tauri/Rust 源码或分发其图标、字体和截图。宿主 bridge SDK 由 AstrBot 在运行时提供，未打包其实现。

本插件保留 [AGPL-3.0 许可证](<LICENSE>)。后续若直接复用上游代码，应逐文件记录来源与修改，并随分发包保留相应版权和许可声明；其他依赖和素材分别核查授权。

## 参考项目的 MIT 许可

以下文本来自固定版本的 [上游许可证](<https://github.com/router-for-me/EasyCLIProxyAPI/blob/97a7d9b76b2462a15076ebaab1719b996885af5d/LICENSE>)。

~~~text
MIT License

Copyright (c) 2026 Router-For.ME

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
~~~

## Models.dev 公共参考价格

公共模型名称与参考价格由用户通过更新入口从 [Models.dev API](<https://models.dev/api.json>) 获取，来源与单位可查 [Models.dev](<https://models.dev/>)。解析与同步代码为本插件独立实现，发行包不包含该目录的运行时快照。

请求固定公共地址，不发送本地模型列表、调用记录、消息或凭证。参考价格用于估算，不代表用户供应商的实际账单；手工覆盖由用户独立维护。
