"""Contraband shipment heists: re-exports `panem_shared.shipments` so every
existing `shipments_svc.X(...)` call site in `panem_bot` keeps working
unchanged -- the logic itself lives in `panem_shared` because `panem_api`'s
dashboard Crime tab needs it too and can't depend on `panem_bot` to get it
(same reasoning as every other move this session)."""

from __future__ import annotations

from panem_shared.shipments import (
    ShipmentResult as ShipmentResult,
)
from panem_shared.shipments import (
    apply_shipment_outcome as apply_shipment_outcome,
)
from panem_shared.shipments import (
    check_can_steal_shipment as check_can_steal_shipment,
)
from panem_shared.shipments import (
    find_shipment_here as find_shipment_here,
)
from panem_shared.shipments import (
    resolve_shipment as resolve_shipment,
)
from panem_shared.shipments import (
    roll_and_apply_shipment as roll_and_apply_shipment,
)
from panem_shared.shipments import (
    shipment_difficulty as shipment_difficulty,
)
