"""Remove retired dependency and UniFi Protect updater state.

Revision ID: 20260920_0008
Revises: 20260912_0007
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260920_0008"
down_revision = "20260912_0007"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute(sa.text("DELETE FROM system_settings WHERE key IN ('dependency_update_backup_storage_mode', 'dependency_update_backup_mount_source', 'dependency_update_backup_mount_options', 'dependency_update_backup_retention_days', 'dependency_update_backup_min_free_bytes', 'dependency_update_backup_config_status')"))
    op.drop_constraint("external_dependencies_latest_analysis_id_fkey", "external_dependencies", type_="foreignkey")
    op.drop_table("dependency_update_jobs")
    op.drop_table("dependency_update_backups")
    op.drop_table("dependency_update_analyses")
    op.drop_table("external_dependencies")


def downgrade() -> None:
    op.create_table("external_dependencies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ecosystem", sa.String(40), nullable=False), sa.Column("package_name", sa.String(240), nullable=False),
        sa.Column("normalized_name", sa.String(240), nullable=False), sa.Column("current_version", sa.String(120)), sa.Column("latest_version", sa.String(120)),
        sa.Column("dependant_area", sa.String(160), nullable=False), sa.Column("manifest_path", sa.String(320)), sa.Column("manifest_section", sa.String(120)),
        sa.Column("requirement_spec", sa.Text()), sa.Column("is_direct", sa.Boolean(), nullable=False), sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("update_available", sa.Boolean(), nullable=False), sa.Column("risk_status", sa.String(40), nullable=False), sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("latest_analysis_id", postgresql.UUID(as_uuid=True)), sa.Column("metadata", postgresql.JSONB()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("ecosystem", "normalized_name", "manifest_path", "manifest_section", name="ux_external_dependency_manifest_identity"))
    op.create_table("dependency_update_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("dependency_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("target_version", sa.String(120), nullable=False), sa.Column("provider", sa.String(80), nullable=False), sa.Column("model", sa.String(160)), sa.Column("verdict", sa.String(40), nullable=False), sa.Column("summary_markdown", sa.Text(), nullable=False), sa.Column("changelog_source", sa.Text()), sa.Column("changelog_markdown", sa.Text()), sa.Column("usage_summary", postgresql.JSONB()), sa.Column("breaking_changes", postgresql.JSONB(), nullable=False), sa.Column("verification_steps", postgresql.JSONB(), nullable=False), sa.Column("suggested_diff", sa.Text()), sa.Column("raw_result", postgresql.JSONB()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.ForeignKeyConstraint(["dependency_id"], ["external_dependencies.id"], ondelete="CASCADE"))
    op.create_table("dependency_update_backups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("dependency_id", postgresql.UUID(as_uuid=True)), sa.Column("package_name", sa.String(240), nullable=False), sa.Column("ecosystem", sa.String(40), nullable=False), sa.Column("version", sa.String(120)), sa.Column("reason", sa.String(160), nullable=False), sa.Column("archive_path", sa.Text(), nullable=False), sa.Column("storage_root", sa.Text(), nullable=False), sa.Column("checksum_sha256", sa.String(64), nullable=False), sa.Column("size_bytes", sa.BigInteger(), nullable=False), sa.Column("manifest_snapshot", postgresql.JSONB()), sa.Column("config_snapshot", postgresql.JSONB()), sa.Column("metadata", postgresql.JSONB()), sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True)), sa.Column("restored_at", sa.DateTime(timezone=True)), sa.Column("restored_by_user_id", postgresql.UUID(as_uuid=True)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.ForeignKeyConstraint(["dependency_id"], ["external_dependencies.id"], ondelete="SET NULL"), sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"), sa.ForeignKeyConstraint(["restored_by_user_id"], ["users.id"], ondelete="SET NULL"))
    op.create_table("dependency_update_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True), sa.Column("dependency_id", postgresql.UUID(as_uuid=True)), sa.Column("kind", sa.String(40), nullable=False), sa.Column("status", sa.String(40), nullable=False), sa.Column("phase", sa.String(120)), sa.Column("actor", sa.String(160), nullable=False), sa.Column("actor_user_id", postgresql.UUID(as_uuid=True)), sa.Column("target_version", sa.String(120)), sa.Column("backup_id", postgresql.UUID(as_uuid=True)), sa.Column("stdout_log_path", sa.Text()), sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("ended_at", sa.DateTime(timezone=True)), sa.Column("result", postgresql.JSONB()), sa.Column("error", sa.Text()), sa.Column("trace_id", sa.String(32)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.ForeignKeyConstraint(["dependency_id"], ["external_dependencies.id"], ondelete="SET NULL"), sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"), sa.ForeignKeyConstraint(["backup_id"], ["dependency_update_backups.id"], ondelete="SET NULL"))
    op.create_foreign_key("external_dependencies_latest_analysis_id_fkey", "external_dependencies", "dependency_update_analyses", ["latest_analysis_id"], ["id"], ondelete="SET NULL")
    for name, table, columns in (
        ("ix_external_dependencies_ecosystem", "external_dependencies", ["ecosystem"]), ("ix_external_dependencies_package_name", "external_dependencies", ["package_name"]), ("ix_external_dependencies_normalized_name", "external_dependencies", ["normalized_name"]), ("ix_external_dependencies_dependant_area", "external_dependencies", ["dependant_area"]), ("ix_external_dependencies_manifest_path", "external_dependencies", ["manifest_path"]), ("ix_external_dependencies_is_direct", "external_dependencies", ["is_direct"]), ("ix_external_dependencies_is_enabled", "external_dependencies", ["is_enabled"]), ("ix_external_dependencies_update_available", "external_dependencies", ["update_available"]), ("ix_external_dependencies_risk_status", "external_dependencies", ["risk_status"]), ("ix_external_dependencies_last_checked_at", "external_dependencies", ["last_checked_at"]), ("ix_external_dependencies_latest_analysis_id", "external_dependencies", ["latest_analysis_id"]),
        ("ix_dependency_update_analyses_dependency_id", "dependency_update_analyses", ["dependency_id"]), ("ix_dependency_update_analyses_target_version", "dependency_update_analyses", ["target_version"]), ("ix_dependency_update_analyses_verdict", "dependency_update_analyses", ["verdict"]),
        ("ix_dependency_update_backups_dependency_id", "dependency_update_backups", ["dependency_id"]), ("ix_dependency_update_backups_package_name", "dependency_update_backups", ["package_name"]), ("ix_dependency_update_backups_ecosystem", "dependency_update_backups", ["ecosystem"]), ("ix_dependency_update_backups_version", "dependency_update_backups", ["version"]), ("ix_dependency_update_backups_created_by_user_id", "dependency_update_backups", ["created_by_user_id"]), ("ix_dependency_update_backups_restored_at", "dependency_update_backups", ["restored_at"]), ("ix_dependency_update_backups_restored_by_user_id", "dependency_update_backups", ["restored_by_user_id"]),
        ("ix_dependency_update_jobs_dependency_id", "dependency_update_jobs", ["dependency_id"]), ("ix_dependency_update_jobs_kind", "dependency_update_jobs", ["kind"]), ("ix_dependency_update_jobs_status", "dependency_update_jobs", ["status"]), ("ix_dependency_update_jobs_actor", "dependency_update_jobs", ["actor"]), ("ix_dependency_update_jobs_actor_user_id", "dependency_update_jobs", ["actor_user_id"]), ("ix_dependency_update_jobs_backup_id", "dependency_update_jobs", ["backup_id"]), ("ix_dependency_update_jobs_started_at", "dependency_update_jobs", ["started_at"]), ("ix_dependency_update_jobs_ended_at", "dependency_update_jobs", ["ended_at"]), ("ix_dependency_update_jobs_trace_id", "dependency_update_jobs", ["trace_id"]),
    ):
        op.create_index(name, table, columns)
