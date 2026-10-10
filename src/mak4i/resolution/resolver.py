from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from mak4i.models import Artifact
from mak4i.resolution.types import DetectedConflict, IntegrityError, ResolutionResult

Subject = tuple[str, str]
"""(artifact_type, normalized subject_key) within one organization/project."""


class Resolver:
    """Integrity checking and deterministic subject-conflict detection
    (MAK-0004 Part A, MAK-0005 Part A).

    Order of operations — every step recorded in the trace:

    1. group the project's versions by lineage and find each lineage's
       single current head (fail closed on integrity errors; a lineage whose
       last version was withdrawn by a resolution simply has no head);
    2. detect conflicts over **all** heads: two or more lineages whose heads
       share `artifact_type` and `subject_key`, or a subject whose
       resolution is still in progress (§A4.2);
    3. only then apply the caller's filters, which choose what is
       *reported* but never what is *detected* (§A6.2): a conflict is
       reported, with all of its candidates, when its type matches and at
       least one candidate matches the tags.

    Deliberately store-independent and stateless, with no
    scenario-specific logic: a caching lineage and a database lineage go
    through identical code. Tags never create a conflict, content is never
    compared to infer one, and no candidate is ever chosen by recency.
    """

    def resolve(
        self,
        all_project_artifacts: Iterable[Artifact],
        artifact_type: str | None = None,
        tags: list[str] | None = None,
        *,
        resolving: set[Subject] | frozenset[Subject] = frozenset(),
    ) -> ResolutionResult:
        trace: list[str] = []
        integrity_errors: list[IntegrityError] = []

        lineages = self._group_by_lineage(all_project_artifacts)
        trace.append(
            f"considered {sum(len(m) for m in lineages.values())} artifact(s) "
            f"across {len(lineages)} lineage(s)"
        )

        heads: dict[str, Artifact] = {}
        for lineage_id, members in lineages.items():
            outcome = self._resolve_lineage(lineage_id, members)
            if outcome is None:
                trace.append(f"lineage {lineage_id!r}: withdrawn by a resolution; no current head")
            elif isinstance(outcome, IntegrityError):
                integrity_errors.append(outcome)
                trace.append(
                    f"lineage {lineage_id!r}: integrity error ({outcome.kind}) — "
                    f"{outcome.detail}; contributes nothing"
                )
            else:
                heads[lineage_id] = outcome
                trace.append(f"lineage {lineage_id!r}: current is {outcome.artifact_id!r}")

        # Detection over every head, before any filter (§A3.2).
        by_subject: dict[Subject, list[Artifact]] = defaultdict(list)
        for lineage_id in sorted(heads):
            head = heads[lineage_id]
            if head.subject_key is not None:
                by_subject[(head.artifact_type, head.subject_key)].append(head)
        conflicted = {
            subject: members
            for subject, members in by_subject.items()
            if len(members) > 1 or subject in resolving
        }
        for subject in sorted(resolving):
            conflicted.setdefault(subject, by_subject.get(subject, []))
        for (subject_type, subject_key), members in sorted(conflicted.items()):
            note = " (resolution in progress)" if (subject_type, subject_key) in resolving else ""
            trace.append(
                f"conflict{note}: lineages {[m.lineage_id for m in members]} claim "
                f"artifact_type={subject_type!r} subject_key={subject_key!r}"
            )

        # Reporting filters (§A6.2).
        conflicts = [
            DetectedConflict(
                artifact_type=subject_type,
                subject_key=subject_key,
                lineage_ids=[m.lineage_id for m in members],
                artifacts=members,
            )
            for (subject_type, subject_key), members in sorted(conflicted.items())
            if (artifact_type is None or subject_type == artifact_type)
            and (not tags or not members or any(set(tags).issubset(m.tags) for m in members))
        ]
        resolved = [
            head
            for _lineage_id, head in sorted(heads.items())
            if not (head.subject_key is not None and (head.artifact_type, head.subject_key) in conflicted)
            and self._matches(head, artifact_type, tags)
        ]
        trace.append(
            f"applied filters artifact_type={artifact_type!r} tags={tags!r}: "
            f"resolved {len(resolved)} artifact(s), reported {len(conflicts)} conflict(s)"
        )

        return ResolutionResult(
            resolved=resolved,
            conflicts=conflicts,
            integrity_errors=integrity_errors,
            trace=trace,
        )

    def heads(self, all_project_artifacts: Iterable[Artifact]) -> dict[str, Artifact]:
        """Current head per lineage (lineages with integrity errors or
        withdrawn ones omitted)."""
        out = {}
        for lineage_id, members in self._group_by_lineage(all_project_artifacts).items():
            outcome = self._resolve_lineage(lineage_id, members)
            if isinstance(outcome, Artifact):
                out[lineage_id] = outcome
        return out

    # -- internals --------------------------------------------------------

    @staticmethod
    def _group_by_lineage(artifacts: Iterable[Artifact]) -> dict[str, list[Artifact]]:
        lineages: dict[str, list[Artifact]] = defaultdict(list)
        for artifact in artifacts:
            lineages[artifact.lineage_id].append(artifact)
        return dict(lineages)

    @staticmethod
    def _matches(artifact: Artifact, artifact_type: str | None, tags: list[str] | None) -> bool:
        if artifact_type is not None and artifact.artifact_type != artifact_type:
            return False
        if tags and not set(tags).issubset(artifact.tags):
            return False
        return True

    def _resolve_lineage(
        self, lineage_id: str, members: list[Artifact]
    ) -> Artifact | IntegrityError | None:
        actives = [m for m in members if m.status == "active"]

        if len(actives) == 0:
            latest = max(members, key=lambda m: _version_number(m.version))
            if latest.status == "withdrawn":
                return None  # MAK-0005 §A6: withdrawn by a resolution
            return IntegrityError(
                lineage_id=lineage_id,
                kind="zero_active",
                detail="lineage has no active member",
            )
        if len(actives) > 1:
            return IntegrityError(
                lineage_id=lineage_id,
                kind="multiple_active",
                detail=f"lineage has {len(actives)} active members: "
                f"{[a.artifact_id for a in actives]}",
            )

        active = actives[0]
        dangling_detail = self._find_dangling_pointer(active, members)
        if dangling_detail is not None:
            return IntegrityError(
                lineage_id=lineage_id, kind="dangling_pointer", detail=dangling_detail
            )
        return active

    @staticmethod
    def _find_dangling_pointer(active: Artifact, members: list[Artifact]) -> str | None:
        members_by_id = {m.artifact_id: m for m in members}

        if active.supersedes is not None:
            predecessor = members_by_id.get(active.supersedes)
            if predecessor is None:
                return f"active {active.artifact_id!r}.supersedes={active.supersedes!r} not found"
            if predecessor.status != "superseded":
                return f"predecessor {predecessor.artifact_id!r} is not marked superseded"
            if predecessor.superseded_by != active.artifact_id:
                return (
                    f"predecessor {predecessor.artifact_id!r}.superseded_by="
                    f"{predecessor.superseded_by!r} does not point back to "
                    f"{active.artifact_id!r}"
                )

        for member in members:
            if member.status != "superseded":
                continue
            if member.superseded_by is None:
                return f"superseded {member.artifact_id!r} has no superseded_by pointer"
            if member.superseded_by not in members_by_id:
                return (
                    f"superseded {member.artifact_id!r}.superseded_by="
                    f"{member.superseded_by!r} not found in lineage"
                )
        return None


def _version_number(version: str) -> int:
    try:
        return int(version.partition(".")[0])
    except ValueError:
        return -1
