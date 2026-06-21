"""Tests for the play-money economy service."""

from __future__ import annotations

from datetime import UTC, datetime

from badactors.platform.economy import (
    COMMUNITY_ACCOUNT,
    PLAY_MONEY_ONLY_NOTICE,
    EconomyService,
)
from badactors.schemas import Event, Phase, Role, Visibility


def test_markets_open_accept_settle() -> None:
    economy = EconomyService()
    economy.apply_event(_roles_event())

    market_ids = {market.id for market in economy.markets_for("economy-game")}
    assert {"winner", "traitor:p1", "traitor:p2"} <= market_ids

    economy.place_position("economy-game", "viewer-1", "winner", "TRAITOR", 100)
    economy.apply_event(_game_over(Role.TRAITOR))

    assert economy.market("economy-game", "winner").open is False
    assert economy.market("economy-game", "winner").pool == 0
    assert economy.wallet_balance("viewer-1") == 1_000


def test_ledger_conserved_through_settlement() -> None:
    economy = EconomyService()
    economy.apply_event(_roles_event())
    economy.place_position("economy-game", "viewer-1", "winner", "FAITHFUL", 125)
    economy.place_position("economy-game", "viewer-2", "winner", "TRAITOR", 75)
    economy.contribute_to_pot("economy-game", "viewer-3", 50)
    before_settlement = economy.total_ledger_value()

    economy.apply_event(_game_over(Role.FAITHFUL))

    assert economy.total_ledger_value() == before_settlement
    assert economy.community_pot("economy-game") == 0


def test_traitor_win_routes_pot_to_traitors() -> None:
    economy = EconomyService()
    economy.apply_event(_roles_event())
    traitor_before = economy.wallet_balance("p1")

    economy.contribute_to_pot("economy-game", "viewer-1", 200)
    economy.apply_event(_game_over(Role.TRAITOR))

    assert economy.wallet_balance("p1") == traitor_before + 200
    assert economy.wallet_balance(COMMUNITY_ACCOUNT) == 1_000


def test_faithful_win_settles_faithful_markets() -> None:
    economy = EconomyService()
    economy.apply_event(_roles_event())
    economy.place_position("economy-game", "viewer-1", "winner", "FAITHFUL", 100)
    economy.place_position("economy-game", "viewer-2", "winner", "TRAITOR", 100)

    economy.apply_event(_game_over(Role.FAITHFUL))

    assert economy.wallet_balance("viewer-1") == 1_100
    assert economy.wallet_balance("viewer-2") == 900


def test_no_real_money_paths() -> None:
    economy = EconomyService()

    assert economy.accepts_real_money() is False
    assert economy.real_money_enabled is False
    assert "play-money only" in PLAY_MONEY_ONLY_NOTICE
    assert "Real money" in PLAY_MONEY_ONLY_NOTICE
    assert "gambling regulation" in PLAY_MONEY_ONLY_NOTICE
    assert "out of scope" in PLAY_MONEY_ONLY_NOTICE


def _roles_event() -> Event:
    return _event(
        1,
        "RolesAssigned",
        {
            "roles": {"p1": "TRAITOR", "p2": "FAITHFUL"},
            "traitor_ids": ["p1"],
        },
        visibility=Visibility.AUDIENCE,
    )


def _game_over(winning_role: Role) -> Event:
    return _event(
        2,
        "GameOver",
        {"winning_role": winning_role.value, "reason": "economy fixture"},
        phase=Phase.GAME_OVER,
    )


def _event(
    event_id: int,
    event_type: str,
    payload: dict[str, object],
    *,
    visibility: Visibility = Visibility.PUBLIC,
    phase: Phase = Phase.LOBBY,
) -> Event:
    return Event(
        id=event_id,
        game_id="economy-game",
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        phase=phase,
        type=event_type,
        payload=payload,
        visibility=visibility,
    )
