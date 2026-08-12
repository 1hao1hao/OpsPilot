"""L3 根因定位 Agent。PRD-04 6步流程。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建</td><td>REQ: 20260713-总体架构, TECH: 04b §3.4.3-3.4.4</td></tr>
<tr><td>0.2.0</td><td>PRD-04 对齐: NoiseFilter 集成、建议模板、证据链提取、分类、System Prompt</td><td>PRD-04 §6-8</td></tr>
</table>
@author xianhuimeng
"""

from __future__ import annotations

import json
import logging
import time

from deeprca.config.settings import get_settings
from deeprca.detection.comparator import MultiDimensionComparator
from deeprca.detection.filters import ExpertRuleEngine, MetricFilter, NoiseFilter
from deeprca.detection.quantile import QuantileAnomalyDetector
from deeprca.detection.volatility import VolatilityDetector
from deeprca.models.result import RootCauseCandidate, RootCauseResult

logger = logging.getLogger(__name__)

# PRD-04 §6.2: System Prompt
ROOT_CAUSE_SYSTEM_PROMPT = """你是故障根因定位专家 Agent。

你的任务是基于多维度分析结果和证据池，推理出故障的根本原因。

分析原则：
1. 优先采用确定性结论：如果专家规则已匹配，直接使用规则结论
2. 变更优先：90% 的故障由变更引起，优先排查变更维度
3. 证据驱动：每个结论必须有证据支撑，不得臆测
4. 置信度量化：基于证据数量和质量计算置信度
   - 5+ 高置信度证据: 0.9~0.95
   - 3~4 中等置信度证据: 0.7~0.85
   - 1~2 低置信度证据: 0.5~0.7
   - 仅推测无实证: < 0.5
5. 证据链排序：按置信度从高到低排序，最多保留 5 条

输出格式（JSON）：
{
  "conclusion": "根因结论（一句话）",
  "confidence": 0.85,
  "evidence_chain": [
    {"dimension": "change", "evidence": "...", "confidence": 0.9},
    ...
  ],
  "suggestions": ["建议措施1", "建议措施2", ...]
}

常见根因模式：
- 变更导致: 配置变更/代码部署/扩缩容 → 服务行为变化
- 下游异常: DB/Redis/MQ 异常 → 调用超时/失败
- 资源饱和: CPU/内存/磁盘饱和 → 性能退化
- 流量突增: 上游 QPS 突增 → 资源耗尽
- 已知问题: 历史故障重现 → 快速定位
"""

# PRD-04 §8: 建议措施模板
SUGGESTION_TEMPLATES = {
    "change": [
        "回滚变更: 检查变更影响范围，评估是否需要紧急回滚",
        "检查变更详情，确认变更内容是否与故障相关",
    ],
    "db_slave_delay": [
        "检查 DB 主从同步状态: SHOW SLAVE STATUS",
        "评估是否需要重建从库",
        "临时切流量到健康的从库",
    ],
    "db_lock": [
        "检查锁等待: SELECT * FROM information_schema.innodb_lock_waits",
        "Kill 长事务: KILL <connection_id>",
        "检查是否有未提交的事务",
    ],
    "redis_memory": [
        "检查大 Key: redis-cli --bigkeys",
        "检查热点 Key: redis-cli --hotkeys",
        "评估是否需要扩容 Redis 内存",
    ],
    "kafka_lag": [
        "检查消费者状态: 确认消费者是否在线",
        "临时增加消费者实例: 扩容消费者 Pod",
        "检查是否有 Rebalance 风暴",
    ],
    "rpc_circuit_breaker": [
        "检查熔断器状态和触发原因",
        "评估是否需要调整熔断阈值",
        "检查下游依赖服务健康状态",
    ],
    "resource_saturation": [
        "紧急扩容: 增加 Pod 副本数",
        "检查资源 Limit 配置是否合理",
        "清理不必要的资源占用",
    ],
    "oom": [
        "检查内存泄漏: 分析 Heap Dump",
        "临时重启服务恢复",
        "调整 JVM 内存参数 -Xmx",
    ],
}


class RootCauseAgent:
    """L3 根因定位 Agent。PRD-04 6步流程。"""

    def __init__(self) -> None:
        self.detector = QuantileAnomalyDetector()
        self.volatility_detector = VolatilityDetector()
        self.comparator = MultiDimensionComparator()
        self.metric_filter = MetricFilter()
        self.noise_filter = NoiseFilter()
        self.rule_engine = ExpertRuleEngine()
        self._settings = get_settings()

    # ------------------------------------------------------------------ #
    #  主入口
    # ------------------------------------------------------------------ #

    async def analyze(
        self,
        alert: dict,
        evidence_summary: dict,
        sub_agent_results: list[dict],
    ) -> dict:
        """6步根因定位:
        1. 多维指标筛选: 筛选出关键异常指标
        2. 过滤低影响抖动: NoiseFilter 过滤瞬态抖动
        3. 基线对比: 对关键指标做周同比/日环比
        4. 异常检测: 四分位 IQR + 波动检测
        5. 专家规则引擎匹配 (R001-R008)
        6. LLM 根因推理（仅当规则未命中时）

        Returns: RootCauseResult 序列化 dict
        """
        trace_id = alert.get("trace_id", "")

        # --- Step 1: 多维指标筛选 ---
        metrics_data = alert.get("metrics", {})
        metric_anomalies = self._run_metric_filter(metrics_data)

        # --- Step 2: 过滤低影响抖动 ---
        metric_anomalies = self.noise_filter.filter_noise(metric_anomalies)

        # --- Step 3: 基线对比 ---
        baseline_comparisons = self._perform_baseline_comparison(alert)

        # --- Step 4: 异常检测 ---
        anomalies = self._run_anomaly_detection(alert)
        # 合并筛选后的指标异常
        anomalies.extend(metric_anomalies)

        # --- Step 5: 专家规则引擎匹配 ---
        matched_rules = self.rule_engine.evaluate(
            evidence_summary, sub_agent_results, alert, anomalies
        )

        rule_set = [r for r in matched_rules if r.get("root_cause")]
        boost_rules = [r for r in matched_rules if r.get("boost")]

        # 如果命中 set_root_cause 规则，直接返回
        if rule_set:
            return self._build_rule_result(
                rule_set, boost_rules, anomalies, trace_id,
                evidence_summary, sub_agent_results,
            )

        # --- Step 6: LLM 根因推理 ---
        return await self._run_llm_reasoning(
            anomalies, baseline_comparisons,
            evidence_summary, sub_agent_results, boost_rules, trace_id,
        )

    # ------------------------------------------------------------------ #
    #  Step 1: 多维指标筛选
    # ------------------------------------------------------------------ #

    def _run_metric_filter(self, metrics_data: dict) -> list[dict]:
        """PRD-04 §4.1: 筛选关键异常指标。"""
        if not metrics_data:
            return []
        return self.metric_filter.filter(metrics_data)

    # ------------------------------------------------------------------ #
    #  Step 3: 基线对比
    # ------------------------------------------------------------------ #

    def _perform_baseline_comparison(self, alert: dict) -> list[dict]:
        """对关键指标做周同比/日环比。"""
        comparisons: list[dict] = []
        metrics = alert.get("metrics", {})

        for metric_name, metric_data in metrics.items():
            if not isinstance(metric_data, dict):
                continue
            current = metric_data.get("current")
            baselines = {
                "last_week": metric_data.get("last_week"),
                "yesterday": metric_data.get("yesterday"),
            }
            if current is None or baselines["last_week"] is None:
                continue
            result = self.comparator.compare(float(current), baselines)
            result["metric"] = metric_name
            comparisons.append(result)

        return comparisons

    # ------------------------------------------------------------------ #
    #  Step 4: 异常检测
    # ------------------------------------------------------------------ #

    def _run_anomaly_detection(self, alert: dict) -> list[dict]:
        """四分位 IQR + 波动检测。"""
        anomalies: list[dict] = []
        metrics = alert.get("metrics", {})

        for metric_name, metric_data in metrics.items():
            if not isinstance(metric_data, dict):
                continue

            # 四分位 IQR 检测
            baseline = metric_data.get("baseline_series", [])
            current = metric_data.get("current")
            if baseline and current is not None:
                iqr_result = self.detector.detect(baseline, float(current))
                if iqr_result.is_anomaly:
                    anomalies.append({
                        "metric": metric_name,
                        "type": "iqr_anomaly",
                        "anomaly_type": iqr_result.anomaly_type,
                        "severity": iqr_result.severity,
                        "value": float(current),
                        "score": iqr_result.confidence,
                        "deviation": iqr_result.deviation_ratio,
                        "deviation_ratio": iqr_result.deviation_ratio,
                        "confidence": iqr_result.confidence,
                        "bounds": iqr_result.details.get("lower_bound", 0.0),
                        "median": iqr_result.baseline_value,
                    })

            # 波动检测
            series = metric_data.get("time_series", [])
            if series and len(series) >= 5:
                vol_results = self.volatility_detector.detect(series)
                spikes = [r for r in vol_results if r["is_spike"]]
                if spikes:
                    anomalies.append({
                        "metric": metric_name,
                        "type": "volatility_spike",
                        "spike_count": len(spikes),
                        "spikes": spikes[:3],
                    })

        return anomalies

    # ------------------------------------------------------------------ #
    #  Step 5: 规则命中结果构建
    # ------------------------------------------------------------------ #

    def _build_rule_result(
        self,
        rule_set: list[dict],
        boost_rules: list[dict],
        anomalies: list[dict],
        trace_id: str,
        evidence_summary: dict,
        sub_agent_results: list[dict],
    ) -> dict:
        """规则命中时构建 Top-3 候选。"""
        rule_set_sorted = sorted(
            rule_set, key=lambda r: r.get("confidence", 0.0), reverse=True
        )

        # 构建证据链
        evidence_chain = self._extract_evidence_chain(evidence_summary, sub_agent_results)

        candidates: list[dict] = []
        base_confidence = rule_set_sorted[0]["confidence"]
        total_boost = sum(r.get("boost", 0.0) for r in boost_rules)
        boosted_confidence = min(base_confidence + total_boost, 0.95)

        # Top-1: 主规则
        candidates.append({
            "rank": 1,
            "root_cause": rule_set_sorted[0]["root_cause"],
            "confidence": round(boosted_confidence, 4),
            "evidence_chain": [r["rule_id"] for r in rule_set_sorted]
            + [r["rule_id"] for r in boost_rules],
            "matched_rule": rule_set_sorted[0]["rule_id"],
            "source": "rule",
        })

        # Top-2 / Top-3: 其他命中的规则
        for idx, rule in enumerate(rule_set_sorted[1:], start=2):
            if idx > 3:
                break
            candidates.append({
                "rank": idx,
                "root_cause": rule["root_cause"],
                "confidence": round(rule["confidence"], 4),
                "evidence_chain": [rule["rule_id"]],
                "matched_rule": rule["rule_id"],
                "source": "rule",
            })

        # 补充候选至 Top-3
        while len(candidates) < 3:
            candidates.append({
                "rank": len(candidates) + 1,
                "root_cause": "其他潜在因素需进一步分析",
                "confidence": 0.3,
                "evidence_chain": [],
                "matched_rule": None,
                "source": "rule",
            })

        best = candidates[0]
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")

        # 生成建议措施
        suggestions = self._generate_suggestions(best["root_cause"], evidence_chain)
        category = self._categorize(best["root_cause"], evidence_chain)

        result = RootCauseResult(
            candidates=[RootCauseCandidate(**c) for c in candidates],
            best_candidate=RootCauseCandidate(**best),
            anomalies_detected=anomalies,
            rule_matched=True,
            llm_used=False,
            trace_id=trace_id,
            timestamp=timestamp,
        )
        result_dict = result.model_dump()
        result_dict["suggestions"] = suggestions
        result_dict["evidence_chain"] = evidence_chain[:5]
        result_dict["matched_rules"] = [
            {"rule_id": r["rule_id"], "name": r.get("name", "")}
            for r in rule_set + boost_rules
        ]
        result_dict["category"] = category
        return result_dict

    # ------------------------------------------------------------------ #
    #  Step 6: LLM 根因推理
    # ------------------------------------------------------------------ #

    async def _run_llm_reasoning(
        self,
        anomalies: list[dict],
        baseline_comparisons: list[dict],
        evidence_summary: dict,
        sub_agent_results: list[dict],
        boost_rules: list[dict],
        trace_id: str,
    ) -> dict:
        """规则未命中时调用 LLM 推理，不可用时降级。"""
        # 构建证据链
        evidence_chain = self._extract_evidence_chain(evidence_summary, sub_agent_results)
        sorted_findings = self._extract_high_confidence_findings(sub_agent_results)

        prompt = self._build_llm_prompt(
            sorted_findings, anomalies, baseline_comparisons, evidence_summary
        )

        try:
            llm_response = await self._call_llm(prompt)
            candidates = self._parse_llm_response(llm_response, boost_rules)
            llm_used = True
        except Exception as exc:
            logger.warning("LLM 不可用，降级返回: %s", exc)
            candidates = self._build_fallback_candidates()
            llm_used = False

        best = candidates[0]
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")

        # 生成建议措施
        suggestions = self._generate_suggestions(
            best.get("root_cause", ""), evidence_chain
        )
        category = self._categorize(best.get("root_cause", ""), evidence_chain)

        result = RootCauseResult(
            candidates=[RootCauseCandidate(**c) for c in candidates],
            best_candidate=RootCauseCandidate(**best),
            anomalies_detected=anomalies,
            rule_matched=False,
            llm_used=llm_used,
            trace_id=trace_id,
            timestamp=timestamp,
        )
        result_dict = result.model_dump()
        result_dict["suggestions"] = suggestions
        result_dict["evidence_chain"] = evidence_chain[:5]
        result_dict["matched_rules"] = [
            {"rule_id": r["rule_id"], "name": r.get("name", "")}
            for r in boost_rules
        ]
        result_dict["category"] = category
        return result_dict

    # ------------------------------------------------------------------ #
    #  证据链提取 (PRD-04 §7)
    # ------------------------------------------------------------------ #

    def _extract_high_confidence_findings(
        self, sub_agent_results: list[dict]
    ) -> list[dict]:
        """从 sub_agent_results 提取高置信度（>= 0.5）findings。"""
        findings: list[dict] = []
        for result in sub_agent_results:
            confidence = result.get("confidence", 0.0)
            if confidence < 0.5:
                continue
            for finding in result.get("findings", []):
                finding_entry = {
                    "agent_name": result.get("agent_name", ""),
                    "dimension": result.get("dimension", ""),
                    "confidence": confidence,
                    **finding,
                }
                findings.append(finding_entry)
        return findings

    def _extract_evidence_chain(
        self,
        evidence: dict,
        sub_agent_results: list[dict],
    ) -> list[dict]:
        """PRD-04 §7: 提取并排序证据链。

        从证据池和子 Agent 结果中提取证据，去重后按置信度排序。
        """
        chain: list[dict] = []

        # 从证据池提取
        sorted_evidences = evidence.get("top_evidences", evidence.get("sorted_evidences", []))
        for ev in sorted_evidences:
            if not isinstance(ev, dict):
                continue
            chain.append({
                "dimension": ev.get("dimension", ""),
                "evidence": ev.get("finding", ev.get("content", "")),
                "confidence": ev.get("confidence", 0.0),
                "source": ev.get("source", ""),
                "level": ev.get("level", ""),
            })

        # 从子 Agent 结果补充
        for result in sub_agent_results:
            for finding in result.get("findings", []):
                if not isinstance(finding, dict):
                    continue
                chain.append({
                    "dimension": result.get("dimension", ""),
                    "evidence": finding.get("description", finding.get("desc", "")),
                    "confidence": finding.get("confidence", result.get("confidence", 0.0)),
                    "source": result.get("agent_name", ""),
                    "category": finding.get("category", finding.get("type", "")),
                })

        # 去重 + 排序
        seen: set[str] = set()
        unique_chain: list[dict] = []
        for item in chain:
            key = item.get("evidence", "")[:50]
            if key and key not in seen:
                seen.add(key)
                unique_chain.append(item)

        unique_chain.sort(key=lambda x: x.get("confidence", 0.0), reverse=True)
        return unique_chain

    # ------------------------------------------------------------------ #
    #  建议措施生成 (PRD-04 §8)
    # ------------------------------------------------------------------ #

    def _generate_suggestions(
        self, conclusion: str, evidence_chain: list[dict]
    ) -> list[str]:
        """PRD-04 §8: 根据根因结论和证据生成建议措施。"""
        suggestions: list[str] = []
        conclusion_lower = conclusion.lower()

        # 根据结论关键词匹配模板
        if "变更" in conclusion_lower or "change" in conclusion_lower or "部署" in conclusion_lower:
            suggestions.extend(SUGGESTION_TEMPLATES["change"])
        if "主从" in conclusion_lower or "slave" in conclusion_lower or "延迟" in conclusion_lower:
            suggestions.extend(SUGGESTION_TEMPLATES["db_slave_delay"])
        if "锁" in conclusion_lower or "lock" in conclusion_lower:
            suggestions.extend(SUGGESTION_TEMPLATES["db_lock"])
        if "redis" in conclusion_lower or "缓存" in conclusion_lower:
            suggestions.extend(SUGGESTION_TEMPLATES["redis_memory"])
        if "kafka" in conclusion_lower or "积压" in conclusion_lower or "消费" in conclusion_lower:
            suggestions.extend(SUGGESTION_TEMPLATES["kafka_lag"])
        if "熔断" in conclusion_lower or "circuit" in conclusion_lower:
            suggestions.extend(SUGGESTION_TEMPLATES["rpc_circuit_breaker"])
        if "资源" in conclusion_lower or "cpu" in conclusion_lower or "内存" in conclusion_lower:
            suggestions.extend(SUGGESTION_TEMPLATES["resource_saturation"])
        if "oom" in conclusion_lower or "内存溢出" in conclusion_lower:
            suggestions.extend(SUGGESTION_TEMPLATES["oom"])

        # 从证据链类别补充
        for evidence in evidence_chain:
            category = evidence.get("category", "")
            if category in SUGGESTION_TEMPLATES:
                suggestions.extend(SUGGESTION_TEMPLATES[category])

        # 去重，保留顺序，最多 5 条
        seen: set[str] = set()
        unique: list[str] = []
        for s in suggestions:
            if s not in seen:
                seen.add(s)
                unique.append(s)
        return unique[:5]

    # ------------------------------------------------------------------ #
    #  分类
    # ------------------------------------------------------------------ #

    def _categorize(self, conclusion: str, evidence_chain: list[dict]) -> str:
        """根据结论和证据链推断根因类别。"""
        conclusion_lower = conclusion.lower()
        if "变更" in conclusion_lower or "change" in conclusion_lower:
            return "change"
        if "数据库" in conclusion_lower or "db" in conclusion_lower or "mysql" in conclusion_lower:
            return "database"
        if "redis" in conclusion_lower or "缓存" in conclusion_lower:
            return "cache"
        if "kafka" in conclusion_lower or "消息" in conclusion_lower:
            return "message_queue"
        if "rpc" in conclusion_lower or "调用" in conclusion_lower:
            return "rpc"
        if "资源" in conclusion_lower or "cpu" in conclusion_lower or "内存" in conclusion_lower:
            return "resource"
        if "流量" in conclusion_lower or "traffic" in conclusion_lower:
            return "traffic"
        return "unknown"

    # ------------------------------------------------------------------ #
    #  LLM 调用
    # ------------------------------------------------------------------ #

    def _build_llm_prompt(
        self,
        sorted_findings: list[dict],
        anomalies: list[dict],
        baseline_comparisons: list[dict],
        evidence_summary: dict,
    ) -> str:
        """构造 LLM 推理 prompt。"""
        findings_text = json.dumps(sorted_findings[:5], ensure_ascii=False, indent=2)
        anomalies_text = json.dumps(anomalies[:5], ensure_ascii=False, indent=2)
        comparisons_text = json.dumps(baseline_comparisons[:5], ensure_ascii=False, indent=2)
        summary_text = json.dumps(evidence_summary, ensure_ascii=False, indent=2)

        return (
            f"{ROOT_CAUSE_SYSTEM_PROMPT}\n\n"
            "基于以下信息进行根因定位：\n\n"
            f"## 证据摘要\n{summary_text}\n\n"
            f"## 高置信度发现\n{findings_text}\n\n"
            f"## 检测到的异常\n{anomalies_text}\n\n"
            f"## 基线对比\n{comparisons_text}\n\n"
            "## 要求\n"
            "请输出 Top-3 根因候选，按可能性从高到低排序。\n"
            "输出 JSON 格式:\n"
            '{"candidates": ['
            '{"rank": 1, "root_cause": "...", "confidence": 0.8, "evidence_chain": ["..."], "suggestions": ["..."]},'
            ' {"rank": 2, ...}, {"rank": 3, ...}'
            "]}"
        )

    async def _call_llm(self, prompt: str) -> str:
        """调用 LLM，返回原始响应文本。"""
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            model=self._settings.llm_model,
            openai_api_base=self._settings.llm_api_base,
            openai_api_key=self._settings.llm_api_key,
            max_tokens=self._settings.llm_max_tokens,
            timeout=self._settings.llm_timeout,
            temperature=0.1,
        )
        response = await llm.ainvoke(prompt)
        return response.content if hasattr(response, "content") else str(response)

    def _parse_llm_response(
        self, response: str, boost_rules: list[dict]
    ) -> list[dict]:
        """解析 LLM 响应为候选列表。"""
        total_boost = sum(r.get("boost", 0.0) for r in boost_rules)

        try:
            text = response.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

            data = json.loads(text)
            raw_candidates = data.get("candidates", [])

            candidates: list[dict] = []
            for raw in raw_candidates[:3]:
                confidence = float(raw.get("confidence", 0.5))
                confidence = min(confidence + total_boost, 1.0)
                candidates.append({
                    "rank": raw.get("rank", len(candidates) + 1),
                    "root_cause": raw.get("root_cause", ""),
                    "confidence": round(confidence, 4),
                    "evidence_chain": raw.get("evidence_chain", []),
                    "matched_rule": None,
                    "source": "llm",
                })

            if not candidates:
                candidates = self._build_fallback_candidates()

            for idx, c in enumerate(candidates, start=1):
                c["rank"] = idx

            return candidates

        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("LLM 响应解析失败: %s, 降级返回", exc)
            return self._build_fallback_candidates()

    def _build_fallback_candidates(self) -> list[dict]:
        """LLM 不可用时降级候选。"""
        return [
            {
                "rank": 1,
                "root_cause": "无法确定根因，建议人工分析",
                "confidence": 0.3,
                "evidence_chain": [],
                "matched_rule": None,
                "source": "fallback",
            },
            {
                "rank": 2,
                "root_cause": "可能存在多因素叠加，需进一步排查",
                "confidence": 0.2,
                "evidence_chain": [],
                "matched_rule": None,
                "source": "fallback",
            },
            {
                "rank": 3,
                "root_cause": "监控数据不足，建议补充采集后重新分析",
                "confidence": 0.1,
                "evidence_chain": [],
                "matched_rule": None,
                "source": "fallback",
            },
        ]
