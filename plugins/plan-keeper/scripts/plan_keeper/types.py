"""Typed shapes for the groundcrew/queue JSON structures.

Pure typing — these ``TypedDict``s describe the dict literals that
``groundcrew.py`` and ``cli.py`` already build and emit; they add no runtime
behavior. Annotating producers and consumers with them lets a reader (or
pyright) trace which keys each dict carries without re-reading every call site.

The ``CrewIssue`` shape mirrors groundcrew's ``shellIssueSchema`` (a required
key per field, ``agent`` nullable). ``IndexEntry`` is plan-keeper's internal
per-repo plan index entry. ``QueueRow`` is one row of ``crew queue list``'s
JSON array.
"""
from typing import Optional, TypedDict


class Blocker(TypedDict):
    """One denormalized prerequisite snapshot embedded in a ``CrewIssue``.

    ``status`` is always a groundcrew-canonical value so the shell-source schema
    accepts it (see ``_GROUNDCREW_STATUS_MAP``).
    """

    id: str
    title: str
    status: str


class SourceRef(TypedDict):
    """Back-pointer from an issue to the plan file it was built from."""

    path: str


class CrewIssue(TypedDict):
    """One plan rendered as a groundcrew shell-adapter issue dict.

    Matches groundcrew's ``shellIssueSchema``: every key is required and
    ``agent`` is nullable (``None`` = unassigned, never a fabricated default).
    """

    id: str
    title: str
    description: str
    status: str
    repository: str
    agent: Optional[str]
    assignee: str
    updatedAt: str
    blockers: list[Blocker]
    hasMoreBlockers: bool
    sourceRef: SourceRef


class IndexEntry(TypedDict):
    """One entry in a repo's plan index (see ``_build_repo_index``).

    A single entry is keyed under every id a plan carries; ``key`` is the
    canonical plan-keeper id used by cycle detection and as the snapshot id.
    """

    id: str
    title: str
    status: str
    location: str
    key: str
    blocked_by: list[str]


class QueueRow(TypedDict):
    """One row of ``crew queue list``'s JSON array, for the plan-crew UI.

    ``status``/``agent`` are raw frontmatter values (``""`` when unset);
    ``blocked``/``blockedBy`` report dispatch-readiness.
    """

    repo: str
    file: str
    status: str
    agent: str
    blocked: bool
    blockedBy: list[str]
