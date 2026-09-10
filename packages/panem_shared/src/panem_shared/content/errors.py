from __future__ import annotations


class ContentValidationError(Exception):
    """Raised when a content YAML file fails schema validation.

    Carries the offending file path so callers (loader, boot sequence,
    `/staff reload content`) can report exactly where the problem is
    (Spec §5.4, NFR-12) instead of a bare pydantic traceback.
    """

    def __init__(self, file_path: str, detail: str) -> None:
        self.file_path = file_path
        self.detail = detail
        super().__init__(f"{file_path}: {detail}")
