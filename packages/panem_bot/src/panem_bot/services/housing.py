"""Housing: buying houses/apartment units (or a whole complex), renting a
unit, sleeping/fatigue, inn stays, financed purchases/refinancing, and
auctions.

Moved to `panem_shared.housing` (same reasoning as every other
`panem_shared` move this session); re-exported here for every existing
call site.
"""

from __future__ import annotations

from panem_shared.housing import FinancedPurchase as FinancedPurchase
from panem_shared.housing import apply_fatigue_restoration as apply_fatigue_restoration
from panem_shared.housing import apply_refinance as apply_refinance
from panem_shared.housing import check_can_bid as check_can_bid
from panem_shared.housing import check_can_buy_complex as check_can_buy_complex
from panem_shared.housing import check_can_buy_property as check_can_buy_property
from panem_shared.housing import check_can_refinance as check_can_refinance
from panem_shared.housing import check_can_rent as check_can_rent
from panem_shared.housing import check_can_sleep as check_can_sleep
from panem_shared.housing import check_can_start_auction as check_can_start_auction
from panem_shared.housing import check_owns_property as check_owns_property
from panem_shared.housing import complex_purchase_price as complex_purchase_price
from panem_shared.housing import dock_fatigue as dock_fatigue
from panem_shared.housing import fatigue_restored as fatigue_restored
from panem_shared.housing import financed_purchase_terms as financed_purchase_terms
from panem_shared.housing import has_a_bed as has_a_bed
from panem_shared.housing import max_refinance_amount as max_refinance_amount
from panem_shared.housing import property_value as property_value
from panem_shared.housing import quoted_price as quoted_price
