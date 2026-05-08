class OrcError(Exception):
    """Base class for all Orc errors."""


class WorkspaceNotFoundError(OrcError):
    pass


class WorkspaceExistsError(OrcError):
    pass


class DirectiveNotFoundError(OrcError):
    pass


class SkillNotFoundError(OrcError):
    pass


class EvidenceNotFoundError(OrcError):
    pass


class TraceNotFoundError(OrcError):
    pass


class IngestError(OrcError):
    pass
