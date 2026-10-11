"""OAuth authorization service tables (MAK-0008)

Revision ID: 0002_oauth
Revises: 0001_initial
Create Date: 2026-10-10

Mirrors the oauth_* tables in mak4i.identity.schema exactly (asserted by
tests/test_control_plane.py). Purely additive: existing organizations,
principals, projects, grants and credentials are untouched, so downgrading
drops only OAuth state (every outstanding OAuth authorization ends; header
credentials keep working).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_oauth"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "oauth_clients",
        sa.Column("client_id", sa.String(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column(
            "organization_id",
            sa.String(),
            sa.ForeignKey("organizations.organization_id"),
            nullable=True,
        ),
        sa.Column("client_name", sa.String(), nullable=True),
        sa.Column("redirect_uris", sa.JSON(), nullable=False),
        sa.Column("token_endpoint_auth_method", sa.String(), nullable=False),
        sa.Column("client_secret_hash", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("fetched_at", _TS, nullable=True),
        sa.Column("cache_expires_at", _TS, nullable=True),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
    )
    op.create_index("ix_oauth_clients_organization_id", "oauth_clients", ["organization_id"])

    op.create_table(
        "oauth_sign_in_codes",
        sa.Column("code_hash", sa.String(), primary_key=True),
        sa.Column(
            "principal_id", sa.String(), sa.ForeignKey("principals.principal_id"), nullable=False
        ),
        sa.Column("issued_by", sa.String(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("expires_at", _TS, nullable=False),
        sa.Column("used_at", _TS, nullable=True),
    )
    op.create_index(
        "ix_oauth_sign_in_codes_principal_id", "oauth_sign_in_codes", ["principal_id"]
    )

    op.create_table(
        "oauth_browser_sessions",
        sa.Column("session_hash", sa.String(), primary_key=True),
        sa.Column("csrf_hash", sa.String(), nullable=False),
        sa.Column(
            "principal_id", sa.String(), sa.ForeignKey("principals.principal_id"), nullable=True
        ),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("expires_at", _TS, nullable=False),
    )

    op.create_table(
        "oauth_authorizations",
        sa.Column("authorization_id", sa.String(), primary_key=True),
        sa.Column(
            "principal_id", sa.String(), sa.ForeignKey("principals.principal_id"), nullable=False
        ),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("resource", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("revoked_reason", sa.String(), nullable=True),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("last_used_at", _TS, nullable=False),
        sa.Column("absolute_expires_at", _TS, nullable=False),
        sa.Column("revoked_at", _TS, nullable=True),
    )
    op.create_index(
        "ix_oauth_authorizations_principal_id", "oauth_authorizations", ["principal_id"]
    )
    op.create_index("ix_oauth_authorizations_client_id", "oauth_authorizations", ["client_id"])

    op.create_table(
        "oauth_codes",
        sa.Column("code_hash", sa.String(), primary_key=True),
        sa.Column(
            "authorization_id",
            sa.String(),
            sa.ForeignKey("oauth_authorizations.authorization_id"),
            nullable=False,
        ),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("redirect_uri", sa.String(), nullable=False),
        sa.Column("code_challenge", sa.String(), nullable=False),
        sa.Column("resource", sa.String(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("expires_at", _TS, nullable=False),
        sa.Column("used_at", _TS, nullable=True),
    )

    op.create_table(
        "oauth_tokens",
        sa.Column("token_hash", sa.String(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column(
            "authorization_id",
            sa.String(),
            sa.ForeignKey("oauth_authorizations.authorization_id"),
            nullable=False,
        ),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("expires_at", _TS, nullable=False),
    )
    op.create_index("ix_oauth_tokens_authorization_id", "oauth_tokens", ["authorization_id"])


def downgrade() -> None:
    op.drop_index("ix_oauth_tokens_authorization_id", table_name="oauth_tokens")
    op.drop_table("oauth_tokens")
    op.drop_table("oauth_codes")
    op.drop_index("ix_oauth_authorizations_client_id", table_name="oauth_authorizations")
    op.drop_index("ix_oauth_authorizations_principal_id", table_name="oauth_authorizations")
    op.drop_table("oauth_authorizations")
    op.drop_table("oauth_browser_sessions")
    op.drop_index("ix_oauth_sign_in_codes_principal_id", table_name="oauth_sign_in_codes")
    op.drop_table("oauth_sign_in_codes")
    op.drop_index("ix_oauth_clients_organization_id", table_name="oauth_clients")
    op.drop_table("oauth_clients")
