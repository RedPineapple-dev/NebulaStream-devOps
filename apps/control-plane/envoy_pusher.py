"""Envoy weight-push integration for NebulaStream.

Supports two modes controlled by ``ENVOY_MODE`` env var:

  ``admin``  — POST to Envoy's admin API (default; works with static config).
  ``xds``    — Placeholder for future xDS/ADS gRPC management-server integration.
  ``off``    — Feature flag disabled; logs but does nothing.

Environment variables:
  ENVOY_ENABLED        — ``true`` / ``false`` (default: ``false``)
  ENVOY_ADMIN_URL      — Admin URL, e.g. ``http://envoy:9901``
  ENVOY_MODE           — ``admin`` | ``xds`` | ``off``
  ENVOY_CLUSTER_NAME   — Cluster name to update (default: ``nebula_workers``)
"""

from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger("control_plane.envoy_pusher")

ENVOY_ENABLED = os.getenv("ENVOY_ENABLED", "false").lower() == "true"
ENVOY_ADMIN_URL = os.getenv("ENVOY_ADMIN_URL", "http://envoy:9901")
ENVOY_MODE = os.getenv("ENVOY_MODE", "admin")
ENVOY_CLUSTER_NAME = os.getenv("ENVOY_CLUSTER_NAME", "nebula_workers")


async def push_weights_to_envoy(weights: dict[str, int]) -> bool:
    """Push new traffic weights to the Envoy edge proxy.

    Returns True on success, False on error or if the feature is disabled.
    This call is fire-and-forget; it should never block the control loop.

    In ``admin`` mode we POST a JSON body that Envoy's admin runtime endpoint
    accepts to modify runtime flags — in a real xDS setup this would instead
    call ``StreamAggregatedResources`` with a ``ClusterLoadAssignment`` proto.

    For the xDS upgrade path, replace the body of the ``"xds"`` branch with
    a gRPC call to an ADS management server (e.g. go-control-plane).
    """
    if not ENVOY_ENABLED:
        log.debug("envoy_push_skipped", reason="ENVOY_ENABLED=false", weights=weights)
        return False

    log.info("envoy_push_start", mode=ENVOY_MODE, weights=weights)

    if ENVOY_MODE == "admin":
        return await _push_via_admin(weights)
    elif ENVOY_MODE == "xds":
        return await _push_via_xds(weights)
    else:
        log.warning("envoy_push_unknown_mode", mode=ENVOY_MODE)
        return False


async def _push_via_admin(weights: dict[str, int]) -> bool:
    """POST weight runtime parameters to Envoy admin API.

    Envoy admin API: ``POST /runtime_modify?<key>=<value>``
    We map region weights to runtime keys so Envoy's weighted cluster
    upstream_cx_http1_1_* can reference them.

    In a fully wired setup this would update the load-assignment via REST/xDS.
    This implementation is a realistic stub that can be swapped for xDS calls.
    """
    try:
        params = {f"nebula.weight.{region}": str(w) for region, w in weights.items()}
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(f"{ENVOY_ADMIN_URL}/runtime_modify", params=params)
            if r.status_code in (200, 202):
                log.info("envoy_push_success", mode="admin", weights=weights)
                return True
            log.warning(
                "envoy_push_failed",
                status=r.status_code,
                body=r.text[:200],
            )
            return False
    except Exception as exc:
        log.warning("envoy_push_error", error=str(exc)[:200])
        return False


async def _push_via_xds(weights: dict[str, int]) -> bool:
    """Placeholder for full xDS/ADS gRPC weight push.

    To implement:
      1. Import ``envoy.service.discovery.v3`` protobuf stubs.
      2. Build a ``ClusterLoadAssignment`` for ``ENVOY_CLUSTER_NAME`` where each
         endpoint's ``load_balancing_weight`` corresponds to the region weight.
      3. Stream the update via ``AggregatedDiscoveryService.StreamAggregatedResources``.

    This requires a gRPC xDS management server (e.g. Nebula acting as one,
    or a sidecar like go-control-plane) listening on ``ENVOY_ADS_ADDRESS``.
    """
    log.info(
        "envoy_xds_stub",
        weights=weights,
        note="xDS push not yet implemented — wire in gRPC ADS client here",
    )
    # TODO: implement xDS ClusterLoadAssignment push
    return False
