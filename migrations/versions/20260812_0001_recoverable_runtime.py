"""Create recoverable runtime facts.

Revision ID: 20260812_0001
Revises: None
"""

import sqlalchemy as sa
from alembic import op

revision = "20260812_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("run_id", sa.String(80), primary_key=True),
        sa.Column("request_id", sa.String(200), nullable=False, unique=True),
        sa.Column("alert_id", sa.String(200), nullable=False),
        sa.Column("alert_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("current_step", sa.String(200)),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("graph_version", sa.String(80), nullable=False),
        sa.Column("config_version", sa.String(80), nullable=False),
        sa.Column("recovered_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_runs_alert_id", "runs", ["alert_id"])
    op.create_index("ix_runs_status", "runs", ["status"])
    op.create_table(
        "steps",
        sa.Column("step_id", sa.String(100), primary_key=True),
        sa.Column("run_id", sa.String(80), sa.ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_name", sa.String(200), nullable=False),
        sa.Column("execution_version", sa.String(80), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("input_ref", sa.String(300)),
        sa.Column("output_ref", sa.String(300)),
        sa.Column("output_json", sa.JSON()),
        sa.Column("error_code", sa.String(100)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("run_id", "step_name", "execution_version", name="uq_step_execution"),
    )
    op.create_index("ix_steps_run_id", "steps", ["run_id"])
    op.create_table(
        "checkpoints",
        sa.Column("checkpoint_id", sa.String(100), primary_key=True),
        sa.Column("run_id", sa.String(80), sa.ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("completed_step", sa.String(200), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("schema_version", sa.String(40), nullable=False),
        sa.Column("graph_version", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_checkpoints_run_id", "checkpoints", ["run_id"])
    op.create_table(
        "tool_executions",
        sa.Column("tool_call_id", sa.String(100), primary_key=True),
        sa.Column("run_id", sa.String(80), sa.ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_name", sa.String(200), nullable=False),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result_json", sa.JSON()),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("latency_ms", sa.Float()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_tool_executions_run_id", "tool_executions", ["run_id"])
    op.create_table(
        "diagnosis_reports",
        sa.Column("run_id", sa.String(80), sa.ForeignKey("runs.run_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("report_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "runtime_events",
        sa.Column("event_id", sa.String(100), primary_key=True),
        sa.Column("run_id", sa.String(80), sa.ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("status", sa.String(24)),
        sa.Column("step_name", sa.String(200)),
        sa.Column("detail_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_runtime_events_run_id", "runtime_events", ["run_id"])
    op.create_index("ix_runtime_event_run_sequence", "runtime_events", ["run_id", "sequence"], unique=True)


def downgrade() -> None:
    op.drop_table("runtime_events")
    op.drop_table("diagnosis_reports")
    op.drop_table("tool_executions")
    op.drop_table("checkpoints")
    op.drop_table("steps")
    op.drop_table("runs")
