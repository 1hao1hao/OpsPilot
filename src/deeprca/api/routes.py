"""API 路由定义 — 将 HTTP 端点接线到 LangGraph 执行流程。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建：5 REST 端点 + WebSocket 接线到 build_coordinator_graph</td><td>REQ: 20260713-总体架构</td></tr>
<tr><td>0.2.0</td><td>P1: WebSocket broadcast 实时推送; P2: Redis-backed analysis_store + E2E HTTP API</td><td>reviewer-fix-3</td></tr>
</table>
@author DeepRCA Team
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from deeprca.api.websocket import ConnectionManager
from deeprca.config import get_settings
from deeprca.models.feedback import FeedbackRequest
from opspilot.compat import get_legacy_graph_adapter
from opspilot.models import AlertEvent

__all__ = ["create_router", "analysis_store"]

logger = logging.getLogger(__name__)

# WebSocket 连接管理器单例
_ws_manager = ConnectionManager()

# 全局编译图单例
_compiled_graph = None


class AnalysisStore:
    """分析状态存储 — Redis 后端，内存降级。

    生产环境使用 Redis 持久化分析状态；
    Mock 环境或 Redis 不可用时自动降级为内存字典。
    """

    def __init__(self):
        self._local: dict[str, dict[str, Any]] = {}
        self._redis = None
        self._redis_checked = False
        self._redis_available = False

    async def _ensure_redis(self):
        """延迟初始化 Redis 连接（带有限重试）。

        首次调用时尝试连接 Redis，如果失败则间隔 1 秒重试最多 3 次。
        一旦成功标记为可用，不再重试。如果全部失败，降级为内存存储，
        但保持 _redis_checked=False 以允许后续操作时再次尝试连接。
        """
        if self._redis_available and self._redis is not None:
            return self._redis
        if self._redis_checked and not self._redis_available:
            # 之前连接失败过，允许后续重试（不永久降级）
            self._redis_checked = False

        settings = get_settings()
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                import redis.asyncio as aioredis

                self._redis = aioredis.Redis(
                    host=settings.redis_host,
                    port=settings.redis_port,
                    db=settings.redis_db,
                    password=settings.redis_password or None,
                    decode_responses=True,
                )
                await self._redis.ping()
                self._redis_available = True
                self._redis_checked = True
                logger.info("AnalysisStore: Redis 连接成功 (attempt %d/%d)", attempt, max_retries)
                return self._redis
            except Exception:
                self._redis = None
                self._redis_available = False
                if attempt < max_retries:
                    logger.warning("AnalysisStore: Redis 连接失败 (attempt %d/%d)，%ds 后重试", attempt, max_retries, 1)
                    await asyncio.sleep(1)
                else:
                    logger.warning("AnalysisStore: Redis 连接失败 (attempt %d/%d)，降级为内存存储", attempt, max_retries)

        self._redis_checked = True
        return None

    async def get(self, trace_id: str) -> dict[str, Any] | None:
        """获取分析记录。"""
        r = await self._ensure_redis()
        if r:
            data = await r.get(f"deeprca:analysis:{trace_id}")
            if data:
                return json.loads(data)
            return None
        return self._local.get(trace_id)

    async def set(self, trace_id: str, data: dict[str, Any]) -> None:
        """设置分析记录（写穿：同时写入内存和 Redis）。"""
        self._local[trace_id] = data
        r = await self._ensure_redis()
        if r:
            await r.set(
                f"deeprca:analysis:{trace_id}",
                json.dumps(data, ensure_ascii=False),
                ex=3600,
            )

    async def update(self, trace_id: str, **kwargs: Any) -> dict[str, Any] | None:
        """部分更新分析记录。"""
        record = await self.get(trace_id)
        if record is None:
            return None
        record.update(kwargs)
        await self.set(trace_id, record)
        return record

    async def delete(self, trace_id: str) -> None:
        """删除分析记录。"""
        self._local.pop(trace_id, None)
        r = await self._ensure_redis()
        if r:
            await r.delete(f"deeprca:analysis:{trace_id}")


# 后台任务引用集合（防止 Task 被 GC 回收）
_background_tasks: set = set()

# 全局分析状态存储实例（Redis 后端，内存降级）
analysis_store = AnalysisStore()


def _get_graph():
    """获取 OpsPilot 兼容适配器单例。"""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = get_legacy_graph_adapter()
    return _compiled_graph


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def create_router() -> APIRouter:
    """创建 API v1 路由器。"""
    router = APIRouter(prefix="/api/v1", tags=["DeepRCA Agent"])

    # ------------------------------------------------------------------ #
    # POST /analyze — 提交故障分析请求
    # ------------------------------------------------------------------ #
    @router.post("/analyze")
    async def submit_analysis(alert: dict):
        """接收告警事件，启动 LangGraph 分析流程。

        注意: 参数类型为 dict 而非 Pydantic 模型，这是有意设计——
        为了自定义校验失败的错误响应格式（返回 {"message": "...", "detail": [...]}
        而非 FastAPI 默认的 422 格式）。请勿改为 Pydantic 模型参数类型注解。

        请求体格式:
        {
            "alert_id": "alt-001",
            "service_name": "order-service",
            "alert_type": "timeout",
            "severity": "P1",
            "timestamp": "2026-07-13T10:00:00Z",
            "description": "接口超时",
            "labels": {"cluster": "prod-cluster", "env": "production", "app": "order"}
        }
        """
        # 使用 AlertEvent Pydantic 模型校验: 必需字段、alert_type 枚举、severity 枚举
        try:
            validated = AlertEvent(**alert)
        except ValidationError as e:
            return JSONResponse(
                status_code=400,
                content={"message": "告警格式校验失败", "detail": e.errors()},
            )

        # 转换为 dict 供 LangGraph 状态使用
        alert_dict = validated.model_dump(mode="json")

        # 生成 trace_id
        trace_id = f"trace-{uuid.uuid4().hex[:12]}"

        # 初始化状态
        initial_state = {
            "alert": alert_dict,
            "task_plan": [],
            "sub_agent_results": [],
            "collected_evidence": None,
            "root_cause": None,
            "report": None,
            "messages": [],
            "trace_id": trace_id,
            "start_time": _now_iso(),
            "status": "running",
            "related_services": [],
            "degraded_mode": False,
        }

        # 存储初始状态
        await analysis_store.set(trace_id, {
            "status": "running",
            "start_time": _now_iso(),
            "result": None,
            "sub_agent_count": 0,
            "root_cause_done": False,
        })

        # 异步执行图（不阻塞 HTTP 响应）
        async def _run_analysis():
            try:
                graph = _get_graph()
                final_state = await graph.ainvoke(initial_state)

                # 更新状态
                await analysis_store.update(
                    trace_id,
                    status=final_state.get("status", "completed"),
                    result=final_state.get("report"),
                    root_cause=final_state.get("root_cause"),
                    sub_agent_count=len(final_state.get("sub_agent_results", [])),
                    root_cause_done=final_state.get("root_cause") is not None,
                    completed_at=_now_iso(),
                )
                # P1 修复：通过 WebSocket broadcast 推送完成事件
                await _ws_manager.broadcast(trace_id, {
                    "trace_id": trace_id,
                    "event": "completed",
                    "status": "completed",
                    "timestamp": _now_iso(),
                })
            except Exception as exc:
                await analysis_store.update(
                    trace_id,
                    status="failed",
                    error=str(exc),
                )
                # P1 修复：通过 WebSocket broadcast 推送错误事件
                await _ws_manager.broadcast(trace_id, {
                    "trace_id": trace_id,
                    "event": "error",
                    "status": "failed",
                    "error": str(exc),
                    "timestamp": _now_iso(),
                })

        # 启动后台任务
        task = asyncio.create_task(_run_analysis())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        # PRD-02 §6.1: 返回 websocket_url（使用外部可访问地址，而非 app_host 的 0.0.0.0）
        settings = get_settings()
        ws_url = f"ws://{settings.app_external_host}:{settings.app_port}/api/v1/analyze/{trace_id}/stream"

        return JSONResponse(
            status_code=202,
            content={
                "trace_id": trace_id,
                "status": "running",
                "websocket_url": ws_url,
            },
        )

    # ------------------------------------------------------------------ #
    # GET /analyze/{trace_id}/status — 查询分析状态
    # ------------------------------------------------------------------ #
    @router.get("/analyze/{trace_id}/status")
    async def get_analysis_status(trace_id: str):
        """查询分析状态。"""
        record = await analysis_store.get(trace_id)
        if record is None:
            return JSONResponse(
                status_code=404,
                content={"trace_id": trace_id, "message": "Trace not found"},
            )

        # PRD-02 §6.2: 返回进度信息（基于实际分析阶段动态计算）
        status = record["status"]
        # progress 基于 L1 维度计数：intake/planner 合并计 1，6 个 L1 维度每个 1，
        # root_cause + reporter 合并计 1。L2 领域专家由 dispatcher 内部并发触发，
        # 不计入此处的粗略进度条，避免 total_stages 动态变化导致进度回跳。
        total_stages = 8
        sub_count = record.get("sub_agent_count", 0)
        rc_done = record.get("root_cause_done", False)
        completed = 1 + min(sub_count, 6) + (1 if rc_done else 0) + (1 if status in ("completed", "failed") else 0)
        progress = {
            "total_dimensions": 6,
            "completed": min(completed, total_stages),
            "failed": 1 if status == "failed" else 0,
            "pending": max(total_stages - min(completed, total_stages) - (1 if status == "failed" else 0), 0),
        }
        elapsed_seconds = 0
        start_time = record.get("start_time")
        if start_time:
            try:
                start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                elapsed_seconds = int((datetime.now(timezone.utc) - start_dt).total_seconds())
            except (ValueError, TypeError):
                pass

        return {
            "trace_id": trace_id,
            "status": status,
            "progress": progress,
            "elapsed_seconds": elapsed_seconds,
            "start_time": record.get("start_time"),
            "completed_at": record.get("completed_at"),
        }

    # ------------------------------------------------------------------ #
    # GET /analyze/{trace_id}/result — 获取分析结果
    # ------------------------------------------------------------------ #
    @router.get("/analyze/{trace_id}/result")
    async def get_analysis_result(trace_id: str):
        """获取分析结果。"""
        record = await analysis_store.get(trace_id)
        if record is None:
            return JSONResponse(
                status_code=404,
                content={"trace_id": trace_id, "message": "Trace not found"},
            )
        if record["status"] not in ("completed", "failed"):
            return JSONResponse(
                status_code=202,
                content={"trace_id": trace_id, "status": record["status"], "message": "Analysis in progress"},
            )
        if record["status"] == "failed":
            return JSONResponse(
                status_code=500,
                content={"trace_id": trace_id, "status": "failed", "error": record.get("error", "Unknown error")},
            )

        # 解析报告 JSON
        report_raw = record.get("result")
        report = json.loads(report_raw) if isinstance(report_raw, str) else report_raw

        return {
            "trace_id": trace_id,
            "status": "completed",
            "report": report,
            "root_cause": record.get("root_cause"),
        }

    # ------------------------------------------------------------------ #
    # POST /feedback — 提交满意度反馈
    # ------------------------------------------------------------------ #
    @router.post("/feedback")
    async def submit_feedback(feedback: dict, trace_id: str = "", token: str = ""):
        """提交满意度反馈。

        支持的字段:
        - trace_id (必需): 分析追踪 ID（可通过请求体或 URL query string 传入）
        - satisfaction (必需): 满意度评分 1-5
        - root_cause_correct (可选): 根因是否正确 (bool)
        - comment (可选): 评论
        - feedback_token (可选): 反馈 token（也可通过 URL query string 传入）

        传参方式:
        - 方式 A: POST /feedback body 中带 trace_id
        - 方式 B: POST /feedback?trace_id=xxx&token=xxx query string 传参
        """
        # 优先从请求体读取 trace_id，若为空则从 query string 读取
        resolved_trace_id = feedback.get("trace_id", "") or trace_id
        if not resolved_trace_id:
            return JSONResponse(
                status_code=400,
                content={"message": "trace_id is required (via body or query string)"},
            )
        feedback["trace_id"] = resolved_trace_id

        # 使用 FeedbackRequest Pydantic 模型做结构化校验
        try:
            validated = FeedbackRequest(**feedback)
        except ValidationError as e:
            return JSONResponse(
                status_code=400,
                content={"message": "反馈格式校验失败", "detail": e.errors()},
            )

        # 如果 URL 中携带 token，回填到反馈记录
        validated_feedback = validated.model_dump()
        if token and not validated_feedback.get("feedback_token"):
            validated_feedback["feedback_token"] = token
        record = await analysis_store.get(resolved_trace_id)
        if record is None:
            return JSONResponse(
                status_code=404,
                content={"message": "Trace not found"},
            )

        # 存储反馈
        await analysis_store.update(resolved_trace_id, feedback=validated_feedback)

        # 尝试推送到 Kafka（非阻塞，失败不影响响应）
        settings = get_settings()
        if not settings.mock_env_enabled:
            asyncio.create_task(_push_feedback_to_kafka(validated_feedback, settings))
        else:
            # Mock 模式下记录日志，确保反馈数据可追溯
            logger.info(
                "Feedback received (mock mode): trace_id=%s satisfaction=%s",
                resolved_trace_id, validated_feedback.get("satisfaction"),
            )

        return {"status": "accepted", "message": "Feedback received"}

    # ------------------------------------------------------------------ #
    # WebSocket /analyze/{trace_id}/stream — 实时推送分析进度
    # ------------------------------------------------------------------ #
    @router.websocket("/analyze/{trace_id}/stream")
    async def analysis_stream(ws: WebSocket, trace_id: str):
        """WebSocket 实时推送分析进度。

        P1 修复：_run_analysis() 通过 _ws_manager.broadcast() 主动推送状态变更，
        此端点同时保留轮询作为心跳兜底，防止 broadcast 事件遗漏。
        """
        await _ws_manager.connect(trace_id, ws)
        try:
            await ws.send_json({"trace_id": trace_id, "event": "connected"})

            record = await analysis_store.get(trace_id)
            if record is None:
                await ws.send_json({"trace_id": trace_id, "event": "error", "message": "Trace not found"})
                await _ws_manager.disconnect(trace_id, ws)
                return

            # 如果分析已完成，立即推送结果
            status = record.get("status", "running")
            if status in ("completed", "failed"):
                report_raw = record.get("result")
                report = json.loads(report_raw) if isinstance(report_raw, str) else report_raw
                await ws.send_json({
                    "trace_id": trace_id,
                    "event": "completed" if status == "completed" else "error",
                    "report": report,
                    "root_cause": record.get("root_cause"),
                })
                return

            # 轮询状态作为心跳兜底（broadcast 提供即时推送）
            sent_completed = False
            while True:
                record = await analysis_store.get(trace_id)
                if record is None:
                    break
                status = record.get("status", "running")

                if status in ("completed", "failed"):
                    # 仅在尚未通过 broadcast 发送过 completed 时才发送
                    # 避免与 _run_analysis 的 broadcast 重复
                    if not sent_completed:
                        report_raw = record.get("result")
                        report = json.loads(report_raw) if isinstance(report_raw, str) else report_raw
                        await ws.send_json({
                            "trace_id": trace_id,
                            "event": "completed" if status == "completed" else "error",
                            "report": report,
                            "root_cause": record.get("root_cause"),
                        })
                        sent_completed = True
                    break

                await ws.send_json({
                    "trace_id": trace_id,
                    "event": "status",
                    "status": status,
                    "timestamp": _now_iso(),
                })

                await asyncio.sleep(1.0)
        except WebSocketDisconnect:
            pass
        finally:
            await _ws_manager.disconnect(trace_id, ws)

    return router


async def _push_feedback_to_kafka(feedback: dict, settings) -> None:
    """推送反馈到 Kafka（生产环境使用）。"""
    try:
        from aiokafka import AIOKafkaProducer

        producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
        )
        await producer.start()
        try:
            await producer.send_and_wait(settings.kafka_feedback_topic, feedback)
        finally:
            await producer.stop()
    except Exception as exc:
        logger.warning("Kafka 反馈推送失败 (trace_id=%s): %s", feedback.get("trace_id", ""), exc)
