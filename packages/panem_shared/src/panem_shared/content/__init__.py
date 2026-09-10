"""Content file schemas and loaders (Spec §5.4).

Districts, goods, jobs, routes, NPCs, events, and dialogue templates are
authored as YAML under `data/` and validated against pydantic schemas at
process startup. A file that fails validation MUST abort boot with the
file path and offending field (Spec §5.4) rather than starting in a
partially-loaded state.
"""

from panem_shared.content.errors import ContentValidationError
from panem_shared.content.loader import ContentBundle, load_content

__all__ = ["ContentBundle", "ContentValidationError", "load_content"]
