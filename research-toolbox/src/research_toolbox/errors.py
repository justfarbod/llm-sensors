class ExportValidationError(ValueError):
    """Raised when an export cannot be safely interpreted."""


class UnsupportedSchemaVersionError(ExportValidationError):
    """Raised when the export's schema major version is unsupported."""
