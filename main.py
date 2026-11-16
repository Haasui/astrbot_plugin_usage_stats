"""模型用量统计 · 入口与装配层（P0）。

分层：

    attribute.py  归因层   从调用栈模块名挑出插件名        纯逻辑，不依赖 AstrBot
    stats.py      统计层   用量累加、小时桶、排行榜        纯逻辑，不依赖 AstrBot
    patch.py      越界层   类级补丁与精确还原              不依赖 AstrBot（目标类由本文件查好传入）
    main.py       装配层   装补丁 / 记账 / 指令 / Web API  依赖 AstrBot

**这个插件是全项目里唯一越界的。** 原因见 ../../docs/usage-attribution.md 第 2 节：
官方钩子 on_llm_request 看不到 context.llm_generate 发起的调用，它直调 provider.text_chat。

越界的三条自律：

1. **只读不改**——不修改请求、不修改响应、不参与决策。记账抛异常一律吞掉。
2. **默认关闭**——`enabled` 为 false 时不装任何补丁。
3. **严格还原**——terminate() 里逐个把原方法写回，绝不 delattr。
"""

from __future__ import annotations

import asyncio
import copy
import contextvars
import importlib
import secrets
import sys
import time
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from . import store
from .log_tail import append_log_batch, parse_tail_request, read_call_log
from .attribute import (
    measure_request,
    plugin_from_modules,
    provider_id_from_config,
    session_from_locals,
)
from .patch import PatchRegistry, collect_targets, make_wrapper
from .stats import (
    Call,
    Usage,
    cache_hit_text,
    cutoff_for_days,
    day_key,
    fold_by_day_model,
    fold_by_model_across,
    render_call_line,
    render_model_table,
    render_plugin_table,
    tidy_model_key,
    usage_from,
)

PLUGIN_NAME = "astrbot_plugin_usage_stats"
VERSION = "0.3.2"

STACK_DEPTH_CAP = 40
MAX_CALL_LOG_TAIL_LINES = 5000
MAX_CALL_LOG_READ_BYTES = 8 * 1024 * 1024
"""调用栈向上走的最大层数。够覆盖 llm_generate -> 插件 这条链，又不至于慢。"""

# 重入深度：provider 子类若 super().text_chat(...) 调回基类，
# 基类也被打了补丁，就会记两次。深度 > 0 时不再记账。
_DEPTH: contextvars.ContextVar[int] = contextvars.ContextVar(
    f"{PLUGIN_NAME}_depth", default=0
)


def _scan_call_stack(cap: int = STACK_DEPTH_CAP) -> tuple[list[str], list[Any]]:
    """一次遍历同时取回两样东西：模块名（归因）和局部变量（找会话）。

    分两次遍历纯属浪费——这个方法在每一次模型调用上都会跑。
    用 sys._getframe 手工走，比 inspect.stack() 快一个量级。

    Returns:
        (模块名, 各层局部变量)，均由内向外。局部变量**不复制**——
        直接交引用，session_from_locals 只读一遍就丢掉。
    """
    modules: list[str] = []
    locals_seq: list[Any] = []
    try:
        frame = sys._getframe(2)
    except ValueError:  # pragma: no cover
        return modules, locals_seq
    while frame is not None and len(modules) < cap:
        modules.append(frame.f_globals.get("__name__", "") or "")
        try:
            locals_seq.append(frame.f_locals)
        except Exception:  # noqa: BLE001
            locals_seq.append({})
        frame = frame.f_back
    return modules, locals_seq


@register(PLUGIN_NAME, "Haasui", "按插件归因的模型 token 用量统计", VERSION)
class UsageStatsPlugin(Star):
    """模型用量统计。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self._config_handle = config
        self.config: dict[str, Any] = copy.deepcopy(dict(config or {}))
        self._registry = PatchRegistry()
        self._calls: list[Call] = []
        # 本次运行记了多少次。和 _calls 分开：_calls 在每次保存后会被清空
        # （内容已经并进基线），这个数只增不清，用来在状态里回答「这一轮跑了多少」。
        self._run_calls = 0
        # 调用日志：攒在内存里，跟着重扫的节拍写出去。默认关。
        self._call_log: list[str] = []
        self._call_log_path: Path | None = None
        self._call_log_flush_uncertain = False
        self._patched: list[str] = []
        self._skipped_reentrant = 0
        self._record_failures = 0
        self._save_failures = 0
        self._unsaved = 0
        # 上一次运行落盘的数据。**只读基线** —— 保存时永远是「基线 + 本次内存全量」，
        # 所以重复保存不会翻倍（见 tests/test_store.py 的幂等用例）。
        self._baseline: dict = {}
        self._data_file: Any = None
        self._refresh_task: asyncio.Task | None = None
        self._events: Any = None
        self._event_queue: list[Call] = []
        self._event_error = ""
        self._event_persistent = False
        self._event_dropped = 0
        self._event_checkpoint = ""
        self._covered_event_ids: set[str] = set()
        self._price_sync_lock = asyncio.Lock()
        self._price_sync_error = ""
        self._settings_lock = asyncio.Lock()
        self._settings_revision = secrets.randbits(52)
        self._settings_error = ""
        self._settings_reload_required = False

    # ------------------------------------------------------------------ 配置

    def _cfg(self, key: str, default: Any = None) -> Any:
        advanced = self.config.get("advanced")
        if isinstance(advanced, dict):
            value = advanced.get(key)
            if value is not None:
                return value
        value = self.config.get(key, default)
        return default if value is None else value

    # ------------------------------------------------------- 生命周期：装/卸

    async def initialize(self) -> None:
        self._load_history()
        self._open_event_store()
        self._register_view()
        if not self._cfg("enabled", False):
            logger.info(
                f"[{PLUGIN_NAME}] 已加载 {VERSION} | enabled=false，未安装任何补丁"
            )
            return
        done = self._hook_providers()
        logger.info(
            f"[{PLUGIN_NAME}] 已加载 {VERSION} | 装了 {len(done)} 处补丁："
            + (", ".join(done) or "（一个都没装上，检查 provider 结构）")
        )
        # 定时重扫：插件初始化时，provider 子类很可能还没被导入，
        # 那时 Provider.__subclasses__() 是空的，一个都挂不上。
        # 而"挂不上"会导致一个调用都记不到，也就没有任何事件能触发重扫 ——
        # 所以这一步必须是时间驱动，不能挂在记账路径上。
        self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def terminate(self) -> None:
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            self._refresh_task = None
        self._save()
        self._flush_call_log()
        self._flush_event_history()
        if self._events is not None:
            try:
                self._events.close()
            except Exception:  # noqa: BLE001
                logger.warning(f"[{PLUGIN_NAME}] 关闭详细统计存储失败")
        restored = self._registry.restore()
        logger.info(
            f"[{PLUGIN_NAME}] 已卸载 | 还原 {len(restored)} 处补丁 | "
            f"记录 {self._run_calls} 次调用 | 跳过重入 {self._skipped_reentrant} | "
            f"记账失败 {self._record_failures}"
        )

    # ------------------------------------------------------------ 记账

    def _append_call_log(self, call: Call) -> None:
        """攒一行调用日志。**不在这里写盘** —— 记账跑在 provider 的调用路径里。"""
        if not self._cfg("call_log", False):
            return
        try:
            self._call_log.append(render_call_line(call))
            cap = int(self._cfg("call_log_lines", 20000) or 0)
            if cap > 0 and len(self._call_log) > cap:
                del self._call_log[: len(self._call_log) - cap]
        except Exception:  # noqa: BLE001 —— 日志写坏了也不能影响记账
            pass

    def _flush_call_log(self) -> bool:
        """Flush pending rows; never retry a batch whose rollback is uncertain."""
        if not self._call_log:
            return not self._call_log_flush_uncertain
        if self._call_log_path is None:
            return False
        try:
            self._call_log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._call_log_path, "a+b") as fh:
                ok, rollback_ok = append_log_batch(fh, self._call_log)
            if ok:
                self._call_log.clear()
                self._trim_call_log()
                return not self._call_log_flush_uncertain
            if not rollback_ok:
                # The append may have partially reached disk; drop it to prevent duplicate retry.
                self._call_log.clear()
                self._call_log_flush_uncertain = True
            logger.warning(f"[{PLUGIN_NAME}] 调用日志写入失败；rollback_ok={rollback_ok}")
            return False
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{PLUGIN_NAME}] 调用日志写入失败：{e!r}")
            return False

    def _trim_call_log(self) -> None:
        """按行数裁。攒到两倍才动手，免得每次追加都重写一遍整个文件。"""
        cap = int(self._cfg("call_log_lines", 20000) or 0)
        if cap <= 0 or self._call_log_path is None:
            return
        try:
            with open(self._call_log_path, encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
            if len(lines) <= cap * 2:
                return
            with open(self._call_log_path, "w", encoding="utf-8") as fh:
                fh.writelines(lines[-cap:])
        except OSError:
            pass

    def _record(
        self,
        provider: Any,
        method_name: str,
        response: Any,
        kwargs: dict,
        elapsed_ms: int = 0,
        meta: dict | None = None,
    ) -> None:
        """一次模型调用的记账。由 patch.py 调用，异常必须自己吞掉。"""
        try:
            if _DEPTH.get() > 0:
                # 子类 super() 调回基类时我们会被进两次，第二次不算
                self._skipped_reentrant += 1
                return
            token = _DEPTH.set(1)
            try:
                provider_id = provider_id_from_config(
                    getattr(provider, "provider_config", None),
                    type(provider).__name__,
                )

                model = ""
                try:
                    model = str(provider.get_model() or "")
                except Exception:  # noqa: BLE001
                    pass

                modules, locals_seq = _scan_call_stack()

                # 会话两条路：主对话经 kwargs 传（runner 传的是 conversation_id，
                # session_id 是废弃字段但仍在传）；插件调用只能从栈上找 event。
                session = str(
                    kwargs.get("conversation_id") or kwargs.get("session_id") or ""
                ).strip()
                if not session:
                    session = session_from_locals(locals_seq)
                images = len(kwargs.get("image_urls") or [])

                shape = measure_request(kwargs, elapsed_ms)
                plugin_name = plugin_from_modules(modules, ignore=(PLUGIN_NAME,))
                if self._cfg("debug", False):
                    # 会话不进 /usage（那儿要保持一屏），这是个观察点
                    logger.info(
                        f"[{PLUGIN_NAME}] {plugin_name} -> {provider_id}|{model} "
                        f"via {method_name} | 会话={session} | 图片={images}"
                        f" | sys={shape.system} ctx={shape.context}/{shape.turns}"
                        f" prompt={shape.prompt} tools={shape.tools}/{shape.tool_count}"
                        f" | {shape.ms}ms"
                    )

                result = str((meta or {}).get("result") or ("success" if _looks_ok(response) else "failed"))
                if result not in ("success", "failed", "canceled"):
                    result = "failed"
                if response is not None and not _looks_ok(response):
                    result = "failed"
                call = Call(
                    plugin=plugin_name,
                    provider_id=provider_id,
                    model=model,
                    timestamp=time.time(),
                    usage=usage_from(getattr(response, "usage", None), response),
                    ok=result == "success",
                    images=images,
                    method=method_name,
                    session=session,
                    shape=shape,
                    result=result,
                    ttft_ms=(meta or {}).get("ttft_ms"),
                )
                self._calls.append(call)
                self._run_calls += 1
                self._append_call_log(call)
                if self._cfg("detail_history", False):
                    self._event_queue.append(call)
                    cap = max(1, min(200000, int(self._cfg("detail_max_events", 20000) or 20000)))
                    if len(self._event_queue) > cap:
                        self._event_dropped += len(self._event_queue) - cap
                        del self._event_queue[: len(self._event_queue) - cap]
                self._unsaved += 1
                self._maybe_save()
                self._trim()
            finally:
                _DEPTH.reset(token)
        except Exception:  # noqa: BLE001
            # 记账自己的故障绝不能让对话挂掉，也绝不往上抛
            self._record_failures += 1

    def _trim(self) -> None:
        cap = int(self._cfg("memory_cap", 20000) or 0)
        if cap > 0 and len(self._calls) > cap:
            # The cap is soft while persistence fails: never discard unsaved usage.
            if not self._save():
                self._save_failures = max(1, self._save_failures)

    def _hook_providers(self) -> list[str]:
        """发现并给 provider 打补丁。**可重复调用** —— install 靠 MARK 幂等。

        每次都用 importlib 重新取模块属性，而不是留一份 import 时的引用：
        万一哪天 AstrBot 改成重新导入模块，类对象就会被换掉，届时这里仍能跟上。
        （v4.29.0 的 dynamic_import_provider 用的是普通 import，没有 reload，
        所以目前类对象是稳定的；这一步是给未来上保险。）
        """
        try:
            module = importlib.import_module("astrbot.core.provider.provider")
            base = getattr(module, "Provider")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[{PLUGIN_NAME}] 取不到 Provider 基类，本轮不挂补丁：{exc!r}")
            return []
        targets = collect_targets(base, ("text_chat", "text_chat_stream"))
        done = self._registry.install(targets, make_wrapper(self._record, capture_timing=True))
        if done:
            self._patched = sorted(set(self._patched) | set(done))
        return done

    async def _refresh_loop(self) -> None:
        interval = int(self._cfg("refresh_interval", 60) or 0)
        if interval <= 0:
            return
        while True:
            try:
                await asyncio.sleep(interval)
                self._flush_call_log()
                self._flush_event_history()
                added = self._hook_providers()
                if added:
                    logger.info(
                        f"[{PLUGIN_NAME}] 补挂 {len(added)} 处补丁：{', '.join(added)}"
                    )
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{PLUGIN_NAME}] 重扫 provider 失败：{exc!r}")

    # ------------------------------------------------------------ 落盘

    def _load_history(self) -> None:
        """读回上次运行的数据。失败不致命——大不了这次只在内存里统计。"""
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

            self._data_file = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME / "usage.json"
            self._call_log_path = self._data_file.parent / "calls.log"
            self._baseline = store.load(self._data_file)
            self._event_checkpoint = store.load_checkpoint(self._data_file)
            if not self._baseline and self._data_file.exists() and self._data_file.stat().st_size > 0:
                # 文件在、也有内容，却读不出记录：多半是版本比本插件新，或者坏了。
                # 不说一声的话，用户只会看到一张空表，不知道历史去哪了。
                logger.warning(
                    f"[{PLUGIN_NAME}] {self._data_file} 有内容但读不出记录"
                    "（版本比本插件新，或文件损坏）—— 本次只在内存里统计"
                )
            if self._baseline:
                logger.info(
                    f"[{PLUGIN_NAME}] 读回历史：{len(self._baseline)} 个插件，"
                    f"{self._data_file}"
                )
        except Exception as exc:  # noqa: BLE001
            self._data_file = None
            self._call_log_path = None
            logger.warning(f"[{PLUGIN_NAME}] 读历史失败，本次只在内存里统计：{exc!r}")

    def _save(self) -> bool:
        """把「基线 + 本次内存全量」合并、裁剪后写盘。任何异常都吞掉。"""
        self._flush_event_history()
        if self._data_file is None:
            return False
        try:
            merged = store.merge(
                self._baseline, store.snapshot(fold_by_day_model(self._calls))
            )
            keep = int(self._cfg("keep_days", 60) or 0)
            pruned = store.prune(merged, time.time(), keep)
            checkpoint = self._event_checkpoint
            if self._events is not None and self._event_persistent:
                checkpoint = self._events.prepare_checkpoint(self._event_checkpoint, (call.event_id for call in self._event_queue))
            ok = store.save(self._data_file, pruned, event_checkpoint=checkpoint)
            if ok:
                self._event_checkpoint = checkpoint
                if self._events is not None and self._event_persistent:
                    self._covered_event_ids = self._events.covered_ids(checkpoint)
                # 内存基线也跟着裁。不裁的话，本次会话里还看得到已经开始丢弃的天，
                # 重启之后它们又消失了 —— 看到的东西应该等于留存下来的东西。
                # 裁它是安全的：本次会话的调用全在 _calls 里，合并时还会加回来。
                # 必须清空：merged 里已经含了 _calls 的全部内容，基线也换成了它。
                # 不清的话，下一次保存会把同一批调用再加一遍 —— 保存 N 次就放大 N 倍。
                self._calls.clear()
                self._baseline = pruned
                self._unsaved = 0
                self._save_failures = 0
                if self._events is not None and self._event_persistent:
                    try:
                        self._events.finalize_checkpoint(checkpoint)
                        self._events.prune(keep_days=max(0, keep), max_events=max(1, min(200000, int(self._cfg("detail_max_events", 20000) or 20000))))
                    except Exception as exc:  # noqa: BLE001
                        self._event_error = "日汇总已保存，但详细历史裁剪失败，请检查存储"
                        logger.warning(f"[{PLUGIN_NAME}] 详细历史裁剪失败：{exc!r}")
            else:
                self._save_failures += 1
            return ok
        except Exception:  # noqa: BLE001
            self._save_failures += 1
            return False

    def _maybe_save(self) -> None:
        every = int(self._cfg("save_every", 50) or 0)
        if every > 0 and self._unsaved >= every:
            self._save()

    def _open_event_store(self) -> None:
        """Open optional detail storage without changing provider behavior."""
        try:
            from .event_store import EventStore

            if self._data_file is None:
                raise OSError("plugin data directory unavailable")
            self._data_file.parent.mkdir(parents=True, exist_ok=True)
            self._events = EventStore(self._data_file.parent / "events.db")
            self._event_persistent = True
            self._covered_event_ids = self._events.covered_ids(self._event_checkpoint)
            recovered = self._events.uncovered(self._event_checkpoint)
            self._calls.extend(recovered)
            self._unsaved += len(recovered)
            if recovered:
                logger.info(f"[{PLUGIN_NAME}] 恢复 {len(recovered)} 条尚未提交到日汇总的调用")
            else:
                self._events.prune(
                    keep_days=max(0, int(self._cfg("keep_days", 60) or 0)),
                    max_events=max(1, min(200000, int(self._cfg("detail_max_events", 20000) or 20000))),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[{PLUGIN_NAME}] 详细统计存储不可用：{exc!r}")
            self._event_error = ("旧明细库缺少日汇总提交证明，原库已保留；请备份并核对后迁移，当前仅显示日汇总"
                                 if getattr(exc, "requires_reconciliation", False)
                                 else "详细统计存储不可用，当前页面仅提供内存查询与日汇总")
            self._event_persistent = False
            try:
                if self._events is not None:
                    self._events.close()
                self._events = EventStore(":memory:")
            except Exception:  # noqa: BLE001
                self._events = None

    def _flush_event_history(self) -> bool:
        """Retry stable event IDs; leave daily aggregation independent of detail IO."""
        if not self._event_queue:
            return not self._event_error
        if self._events is None or not self._event_persistent:
            return False
        completed = 0
        try:
            for call in self._event_queue:
                self._events.append(call)
                completed += 1
            del self._event_queue[:completed]
            completed = 0
            # Do not prune uncheckpointed details; they are the crash recovery log.
            self._event_error = ""
            return True
        except Exception as exc:  # noqa: BLE001
            if completed:
                del self._event_queue[:completed]
            self._event_error = "详细统计写入失败，日汇总仍可用；未写入明细等待重试"
            logger.warning(f"[{PLUGIN_NAME}] 详细统计写入失败：{exc!r}")
            return False

    async def api_prices(self):
        """Save an authenticated, bounded manual price table."""
        from astrbot.api.web import error_response, json_response, request

        denied = self._auth_error()
        if denied is not None:
            return denied
        if self._events is None or not self._event_persistent:
            return error_response("价格存储不可用，未保存", status_code=503)
        try:
            body = await request.body()
            if len(body) > 262144:
                return error_response("价格表超过 256 KiB 上限", status_code=413)
            payload = await request.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("prices"), list):
                raise ValueError("请提供 prices 数组")
            if len(payload["prices"]) > 500:
                raise ValueError("最多保存 500 个模型价格")
            await asyncio.to_thread(self._events.save_prices, payload["prices"])
            return json_response({"saved": True})
        except (ValueError, TypeError) as exc:
            return error_response(str(exc), status_code=400)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[{PLUGIN_NAME}] 保存价格失败：{exc!r}")
            return error_response("价格保存失败，原价表保持不变", status_code=503)

    async def api_price_sync(self):
        """Fetch a fixed public reference catalog without sending local model usage."""
        from astrbot.api.web import error_response, json_response, request

        denied = self._auth_error()
        if denied is not None:
            return denied
        if self._events is None or not self._event_persistent:
            return error_response("价格存储不可用，未更新公共目录", status_code=503)
        try:
            if len(await request.body()) > 4096:
                return error_response("价格同步请求超过上限", status_code=413)
            payload = await request.json()
            if not isinstance(payload, dict) or set(payload) - {"source"} or payload.get("source", "models.dev") != "models.dev":
                return error_response("只支持固定Models.dev公共价格源，不接受自定义地址", status_code=400)
        except Exception:
            return error_response("价格同步请求无效", status_code=400)
        if self._price_sync_lock.locked():
            return error_response("价格目录正在更新，请稍后刷新", status_code=409)
        async with self._price_sync_lock:
            try:
                from .price_catalog import fetch_catalog

                rows = await asyncio.to_thread(fetch_catalog)
                updated_at = time.time()
                await asyncio.to_thread(self._events.save_catalog, rows, updated_at=updated_at)
                self._price_sync_error = ""
                return json_response({"synced": True, "source": "models.dev", "rows": len(rows), "updated_at": updated_at})
            except Exception as exc:  # noqa: BLE001
                self._price_sync_error = "公共价格更新失败，已保留上次目录和手工价格。"
                logger.warning(f"[{PLUGIN_NAME}] 更新公共参考价失败：{exc!r}")
                return error_response(self._price_sync_error, status_code=502)

    def _settings_snapshot(self) -> dict:
        handle = self._config_handle
        return {
            "enabled": bool(self._cfg("enabled", False)),
            "detail_history": bool(self._cfg("detail_history", False)),
            "keep_days": int(self._cfg("keep_days", 60)),
            "detail_max_events": int(self._cfg("detail_max_events", 20000)),
            "writable": isinstance(handle, dict) and (callable(getattr(handle, "save_config_async", None)) or callable(getattr(handle, "save_config", None))),
            "revision": self._settings_revision,
            "reload_required": self._settings_reload_required,
            "runtime_error": self._settings_error,
        }

    async def _apply_settings_runtime(self) -> None:
        if self._cfg("enabled", False):
            self._hook_providers()
            if self._refresh_task is None or self._refresh_task.done():
                self._refresh_task = asyncio.create_task(self._refresh_loop())
        else:
            task, self._refresh_task = self._refresh_task, None
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            self._registry.restore()
            self._patched.clear()
        if self._data_file is not None and not self._save():
            self._settings_error = "采集设置已生效；统计数据仍等待保存，请检查存储状态。"

    async def api_settings(self):
        """Persist explicit settings through the original AstrBotConfig object."""
        from astrbot.api.web import error_response, json_response, request

        denied = self._auth_error()
        if denied is not None:
            return denied
        if not self._settings_snapshot()["writable"]:
            return error_response("当前宿主未提供配置保存接口，未修改设置", status_code=501)
        try:
            if len(await request.body()) > 4096:
                return error_response("设置请求超过上限", status_code=413)
            payload = await request.json()
            if not isinstance(payload, dict) or set(payload) - {"settings", "revision"}:
                raise ValueError("设置请求格式无效")
            values = payload.get("settings")
            names = {"enabled", "detail_history", "keep_days", "detail_max_events"}
            if not isinstance(values, dict) or set(values) != names:
                raise ValueError("请提交完整的采集设置")
            for key in ("enabled", "detail_history"):
                if type(values[key]) is not bool:
                    raise ValueError("采集开关必须为布尔值")
            for key, lower, upper in (("keep_days", 0, 3650), ("detail_max_events", 1, 200000)):
                if type(values[key]) is not int or not lower <= values[key] <= upper:
                    raise ValueError(f"{key} 必须为 {lower} 至 {upper} 的整数")
            revision = payload.get("revision", self._settings_revision)
            if type(revision) is not int:
                raise ValueError("设置版本无效")
        except (ValueError, TypeError):
            return error_response("设置无效：保留天数为0-3650，明细上限为1-200000，开关需为布尔值", status_code=400)
        if self._settings_lock.locked():
            return error_response("设置正在保存，请稍后重试", status_code=409)
        async with self._settings_lock:
            if revision != self._settings_revision:
                return error_response("设置已被更新，请还原服务器值后重新修改", status_code=409)
            handle = self._config_handle
            original = copy.deepcopy(dict(handle))
            patch = copy.deepcopy(values)
            advanced = original.get("advanced")
            advanced_keys = set(advanced).intersection(values) if isinstance(advanced, dict) else set()
            if advanced_keys:
                patch["advanced"] = copy.deepcopy(advanced)
                patch["advanced"].update({key: values[key] for key in advanced_keys})
            try:
                saver = getattr(handle, "save_config_async", None)
                if callable(saver):
                    committed = await saver(replace_config=patch)
                    if committed is not True:
                        raise OSError("asynchronous configuration save was not confirmed")
                else:
                    committed = await asyncio.to_thread(handle.save_config, replace_config=patch)
                if committed is False:
                    raise OSError("configuration snapshot was not committed")
            except Exception as exc:  # noqa: BLE001
                for key, value in values.items():
                    if handle.get(key) == value:
                        if key in original:
                            handle[key] = original[key]
                        else:
                            handle.pop(key, None)
                current_advanced = handle.get("advanced")
                if isinstance(current_advanced, dict):
                    for key in advanced_keys:
                        if current_advanced.get(key) == values[key]:
                            current_advanced[key] = advanced[key]
                logger.warning(f"[{PLUGIN_NAME}] 保存Web设置失败：{exc!r}")
                return error_response("设置保存失败，运行中的配置保持不变", status_code=503)
            self.config = copy.deepcopy(dict(handle))
            self._settings_revision += 1
            self._settings_error = ""
            self._settings_reload_required = False
            applied = True
            try:
                await self._apply_settings_runtime()
            except Exception as exc:  # noqa: BLE001
                applied = False
                self._settings_reload_required = True
                self._settings_error = "设置已保存，但运行状态未全部应用，请重载插件。"
                logger.warning(f"[{PLUGIN_NAME}] 应用Web设置失败：{exc!r}")
            return json_response({"saved": True, "applied": applied,
                "reload_required": self._settings_reload_required, "settings": self._settings_snapshot()})

    def _register_view(self) -> None:
        """注册 WebUI 视图的数据接口。

        视图页面在 views/usage/index.html，走官方的
        window.AstrBotPluginPage bridge（SDK 由 /api/plugin/page/bridge-sdk.js 提供），
        apiGet("stats") 就会打到这个路由。
        """
        try:
            self.context.register_web_api(
                f"/{PLUGIN_NAME}/stats",
                self.api_stats,
                ["GET"],
                "按插件与模型的用量统计（?days=N 按天筛选）",
            )
            self.context.register_web_api(
                f"/{PLUGIN_NAME}/reset",
                self.api_reset,
                ["POST"],
                "清空全部用量记录（落盘历史 + 本次运行）",
            )
            self.context.register_web_api(
                f"/{PLUGIN_NAME}/prices",
                self.api_prices,
                ["POST"],
                "保存模型手工价格（USD / 百万 Token）",
            )
            self.context.register_web_api(
                f"/{PLUGIN_NAME}/price_sync",
                self.api_price_sync,
                ["POST"],
                "更新Models.dev公共参考价格（不上传本地用量）",
            )
            self.context.register_web_api(
                f"/{PLUGIN_NAME}/settings", self.api_settings, ["POST"], "保存并应用用量采集设置",
            )
            logger.info(f"[{PLUGIN_NAME}] 已注册视图接口 /{PLUGIN_NAME}/stats、/reset、/prices、/price_sync、/settings")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[{PLUGIN_NAME}] 注册视图接口失败，视图页面会取不到数：{exc!r}")

    @staticmethod
    def _auth_error() -> Any:
        """Protect dashboard data, exports, pricing writes and resets with host login."""
        try:
            from astrbot.api.web import error_response, request
        except Exception:  # noqa: BLE001
            return None
        if not getattr(request, "username", None):
            return error_response("需要登录 Dashboard", status_code=401)
        return None

    async def api_reset(self):
        """视图上的「重置」：清空落盘历史 + 本次运行记录。"""
        from astrbot.api.web import json_response

        denied = self._auth_error()
        if denied is not None:
            return denied
        result = self._reset_all()
        logger.info(
            f"[{PLUGIN_NAME}] 已从视图重置用量记录："
            f"历史 {result['baseline_plugins']} 个插件，内存 {result['memory_calls']} 次"
        )
        return json_response(result)

    async def api_stats(self):
        """视图要的数据。?days=N 按天筛选（0 = 全部），?tail=N 取调用日志的尾部。"""
        from astrbot.api.web import error_response, json_response, request

        view = str(request.query.get("view", "") or "")
        if view in ("dashboard", "export"):
            denied = self._auth_error()
            if denied is not None:
                return denied
            self._flush_event_history()
            if self._events is None:
                return error_response("统计存储不可用，请检查插件数据目录", status_code=503)
            query = {key: request.query.get(key) for key in (
                "days", "start", "end", "plugin", "model", "provider", "result",
                "group", "page", "page_size", "speed_mode",
            ) if request.query.get(key) is not None}
            snapshot_time = time.time()
            aggregates = self._view_days()
            covered_ids = self._covered_event_ids | {call.event_id for call in self._calls}
            try:
                if view == "export":
                    payload = await asyncio.to_thread(self._events.export, query, aggregates, snapshot_time, covered_ids)
                else:
                    payload = await asyncio.to_thread(self._events.dashboard, query, aggregates, snapshot_time, covered_ids)
                    warnings = payload.setdefault("coverage", {}).setdefault("warnings", [])
                    if self._save_failures:
                        warnings.append("日汇总保存失败，未保存调用仍保留在内存中；内存可能超过配置上限。")
                    if self._event_queue:
                        warnings.append(f"还有 {len(self._event_queue)} 条明细等待写入；性能与明细仅覆盖已保存样本。")
                    if self._event_dropped:
                        warnings.append(f"写入队列曾达到上限，{self._event_dropped} 条明细未保留；日汇总不受此限制影响。")
                    payload["settings"] = self._settings_snapshot()
                    payload.setdefault("pricing", {})["sync_error"] = self._price_sync_error
                    payload["collection"] = {
                        "plugin_enabled": bool(self._cfg("enabled", False)),
                        "patched_count": len(self._patched),
                        "wrapped_methods": list(self._patched),
                        "recorded_count": self._run_calls,
                        "detail_enabled": bool(self._cfg("detail_history", False)),
                        "detail_available": self._event_persistent,
                        "detail_error": self._event_error,
                        "pending": len(self._event_queue),
                        "dropped": self._event_dropped,
                    }
                return json_response(payload)
            except ValueError as exc:
                return error_response(str(exc), status_code=400)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{PLUGIN_NAME}] 详细统计查询失败：{exc!r}")
                return error_response("统计查询失败，请稍后重试", status_code=503)

        # 调用日志：先把内存里没落盘的那几行刷出去，再从文件读尾巴。
        # 不刷的话最常见的现象是「开了、聊了几句，面板还是空的」——那几行还在内存里。
        invalid_tail, tail = parse_tail_request(
            request.query.get("tail", 0), MAX_CALL_LOG_TAIL_LINES
        )
        if invalid_tail:
            return json_response({"enabled": bool(self._cfg("call_log", False)), "path": str(self._call_log_path) if self._call_log_path else "", "lines": [], "state": "invalid_request", "returned": 0, "truncated": False, "error_code": "invalid_tail"})
        if tail > 0:
            enabled = bool(self._cfg("call_log", False))
            if not enabled:
                return json_response({"enabled": False, "path": str(self._call_log_path) if self._call_log_path else "", "lines": [], "state": "disabled", "returned": 0, "truncated": False, "error_code": ""})
            flushed = self._flush_call_log()
            return json_response(read_call_log(
                self._call_log_path, True, flushed, tail, MAX_CALL_LOG_READ_BYTES
            ))

        days = 0
        try:
            days = int(request.query.get("days", 0) or 0)
        except Exception:  # noqa: BLE001
            days = 0
        cutoff = cutoff_for_days(days)

        nested = self._view_days()

        # 下钻：点了某个插件（或某个模型）的「趋势」时前端才会带上这两个参数。
        # 模型级序列只在有人要的时候现算，常规轮询不必背这份数据。
        focus_plugin = str(request.query.get("plugin", "") or "")
        focus_model = str(request.query.get("model", "") or "")
        focus_series = (
            store.series_for(nested, focus_plugin, focus_model, cutoff)
            if focus_plugin
            else None
        )
        view, series, by_plugin = store.window_view(nested, cutoff)

        # 上一个同长周期：用同一对界圈出来，供前端算环比。范围是「全部」时没有上一周期。
        previous = None
        if days > 0:
            prev_from = day_key(time.time() - (2 * days - 1) * 86400)
            prev_view, _, _ = store.window_view(nested, prev_from, cutoff)
            previous = store.aggregate(prev_view)

        # 焦点在单个插件时算不了环比（API 不返回单插件的历史），前端会隐藏这部分

        grand = 0
        plugins: list[dict] = []
        for name, models in view.items():
            plugin_usage = Usage()
            rows: list[dict] = []
            for model_key, bucket in models.items():
                rows.append(
                    {
                        "name": tidy_model_key(model_key),
                        "total": bucket.usage.total,
                        "calls": bucket.calls,
                        "failed": bucket.failed,
                        "images": bucket.images,
                        "cache_hit": cache_hit_text(bucket.usage),
                        "input_other": bucket.usage.input_other,
                        "input_cached": bucket.usage.input_cached,
                        "output": bucket.usage.output,
                        "cache_reported": bucket.usage.cache_reported,
                    }
                )
                plugin_usage = plugin_usage + bucket.usage
            rows.sort(key=lambda r: r["total"], reverse=True)
            plugins.append(
                {
                    "name": name,
                    "total": plugin_usage.total,
                    "calls": sum(b.calls for b in models.values()),
                    "failed": sum(b.failed for b in models.values()),
                    "images": sum(b.images for b in models.values()),
                    "cache_hit": cache_hit_text(plugin_usage),
                    # 交给前端做整体汇总：比率不能相加，原始计数可以
                    "cache_reported": plugin_usage.cache_reported,
                    "output": plugin_usage.output,
                    "input_other": plugin_usage.input_other,
                    "input_cached": plugin_usage.input_cached,
                    "models": rows,
                }
            )
            grand += plugin_usage.total

        plugins.sort(key=lambda p: p["total"], reverse=True)
        return json_response(
            {
                "total_tokens": grand,
                "patched": len(self._patched),
                "recorded": self._run_calls,
                "plugins": plugins,
                "series": series,
                "series_by_plugin": by_plugin,
                "focus_series": focus_series,
                "range_days": days,
                "previous": previous,
                "generated_at": time.strftime("%H:%M:%S"),
            }
        )

    def _view_days(self) -> dict:
        """插件 -> 天 -> 模型 -> Bucket（落盘历史 + 本次内存）。

        按天筛选和画趋势图都要它。这里直接复用 store.merge 的「基线 + 内存全量」口径，
        免得再写一份合并逻辑、再养一个翻倍的 bug。
        """
        merged = store.merge(
            self._baseline, store.snapshot(fold_by_day_model(self._calls))
        )
        return store.as_buckets(merged)

    def _view(self) -> dict:
        """展示用的一张表：插件 -> 模型 -> Bucket（不分天）。"""
        return store.by_plugin_model(self._view_days())

    # ------------------------------------------------------------ 指令

    @filter.command("usage", alias={"用量", "usg"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def usage_status(self, event: AstrMessageEvent, arg: str = ""):
        """按模型看用量。子命令：full / day / week / status。

        默认一行一个模型（调用数、token、缓存命中）。插件拆分在 full 里，运行期
        计数在 status 里 —— 它们是**不同口径**（本次运行 vs 落盘历史 + 本次），
        混在一屏会让人以为数字对不上。
        """

        if not self._cfg("enabled", False):
            yield event.plain_result(
                f"模型用量统计 {VERSION}\nenabled=false —— 未安装补丁，没有数据。"
            )
            return

        scope = (arg or "").strip().lower()
        if scope in ("help", "?", "帮助"):
            yield event.plain_result(self._help_text())
            return
        if scope in ("status", "状态"):
            yield event.plain_result(self._status_text())
            return

        self._save()  # 顺手落盘：这是一条管理指令，不差这点开销
        view, label = self._usage_scope(scope)
        if scope in ("full", "详细"):
            yield event.plain_result(render_plugin_table(view, label, VERSION))
            return
        yield event.plain_result(
            render_model_table(fold_by_model_across(view), label, VERSION)
        )

    def _help_text(self) -> str:
        """子命令清单。

        这份清单和 _usage_scope 认的口径必须一致 —— tests/test_command.py 会对着
        语法树核一遍，漏一个子命令就红。
        """
        return "\n".join([
            f"模型用量统计 {VERSION} · 指令",
            "",
            "/usage          一行一个模型：调用数、token、缓存命中",
            "/usage full     明细：按插件拆到模型，带失败数与图片数",
            "/usage day      今天的用量",
            "/usage week     近 7 天的用量",
            "/usage status   运行期状态：补丁数、本次运行记录、落盘情况",
            "/usage help     这份清单",
            "",
            "别名：用量 / usg。管理员指令，其他参数按「全部」处理。",
            "",
            "/usage 的数字是「落盘历史 + 本次」；/usage status 里的「本次运行记录」",
            "只算这次启动以来 —— 两者不该相等。",
        ])

    def _usage_scope(self, scope: str) -> tuple[dict, str]:
        """按子命令圈一段数据。返回 (插件 → 模型 → Bucket, 口径说明)。"""
        days = {"day": 1, "today": 1, "今天": 1, "week": 7, "7": 7, "近7天": 7}.get(
            scope, 0
        )
        label = {1: "今天", 7: "近 7 天"}.get(days, "全部")
        cutoff = cutoff_for_days(days)
        view, series, _ = store.window_view(self._view_days(), cutoff)
        span = f"{series[0]['day'][5:]} ~ {series[-1]['day'][5:]}" if series else "无记录"
        return view, f"{label} · {span}"

    def _status_text(self) -> str:
        """运行期状态。口径和用量数字不同，所以单独一条。"""
        file_name = self._data_file.name if self._data_file is not None else "不可用"
        return "\n".join([
            f"模型用量统计 {VERSION} · 运行状态",
            f"补丁 {len(self._patched)} 处 | 跳过重入 {self._skipped_reentrant} | "
            f"记账失败 {self._record_failures}",
            f"本次运行记录 {self._run_calls:,} 次 | 未落盘 {self._unsaved} 条 | "
            f"保存失败 {self._save_failures} 次",
            f"落盘文件：{file_name}",
            "",
            "「本次运行」只算这次启动以来的调用；/usage 的数字是落盘历史 + 本次。",
            "两者不该相等 —— 混在一起看会以为统计坏了。",
        ])


    @filter.command("usage_clear", alias={"用量清空", "usg_clear"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def usage_clear(self, event: AstrMessageEvent):
        """清空**本次运行**的内存记录。落盘的历史不动。"""
        n = len(self._calls)
        event_ids = {call.event_id for call in self._calls}
        if self._events is not None:
            try:
                self._events.discard(event_ids)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{PLUGIN_NAME}] 清理未保存明细失败：{exc!r}")
                yield event.plain_result("清理未保存明细失败，本次记录保持不变，请稍后重试。")
                return
        self._event_queue = [call for call in self._event_queue if call.event_id not in event_ids]
        self._calls.clear()
        self._unsaved = 0
        self._skipped_reentrant = 0
        self._record_failures = 0
        yield event.plain_result(
            f"模型用量统计：已清空 {n} 条内存记录。"
            f"\n落盘的历史没动 —— 要连它一起清用 /usage_reset。"
        )

    def _reset_all(self) -> dict[str, Any]:
        """清空落盘历史与本次运行记录，返回一份能同时给指令和视图用的结果。

        为什么需要它：展示的数据是「启动时读进来的基线 + 本次内存」。基线里存的是
        **当时记下的插件名**，代码改了归因规则也**不会回溯修正它们** —— 改完归因
        如果不重置，旧的错数据会一直并进结果里，直到被 keep_days 滚动清掉。

        写回空结构而不是删文件：删是不可逆的，写空随时能再来一次。
        """
        old = len(self._baseline)
        calls = len(self._calls)
        self._baseline = {}
        self._calls.clear()
        self._unsaved = 0
        self._event_queue.clear()
        self._event_dropped = 0
        detail_ok = self._events is not None and self._event_persistent
        if self._events is not None:
            try:
                self._events.clear()
                if self._event_persistent:
                    self._event_error = ""
            except Exception as exc:  # noqa: BLE001
                detail_ok = False
                self._event_error = "详细历史重置失败，请重试；旧明细可能仍然可见"
                logger.warning(f"[{PLUGIN_NAME}] 详细历史重置失败：{exc!r}")
        self._event_checkpoint = ""
        self._covered_event_ids.clear()
        ok = store.save(self._data_file, {}, event_checkpoint="") if self._data_file else False
        ok = ok and detail_ok
        if self._data_file is None:
            note = "没有可用的数据文件，只清了内存记录。"
        elif ok:
            note = "日汇总与调用明细已清空；手工价格和文本日志保留。"
        else:
            note = "重置未能全部持久化，旧日汇总或明细可能仍保留，请检查存储后重试。"
        return {
            "baseline_plugins": old,
            "memory_calls": calls,
            "saved": bool(ok),
            "details_cleared": detail_ok,
            "path": str(self._data_file) if self._data_file else "",
            "note": note,
        }

    @filter.command("usage_reset", alias={"用量重置", "usg_reset"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def usage_reset(self, event: AstrMessageEvent):
        """清空**落盘的历史**（连同本次运行的记录）。"""
        result = self._reset_all()
        status = "日汇总与明细重置完成" if result["saved"] else "重置未全部持久化"
        yield event.plain_result(f"模型用量统计：{status}。\n{result['note']}")


def _looks_ok(response: Any) -> bool:
    """失败也算一次调用——失败率是排障指标。"""
    if response is None:
        return False
    role = getattr(response, "role", None)
    return role != "err"
