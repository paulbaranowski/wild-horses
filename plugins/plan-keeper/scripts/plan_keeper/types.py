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
from typing import Any, Literal, Optional, TypedDict

# A decoded JSON object from an HTTP boundary. The alias localizes the ``Any``
# to the raw-response layer (``http_post_json`` / ``http_get_json``); the
# transform functions narrow it into the concrete row TypedDicts below.
JsonObject = dict[str, Any]

# The closed plan-Kind vocabulary. Members mirror ``frontmatter.VALID_KINDS``
# exactly; that tuple stays the runtime source of truth (validation iterates it)
# and this Literal is the static mirror so signatures can name the closed set.
Kind = Literal["idea", "prd", "design", "spec", "exec-plan"]


class LinearDefaults(TypedDict, total=False):
    """Per-repo Linear push defaults stored under ``config['linear']['defaults']``.

    Push validates ``teamId`` before reading it and treats everything else as
    optional — applied to the create payload only when present. The ``*Name``
    fields are display labels carried alongside their ids so cache-staleness
    warnings can name what went missing. Every key is optional (``total=False``)
    because the wizard writes ``defaults`` incrementally as the user picks each
    value; read required fields through ``.get`` / post-validation.
    """

    teamId: str
    teamName: str
    projectId: str
    projectName: str
    assigneeId: str
    assigneeName: str
    labelIds: list[str]
    labelNames: list[str]


class JiraDefaults(TypedDict, total=False):
    """Per-repo Jira push defaults stored under ``config['jira']['defaults']``.

    Push validates ``projectKey`` before reading it; ``issueType`` falls back to
    ``"Task"`` when absent; the id-list fields are applied to the create payload
    only when non-empty. Every key is optional (``total=False``) for the same
    incrementally-written reason as ``LinearDefaults``.
    """

    projectKey: str
    issueType: str
    componentIds: list[str]
    componentNames: list[str]
    assigneeAccountId: str
    assigneeName: str
    labels: list[str]


class LinearSection(TypedDict, total=False):
    """The ``config['linear']`` provider section.

    ``apiKey`` is the credential; ``defaults`` drives the push payload; ``cache``
    holds the last metadata refresh (teams/projects/labels/users). All optional
    because a half-configured section (e.g. credential saved, defaults not yet
    picked) is a valid on-disk intermediate state — push validates presence
    before indexing in.
    """

    apiKey: str
    defaults: LinearDefaults
    cache: dict


class JiraSection(TypedDict, total=False):
    """The ``config['jira']`` provider section.

    ``site``/``email``/``apiToken`` are the Basic-auth credentials; ``defaults``
    drives the push payload; ``cache`` holds the last metadata refresh
    (projects/components/users/issueTypes). All optional for the same
    half-configured-is-valid reason as ``LinearSection``.
    """

    site: str
    email: str
    apiToken: str
    defaults: JiraDefaults
    cache: dict


class PlanKeeperConfig(TypedDict, total=False):
    """The per-repo ``.plankeeper.json`` document, keyed by ticket-system name.

    Each provider gets at most one section; a section is absent until that
    provider is configured for the repo. ``load_config`` returns ``{}`` (an
    empty instance) when the file is missing.
    """

    linear: LinearSection
    jira: JiraSection


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

    ``root`` is the plan's registered root name (``"default"`` on a single-root
    install), carried so the UI can disambiguate a repo that straddles two roots.
    ``status``/``agent`` are raw frontmatter values (``""`` when unset);
    ``blocked``/``blockedBy`` report dispatch-readiness.
    """

    root: str
    repo: str
    file: str
    status: str
    agent: str
    blocked: bool
    blockedBy: list[str]


class LinearTeam(TypedDict):
    """One transformed row from ``linear_teams``."""

    id: str
    name: str


class LinearProject(TypedDict):
    """One transformed row from ``linear_projects``.

    ``teamIds`` is the flattened list of team ids the project belongs to.
    """

    id: str
    name: str
    teamIds: list[str]


class LinearLabel(TypedDict):
    """One transformed row from ``linear_labels``.

    ``teamId`` is ``None`` for workspace-level (un-teamed) labels.
    """

    id: str
    name: str
    teamId: Optional[str]


class LinearUser(TypedDict):
    """One transformed row from ``linear_users``."""

    id: str
    name: str
    email: str


class LinearIssueInput(TypedDict, total=False):
    """The ``IssueCreateInput`` payload built by ``_push_linear``.

    ``title``/``description``/``teamId`` are always set on create; the rest are
    added only when the corresponding default is present, hence ``total=False``.
    """

    title: str
    description: str
    teamId: str
    projectId: str
    assigneeId: str
    labelIds: list[str]


class JiraProject(TypedDict):
    """One transformed row from ``jira_projects``.

    ``key`` is the human-readable project key; ``id`` is the numeric id that
    ``jira_issuetypes`` requires (see ``_resolve_jira_project_id``).
    """

    key: str
    id: str
    name: str


class JiraComponent(TypedDict):
    """One transformed row from ``jira_components``."""

    id: str
    name: str
    projectKey: str


class JiraUser(TypedDict):
    """One transformed row from ``jira_users``.

    ``email`` is ``""`` when the directory hides the user's email address.
    """

    accountId: str
    name: str
    email: str


class JiraIssueType(TypedDict):
    """One transformed row from ``jira_issuetypes``.

    ``projectId`` is the numeric project id the issue types were fetched for.
    """

    id: str
    name: str
    projectId: str


class JiraFields(TypedDict, total=False):
    """The ``fields`` payload built by ``_push_jira`` for create/update.

    ``project``/``summary``/``description``/``issuetype`` are set on create;
    ``components``/``assignee``/``labels`` are added only when their default is
    present. Update sends just ``summary``/``description``. ``total=False``
    because no single call path sets every key.
    """

    project: dict[str, str]
    summary: str
    description: JsonObject
    issuetype: dict[str, str]
    components: list[dict[str, str]]
    assignee: dict[str, str]
    labels: list[str]
