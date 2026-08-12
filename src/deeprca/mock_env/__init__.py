"""Mock 环境模块 — 模拟 K8s/DB/Redis/Kafka/微服务，用于端到端验证。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建：Mock 环境模块骨架</td><td>REQ: 20260713-总体架构</td></tr>
<tr><td>0.2.0</td><td>完整实现：5 个模拟器 + 场景管理 + Mock API</td><td>PRD-05</td></tr>
</table>
@author DeepRCA Team
"""

from deeprca.mock_env.k8s_simulator import K8sSimulator
from deeprca.mock_env.mysql_simulator import MySQLSimulator
from deeprca.mock_env.redis_simulator import RedisSimulator
from deeprca.mock_env.kafka_simulator import KafkaSimulator
from deeprca.mock_env.service_simulator import MicroserviceSimulator
from deeprca.mock_env.alert_simulator import AlertSimulator, SCENARIOS, get_alert_simulator
from deeprca.mock_env.mock_routes import create_mock_router

__all__ = [
    "K8sSimulator",
    "MySQLSimulator",
    "RedisSimulator",
    "KafkaSimulator",
    "MicroserviceSimulator",
    "AlertSimulator",
    "SCENARIOS",
    "get_alert_simulator",
    "create_mock_router",
]

