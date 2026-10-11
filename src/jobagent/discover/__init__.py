"""Discovery layer (hunt): DiscoverySource and TenantSource implementations.

Owner: plan-general.md section 4; Workday specifics in plan-workday.md sections 6-7.
Contracts: docs/design.md 7.1 (DiscoverySource), 7.2 (TenantSource), 8.2 (cxs API).
See also: models.py (Source, Posting), store.py (upsert/freshness).
"""

from .base import BaseDiscoverySource, DiscoverySource, TenantCandidate, TenantSource

__all__ = [
    "BaseDiscoverySource",
    "DiscoverySource",
    "TenantCandidate",
    "TenantSource",
]
