"""K8s 集群模拟器 — 模拟 Pod/Service/Deployment 资源状态与故障注入。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建：K8s 集群模拟器</td><td>PRD-05 §3</td></tr>
</table>
@author DeepRCA Team
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class PodPhase(Enum):
    PENDING = "Pending"
    RUNNING = "Running"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    UNKNOWN = "Unknown"


@dataclass
class PodStatus:
    name: str
    phase: PodPhase
    conditions: dict[str, bool]
    containers: list[dict]
    restart_count: int = 0
    cpu_usage: float = 0.0
    memory_usage: float = 0.0
    cpu_limit: float = 2.0
    memory_limit: float = 4096.0
    node_name: str = ""
    start_time: str = ""
    ip: str = ""


@dataclass
class DeploymentStatus:
    name: str
    namespace: str
    replicas_desired: int
    replicas_available: int
    replicas_ready: int
    image: str
    pods: list[PodStatus] = field(default_factory=list)
    creation_timestamp: str = ""


@dataclass
class ServiceStatus:
    name: str
    namespace: str
    type: str
    cluster_ip: str
    ports: list[dict]
    selector: dict[str, str]
    endpoints: list[str] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


class K8sSimulator:
    """K8s 集群模拟器。

    管理 Deployment/Service/Pod 资源，支持 Pod 重启、资源压力、
    Pod 崩溃、扩缩容等故障注入。
    """

    def __init__(self) -> None:
        self.deployments: dict[str, DeploymentStatus] = {}
        self.services: dict[str, ServiceStatus] = {}
        self.pods: dict[str, PodStatus] = {}
        self.events: list[dict[str, Any]] = []
        self._init_default_cluster()

    def _init_default_cluster(self) -> None:
        """初始化默认集群状态。"""
        services = [
            ("order-service", "v2.3.0", 3),
            ("payment-service", "v1.8.0", 2),
            ("user-service", "v3.1.0", 3),
            ("inventory-service", "v2.0.0", 2),
        ]
        for name, image, replicas in services:
            self._create_deployment(name, "default", image, replicas)

    def _create_deployment(self, name: str, namespace: str, image: str, replicas: int) -> None:
        """创建 Deployment。"""
        pods: list[PodStatus] = []
        for i in range(replicas):
            pod_name = f"{name}-{random.randint(1000, 9999)}-{i}"
            pod = PodStatus(
                name=pod_name,
                phase=PodPhase.RUNNING,
                conditions={"Ready": True, "Initialized": True, "Scheduled": True},
                containers=[{"name": name, "image": image, "ready": True}],
                restart_count=0,
                cpu_usage=0.5 + i * 0.1,
                memory_usage=1024.0 + i * 100,
                cpu_limit=2.0,
                memory_limit=4096.0,
                node_name=f"node-{i % 3}",
                ip=f"10.244.{i % 3}.{i + 1}",
            )
            pods.append(pod)
            self.pods[pod_name] = pod

        deployment = DeploymentStatus(
            name=name,
            namespace=namespace,
            replicas_desired=replicas,
            replicas_available=replicas,
            replicas_ready=replicas,
            image=image,
            pods=pods,
            creation_timestamp=_now_iso(),
        )
        self.deployments[name] = deployment

        service = ServiceStatus(
            name=name,
            namespace=namespace,
            type="ClusterIP",
            cluster_ip=f"10.96.{random.randint(1, 254)}.{random.randint(1, 254)}",
            ports=[{"port": 8080, "targetPort": 8080, "protocol": "TCP"}],
            selector={"app": name},
            endpoints=[pod.ip for pod in pods],
        )
        self.services[name] = service

    # ─────────────────────────────────────
    # 故障注入
    # ─────────────────────────────────────
    def inject_pod_restart(self, deployment_name: str, pod_index: int = 0, reason: str = "OOMKilled") -> dict[str, Any]:
        """注入 Pod 重启故障。"""
        deployment = self.deployments.get(deployment_name)
        if not deployment or pod_index >= len(deployment.pods):
            return {"error": "deployment or pod not found"}

        pod = deployment.pods[pod_index]
        pod.restart_count += 1
        pod.phase = PodPhase.RUNNING
        pod.start_time = _now_iso()

        event = {
            "type": "Warning",
            "reason": reason,
            "message": f"Pod {pod.name} was restarted due to {reason}",
            "timestamp": _now_iso(),
            "pod": pod.name,
        }
        self.events.append(event)
        return {"status": "injected", "pod": pod.name, "restart_count": pod.restart_count}

    def inject_resource_pressure(
        self, deployment_name: str, cpu_usage: float | None = None, memory_usage: float | None = None
    ) -> dict[str, Any]:
        """注入资源压力。"""
        deployment = self.deployments.get(deployment_name)
        if not deployment:
            return {"error": "deployment not found"}

        for pod in deployment.pods:
            if cpu_usage is not None:
                pod.cpu_usage = min(cpu_usage, pod.cpu_limit * 1.2)
            if memory_usage is not None:
                pod.memory_usage = min(memory_usage, pod.memory_limit * 1.2)

        return {"status": "injected", "deployment": deployment_name}

    def inject_pod_crash(self, deployment_name: str, pod_index: int = 0) -> dict[str, Any]:
        """注入 Pod 崩溃。"""
        deployment = self.deployments.get(deployment_name)
        if not deployment or pod_index >= len(deployment.pods):
            return {"error": "deployment or pod not found"}

        pod = deployment.pods[pod_index]
        pod.phase = PodPhase.FAILED
        pod.conditions["Ready"] = False
        deployment.replicas_ready = max(0, deployment.replicas_ready - 1)

        event = {
            "type": "Warning",
            "reason": "BackOff",
            "message": f"Pod {pod.name} crashed",
            "timestamp": _now_iso(),
        }
        self.events.append(event)
        return {"status": "crashed", "pod": pod.name}

    def scale_deployment(self, deployment_name: str, replicas: int) -> dict[str, Any]:
        """模拟扩缩容。"""
        deployment = self.deployments.get(deployment_name)
        if not deployment:
            return {"error": "deployment not found"}

        old_replicas = deployment.replicas_desired
        deployment.replicas_desired = replicas

        if replicas > len(deployment.pods):
            for i in range(len(deployment.pods), replicas):
                pod_name = f"{deployment_name}-{random.randint(1000, 9999)}-{i}"
                pod = PodStatus(
                    name=pod_name,
                    phase=PodPhase.RUNNING,
                    conditions={"Ready": True, "Initialized": True, "Scheduled": True},
                    containers=[{"name": deployment_name, "ready": True}],
                    cpu_usage=0.5,
                    memory_usage=1024.0,
                    cpu_limit=2.0,
                    memory_limit=4096.0,
                    node_name=f"node-{i % 3}",
                    ip=f"10.244.{i % 3}.{i + 1}",
                )
                deployment.pods.append(pod)
                self.pods[pod_name] = pod
        else:
            for pod in deployment.pods[replicas:]:
                pod.phase = PodPhase.SUCCEEDED
                pod.conditions["Ready"] = False
            deployment.pods = deployment.pods[:replicas]

        deployment.replicas_available = replicas
        deployment.replicas_ready = replicas
        return {"status": "scaled", "old": old_replicas, "new": replicas}

    # ─────────────────────────────────────
    # 查询
    # ─────────────────────────────────────
    def list_deployments(self) -> list[dict[str, Any]]:
        """列出所有 Deployment。"""
        return [
            {
                "name": d.name,
                "namespace": d.namespace,
                "replicas_desired": d.replicas_desired,
                "replicas_available": d.replicas_available,
                "replicas_ready": d.replicas_ready,
                "image": d.image,
            }
            for d in self.deployments.values()
        ]

    def get_deployment(self, name: str) -> dict[str, Any] | None:
        """查询 Deployment 详情。"""
        d = self.deployments.get(name)
        if not d:
            return None
        return {
            "name": d.name,
            "namespace": d.namespace,
            "replicas_desired": d.replicas_desired,
            "replicas_available": d.replicas_available,
            "replicas_ready": d.replicas_ready,
            "image": d.image,
            "pods": [{"name": p.name, "phase": p.phase.value, "restart_count": p.restart_count} for p in d.pods],
            "creation_timestamp": d.creation_timestamp,
        }

    def get_pods(self, deployment_name: str) -> list[dict[str, Any]] | None:
        """查询 Pod 列表。"""
        d = self.deployments.get(deployment_name)
        if not d:
            return None
        return [
            {
                "name": p.name,
                "phase": p.phase.value,
                "conditions": p.conditions,
                "restart_count": p.restart_count,
                "cpu_usage": p.cpu_usage,
                "memory_usage": p.memory_usage,
                "cpu_limit": p.cpu_limit,
                "memory_limit": p.memory_limit,
                "node_name": p.node_name,
                "ip": p.ip,
                "start_time": p.start_time,
            }
            for p in d.pods
        ]

    def list_services(self) -> list[dict[str, Any]]:
        """列出所有 Service。"""
        return [
            {
                "name": s.name,
                "namespace": s.namespace,
                "type": s.type,
                "cluster_ip": s.cluster_ip,
                "ports": s.ports,
                "endpoints": s.endpoints,
            }
            for s in self.services.values()
        ]

    def list_events(self) -> list[dict[str, Any]]:
        """查询事件列表。"""
        return list(self.events)

    def reset(self) -> None:
        """重置到基线状态。"""
        self.deployments.clear()
        self.services.clear()
        self.pods.clear()
        self.events.clear()
        self._init_default_cluster()
