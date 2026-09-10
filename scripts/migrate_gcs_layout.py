"""Copy-and-validate migration of the legacy `schedovia/<id>.json` GCS
layout (pre-Developer-Preview, MVP_ARCHITECTURE.md §4) into the Developer
Preview's `<organization_id>/<project_id>/<id>.json` layout
(src/mak4i/store/gcs.py).

**Copies, never moves or deletes.** The old `schedovia/` prefix is left
completely untouched by this script — per the approved plan's Deployment
gate, it is retired manually, later, and only after every validation below
passes against the *live* bucket. This script's own exit code communicates
nothing about that decision; it only reports what it copied and validated.

**Dry-run by default.** Without `--execute`, this only lists the planned
`old -> new` mapping and the `organization_id` it would stamp — no network
write of any kind. Pass `--execute` to actually copy, and this still never
touches the source objects.

**Idempotent.** Rerunning with `--execute` after a first successful run
finds every destination object already present with identical (stamped)
content and skips it — safe to rerun after a partial failure.

Usage (dry-run):
    uv run python scripts/migrate_gcs_layout.py \\
        --bucket <artifact-bucket> \\
        --organization-id org_... --project-id prj_...

Usage (execute + validate):
    uv run python scripts/migrate_gcs_layout.py \\
        --bucket <artifact-bucket> \\
        --organization-id org_... --project-id prj_... --execute
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field

from mak4i.models import Artifact
from mak4i.resolution import Resolver

OLD_PREFIX = "schedovia"


@dataclass
class MigrationPlanItem:
    old_name: str
    new_name: str
    artifact_id: str


@dataclass
class ValidationReport:
    old_count: int
    new_count: int
    content_mismatches: list[str] = field(default_factory=list)
    integrity_errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            self.old_count == self.new_count
            and not self.content_mismatches
            and not self.integrity_errors
        )


def _old_blob_names(client, bucket) -> list[str]:
    prefix = f"{OLD_PREFIX}/"
    return sorted(
        blob.name
        for blob in client.list_blobs(bucket, prefix=prefix)
        if blob.name.endswith(".json")
    )


def _new_prefix(organization_id: str, project_id: str) -> str:
    return f"{organization_id}/{project_id}/"


def _new_blob_name(organization_id: str, project_id: str, artifact_id: str) -> str:
    return f"{_new_prefix(organization_id, project_id)}{artifact_id}.json"


def plan_migration(client, bucket, organization_id: str, project_id: str) -> list[MigrationPlanItem]:
    """Computes the `old -> new` mapping with zero writes — the dry-run
    output, and the input to `execute_migration`."""
    items = []
    for old_name in _old_blob_names(client, bucket):
        artifact_id = old_name[len(OLD_PREFIX) + 1 : -len(".json")]
        items.append(
            MigrationPlanItem(
                old_name=old_name,
                new_name=_new_blob_name(organization_id, project_id, artifact_id),
                artifact_id=artifact_id,
            )
        )
    return items


def _stamp(raw: bytes, organization_id: str, project_id: str) -> bytes:
    """Adds `organization_id` and normalizes `project` to the destination
    project id — the *only* fields this migration changes. Everything
    else in the record is byte-for-byte what the source held."""
    record = json.loads(raw)
    record["organization_id"] = organization_id
    record["project"] = project_id
    return json.dumps(record).encode("utf-8")


def execute_migration(
    client, bucket, organization_id: str, project_id: str
) -> list[MigrationPlanItem]:
    """Copies every old object forward, stamping `organization_id`.
    Skips (rather than rewrites) a destination that already holds
    identical stamped content, so a rerun after a partial failure is
    safe. Never touches the source object."""
    written: list[MigrationPlanItem] = []
    for item in plan_migration(client, bucket, organization_id, project_id):
        old_blob = bucket.blob(item.old_name)
        stamped = _stamp(old_blob.download_as_bytes(), organization_id, project_id)

        new_blob = bucket.get_blob(item.new_name)
        if new_blob is not None and new_blob.download_as_bytes() == stamped:
            continue  # already migrated with identical content — idempotent no-op

        bucket.blob(item.new_name).upload_from_string(stamped, content_type="application/json")
        written.append(item)
    return written


def validate_migration(client, bucket, organization_id: str, project_id: str) -> ValidationReport:
    """Object-count parity, per-object content equality (modulo the added
    `organization_id`/normalized `project`), and lineage/integrity
    reconstruction over the new layout — the three checks the plan's
    Deployment gate requires before the old prefix may ever be retired."""
    old_names = _old_blob_names(client, bucket)
    new_prefix = _new_prefix(organization_id, project_id)
    new_names = sorted(
        blob.name for blob in client.list_blobs(bucket, prefix=new_prefix) if blob.name.endswith(".json")
    )

    mismatches: list[str] = []
    new_artifacts: list[Artifact] = []
    for old_name in old_names:
        artifact_id = old_name[len(OLD_PREFIX) + 1 : -len(".json")]
        new_name = _new_blob_name(organization_id, project_id, artifact_id)
        new_blob = bucket.get_blob(new_name)
        if new_blob is None:
            mismatches.append(f"{artifact_id}: missing at {new_name!r}")
            continue

        old_record = json.loads(bucket.get_blob(old_name).download_as_bytes())
        new_record = json.loads(new_blob.download_as_bytes())

        # The migration's only two intentional field changes: organization_id
        # is added, and project is normalized to the destination project id.
        # Everything else must be byte-for-byte identical to the source.
        expected = dict(old_record)
        expected.pop("organization_id", None)
        expected["project"] = project_id
        actual = dict(new_record)
        actual.pop("organization_id", None)
        if actual != expected:
            mismatches.append(f"{artifact_id}: content differs beyond organization_id/project")
        new_artifacts.append(Artifact.model_validate(new_record))

    integrity_errors = [
        f"[{e.kind}] lineage {e.lineage_id!r}: {e.detail}"
        for e in Resolver().resolve(new_artifacts).integrity_errors
    ]

    return ValidationReport(
        old_count=len(old_names),
        new_count=len(new_names),
        content_mismatches=mismatches,
        integrity_errors=integrity_errors,
    )


def _build_bucket(bucket_name: str, gcp_project: str | None):
    from google.cloud import storage

    client = storage.Client(project=gcp_project)
    return client, client.bucket(bucket_name)


def main(argv: list[str] | None = None, *, client=None, bucket=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True, help="GCS bucket name")
    parser.add_argument("--organization-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--gcp-project", default=None, help="GCP project for the storage client (ADC)")
    parser.add_argument(
        "--execute", action="store_true", help="actually copy (default: dry-run, no writes)"
    )
    args = parser.parse_args(argv)

    if client is None or bucket is None:
        client, bucket = _build_bucket(args.bucket, args.gcp_project)

    plan = plan_migration(client, bucket, args.organization_id, args.project_id)
    print(f"Planned migration: {len(plan)} object(s) under {OLD_PREFIX!r} ->")
    print(f"  {args.organization_id}/{args.project_id}/ (organization_id stamped, source untouched)")
    for item in plan:
        print(f"  {item.old_name} -> {item.new_name}")

    if not args.execute:
        print("\nDry run only — no objects were written. Pass --execute to copy.")
        return 0

    written = execute_migration(client, bucket, args.organization_id, args.project_id)
    print(f"\nCopied {len(written)} object(s); {len(plan) - len(written)} already up to date.")

    report = validate_migration(client, bucket, args.organization_id, args.project_id)
    print(
        f"\nValidation: old_count={report.old_count} new_count={report.new_count} "
        f"content_mismatches={len(report.content_mismatches)} "
        f"integrity_errors={len(report.integrity_errors)}"
    )
    for line in report.content_mismatches:
        print(f"  MISMATCH: {line}", file=sys.stderr)
    for line in report.integrity_errors:
        print(f"  INTEGRITY: {line}", file=sys.stderr)

    if not report.ok:
        print("\nFAILED — do not retire the schedovia/ prefix.", file=sys.stderr)
        return 1
    print("\nOK — every validation passed. The schedovia/ prefix is untouched;")
    print("retiring it remains a separate, manual, later decision (M9 gate).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
