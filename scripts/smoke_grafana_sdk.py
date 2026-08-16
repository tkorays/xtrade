"""End-to-end smoke test: build a dashboard with the Foundation SDK and
push it to the user's real Grafana 13 via the xtrade SDK.

Creates a unique throwaway dashboard, mutates a panel via a typed
panel builder, verifies, and deletes. Prints each step. Non-zero exit
on any failure.

Run via::

    uv run python scripts/smoke_grafana_sdk.py
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xtrade.core.config import get_config
from xtrade.core.grafana import (
    DashboardBuilder,
    GrafanaAPIError,
    GrafanaClient,
    StatPanelBuilder,
    TimeseriesPanelBuilder,
)
from xtrade.core.grafana.builder import build_envelope


def _poll_list(client: GrafanaClient, uid: str, *, tries: int = 5, delay: float = 0.4) -> int:
    for _ in range(tries):
        rows = [d for d in client.dashboards.list() if d.uid == uid]
        if rows:
            return len(rows)
        time.sleep(delay)
    return 0


def main() -> int:
    client = GrafanaClient(get_config().grafana)
    suffix = uuid.uuid4().hex[:16]
    name = f"xtrade-sdk-{suffix}"
    print(f"using throwaway name={name!r}")

    # ---- 1. Build the dashboard via Foundation SDK + xtrade wrappers. -----
    builder = (
        DashboardBuilder(title="xtrade SDK smoke", tags=["xtrade-sdk-smoke"])
        .with_panel(
            TimeseriesPanelBuilder(title="Net PnL")
        )
        .with_panel(
            StatPanelBuilder(title="Trades today")
        )
    )
    envelope = build_envelope(builder, name=name, message="init")

    try:
        # 2. CREATE
        print("[1/4] POST .../dashboards (create via Foundation SDK builder)")
        created = client.dashboards.create(envelope, message="init")
        real_uid = created.uid
        print(
            f"  ok  uid={real_uid!r}  name={created.name!r}  "
            f"panels_in_response={len(created.spec.get('panels', []))}"
        )

        # 3. LIST - find ours (poll for read-after-write consistency)
        print("[2/4] GET .../dashboards?limit=200  (poll for read-after-write)")
        found = _poll_list(client, real_uid)
        assert found == 1, f"expected 1 hit, got {found}"
        print(f"  ok  found uid={real_uid!r}")

        # 4. UPDATE PANEL via typed builder
        print("[3/4] panels.update_panel() via TimeseriesPanelBuilder")
        client.panels.update_panel(
            created.name,
            panel_id=1,
            panel_builder=TimeseriesPanelBuilder(title="Net PnL (renamed)"),
            message="rename via SDK",
        )
        # Verify
        fetched = client.dashboards.get(created.name)
        panel = next(p for p in fetched.spec["panels"] if p["id"] == 1)
        assert panel["title"] == "Net PnL (renamed)", panel
        print(f"  ok  panel[1].title={panel['title']!r}")

        # 5. DELETE
        print("[4/4] DELETE .../dashboards")
        result = client.dashboards.delete(created.name)
        assert result is None
        print("  ok  dashboard removed")

        print("ALL SMOKE STEPS PASSED")
        return 0
    except GrafanaAPIError as exc:
        print(
            f"GRAFANA API ERROR  status={exc.status_code} url={exc.url} body={exc.body}",
            file=sys.stderr,
        )
        return 2
    except AssertionError as exc:
        print(f"ASSERTION FAILED  {exc!r}", file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001
        print(f"UNEXPECTED ERROR  type={type(exc).__name__}  {exc!r}", file=sys.stderr)
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())