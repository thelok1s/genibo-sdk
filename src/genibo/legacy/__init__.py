"""Legacy interoperability: the ``CGenibo_Bt``-compatible shim and the C# migration scanner."""

from genibo.legacy.compat import CGeniboBt
from genibo.legacy.migrate import MigrationReport, scan

__all__ = ["CGeniboBt", "MigrationReport", "scan"]
