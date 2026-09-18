"""Re-exports `panem_shared.errors` so every existing `from panem_bot.errors
import NotAllowed`-style call site keeps working unchanged -- the classes
themselves live in `panem_shared` now (see that module's docstring)."""

from __future__ import annotations

from panem_shared.errors import (
    LimitReached as LimitReached,
)
from panem_shared.errors import (
    NotAllowed as NotAllowed,
)
from panem_shared.errors import (
    NotFound as NotFound,
)
from panem_shared.errors import (
    ServiceError as ServiceError,
)
from panem_shared.errors import (
    ValidationFailed as ValidationFailed,
)
