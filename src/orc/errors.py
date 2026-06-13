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


class EmbeddingsUnavailableError(OrcError):
    """Embeddings were requested but the optional dependencies are missing."""


class UnknownDomainError(OrcError):
    """Raised when a caller passes a domain that is neither a product domain
    nor a benchmark source alias (see orc.directives.research.routing)."""
