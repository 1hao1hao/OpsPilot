# 可追溯实验数字草稿（不是简历文案）

正式状态 `completed`；配置 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\config.json`；dataset `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\dataset.json`；上游 `{'commit': '7ea09b865a9b9d411b59459b15133d84f8c2784a', 'demo_image_version': '3.1.0', 'collector_version': '0.160.0'}`。

## full_adaptive
Fault Top1=10/18；Top3=12/18；Normal=2/3。
Avg Tool/Expert=6.10/1.00；P50/P95=17038.33/21673.26ms。
来源 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\metrics.json` / `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\records.json`，只能引用此模式与版本，fallback 运行=0。

## fixed_full
Fault Top1=10/18；Top3=11/18；Normal=0/3。
Avg Tool/Expert=13.00/0.00；P50/P95=27408.37/28744.40ms。
来源 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\metrics.json` / `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\records.json`，只能引用此模式与版本，fallback 运行=0。

## adaptive_no_l2
Fault Top1=10/18；Top3=12/18；Normal=2/3。
Avg Tool/Expert=5.71/0.00；P50/P95=13809.91/14738.43ms。
来源 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\metrics.json` / `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\records.json`，只能引用此模式与版本，fallback 运行=0。

调用差值来源 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\comparisons.json`；Recovery 逐次事实来源 `D:\deeprca\DeepRCA-Agent-master\artifacts\otel_demo_rca_v1\20261005T041333Z-9b9d16\pairs.json` 与各 trial recovery/；不能把人工重启描述成自愈。

没有企业生产数据，不宣称生产环境验证。

真实数量与独立审计来源 `artifacts/otel_demo_rca_v1/20261005T041333Z-9b9d16/release_completeness.json`、`independent_audit.json` 和 `final_verification.json`。63 次 RCA 与 21 次生命周期不可混用；18 次故障出现/恢复不包括 Normal。
