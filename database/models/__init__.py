"""SQLAlchemy models. Importing this package registers every table."""

from database.models.analysis import Analysis, AnalysisSource
from database.models.audit import AuditLog
from database.models.chat import ChatMessage, ChatSession
from database.models.knowledge import KnowledgeChunk, KnowledgeDocument
from database.models.report import ExtractedValue, Report, ReportPage
from database.models.share import ReportShareLink
from database.models.user import PasswordResetToken, User, UserSession

__all__ = [
    "Analysis",
    "AnalysisSource",
    "AuditLog",
    "ChatMessage",
    "ChatSession",
    "ExtractedValue",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "PasswordResetToken",
    "Report",
    "ReportPage",
    "ReportShareLink",
    "User",
    "UserSession",
]
