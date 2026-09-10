from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from mak4i.models import Artifact
from mak4i.resolution.types import Conflict, IntegrityError, ResolutionResult


class Resolver:
    """Applicability, integrity, and cross-lineage conflict resolution.

    [PROTOCOL] mechanism per MVP_ARCHITECTURE.md §7: applicability → group
    by lineage_id → integrity check (fail closed, §5) → cross-lineage
    conflict check (§18) → resolved/conflict/integrity-error outcome,
    every step captured in a trace.

    Deliberately store-independent and stateless: it operates on whatever
    artifact list it is given (typically `store.all(project)`, supplied by
    the engine) so it stays trivially testable and carries no
    scenario-specific logic — a caching lineage and a database lineage
    are grouped, integrity-checked, and conflict-checked identically.
    """

    def resolve(
        self,
        all_project_artifacts: Iterable[Artifact],
        artifact_type: str | None = None,
        tags: list[str] | None = None,
    ) -> ResolutionResult:
        trace: list[str] = []
        integrity_errors: list[IntegrityError] = []

        lineages = self._group_by_lineage(all_project_artifacts)
        trace.append(
            f"considered {sum(len(m) for m in lineages.values())} artifact(s) "
            f"across {len(lineages)} lineage(s)"
        )

        current_by_lineage: dict[str, Artifact] = {}
        for lineage_id, members in lineages.items():
            outcome = self._resolve_lineage(lineage_id, members)
            if isinstance(outcome, IntegrityError):
                integrity_errors.append(outcome)
                trace.append(
                    f"lineage {lineage_id!r}: integrity error ({outcome.kind}) — "
                    f"{outcome.detail}; contributes nothing"
                )
            else:
                current_by_lineage[lineage_id] = outcome
                trace.append(f"lineage {lineage_id!r}: current is {outcome.artifact_id!r}")

        applicable = {
            lineage_id: artifact
            for lineage_id, artifact in current_by_lineage.items()
            if self._matches(artifact, artifact_type, tags)
        }
        trace.append(
            f"{len(applicable)} lineage(s) applicable to "
            f"artifact_type={artifact_type!r} tags={tags!r}"
        )

        conflicts, resolved_by_lineage = self._group_conflicts(applicable)
        for conflict in conflicts:
            trace.append(
                f"conflict: lineages {conflict.lineage_ids} share "
                f"artifact_type={conflict.artifact_type!r} with overlapping tags"
            )

        resolved = list(resolved_by_lineage.values())
        trace.append(f"resolved {len(resolved)} artifact(s), {len(conflicts)} conflict(s)")

        return ResolutionResult(
            resolved=resolved,
            conflicts=conflicts,
            integrity_errors=integrity_errors,
            trace=trace,
        )

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
    ) -> Artifact | IntegrityError:
        actives = [m for m in members if m.status == "active"]

        if len(actives) == 0:
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

    @staticmethod
    def _group_conflicts(
        applicable: dict[str, Artifact],
    ) -> tuple[list[Conflict], dict[str, Artifact]]:
        """Cross-lineage conflict grouping.

        [MVP CHOICE — flagged, MVP_ARCHITECTURE.md §7/§24]: same
        artifact_type + tag overlap, no lineage link. The full MAK-0004
        conflict-grouping text has never been provided; this is this
        design's own reading, applied uniformly regardless of technology.

        Overlap requires at least 2 shared tags, not just 1: every
        `architecture_decision` in this MVP is expected to carry a broad,
        shared category tag (e.g. "architecture"), so a 1-tag-overlap rule
        would make every decision in a project conflict with every other
        one — breaking the ordinary case of two unrelated current decisions
        (a caching choice and a database choice) coexisting. Requiring 2+
        shared tags still catches the intended case (two technologies
        competing for the same narrow slot, e.g. Redis vs. Memcached, which
        share both a category tag and a role tag) without that false
        positive, and remains free of any technology-specific tag names.
        """
        items = list(applicable.items())
        parent = list(range(len(items)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i: int, j: int) -> None:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj

        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                _, a = items[i]
                _, b = items[j]
                shared_tags = set(a.tags) & set(b.tags)
                if a.artifact_type == b.artifact_type and len(shared_tags) >= 2:
                    union(i, j)

        groups: dict[int, list[int]] = defaultdict(list)
        for i in range(len(items)):
            groups[find(i)].append(i)

        conflicts: list[Conflict] = []
        resolved: dict[str, Artifact] = {}
        for indices in groups.values():
            if len(indices) > 1:
                group = [items[i] for i in indices]
                conflicts.append(
                    Conflict(
                        artifact_type=group[0][1].artifact_type,
                        lineage_ids=[lineage_id for lineage_id, _ in group],
                        artifacts=[artifact for _, artifact in group],
                    )
                )
            else:
                lineage_id, artifact = items[indices[0]]
                resolved[lineage_id] = artifact

        return conflicts, resolved
