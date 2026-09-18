"""Character lifecycle: re-exports `panem_shared.characters` so every
existing `characters_svc.X(...)` call site in `panem_bot` keeps working
unchanged -- the logic itself moved to `panem_shared` because
`panem_api`'s dashboard character endpoints need it too and can't depend
on `panem_bot` to get it (same reasoning as `panem_shared.jail`/
`panem_shared.stealing`)."""

from __future__ import annotations

from panem_shared.characters import (
    approve_character as approve_character,
)
from panem_shared.characters import (
    create_character as create_character,
)
from panem_shared.characters import (
    effective_max_characters as effective_max_characters,
)
from panem_shared.characters import (
    ensure_name_available as ensure_name_available,
)
from panem_shared.characters import (
    get_character as get_character,
)
from panem_shared.characters import (
    get_or_create_user as get_or_create_user,
)
from panem_shared.characters import (
    is_frozen as is_frozen,
)
from panem_shared.characters import (
    max_age_for_district as max_age_for_district,
)
from panem_shared.characters import (
    reject_character as reject_character,
)
from panem_shared.characters import (
    request_changes as request_changes,
)
from panem_shared.characters import (
    retire_character as retire_character,
)
from panem_shared.characters import (
    validate_avatar_url as validate_avatar_url,
)
from panem_shared.characters import (
    validate_character_fields as validate_character_fields,
)
from panem_shared.characters import (
    validate_job_title as validate_job_title,
)
from panem_shared.characters import (
    validate_proxy_tag as validate_proxy_tag,
)
