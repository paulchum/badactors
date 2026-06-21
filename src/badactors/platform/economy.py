"""Play-money markets and community pot settlement."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from badactors.schemas import Event, Role

STARTING_BALANCE = 1_000
COMMUNITY_ACCOUNT = "community"
PLAY_MONEY_ONLY_NOTICE = (
    "Bad Actors economy uses play-money only. Real money would trigger "
    "gambling regulation and is out of scope."
)

type MarketKind = Literal["winner", "traitor"]


class EconomyError(ValueError):
    pass


@dataclass(frozen=True)
class Position:
    bettor_id: str
    outcome: str
    amount: int


@dataclass(frozen=True)
class LedgerEntry:
    account_id: str
    amount: int
    reason: str
    game_id: str | None = None
    market_id: str | None = None


@dataclass
class Market:
    id: str
    kind: MarketKind
    outcomes: tuple[str, ...]
    player_id: str | None = None
    open: bool = True
    positions: list[Position] = field(default_factory=list)
    pool: int = 0


@dataclass
class GameEconomy:
    game_id: str
    players: set[str] = field(default_factory=set)
    roles: dict[str, Role] = field(default_factory=dict)
    markets: dict[str, Market] = field(default_factory=dict)
    community_pot: int = 0
    settled: bool = False


class EconomyService:
    real_money_enabled = False

    def __init__(self, *, starting_balance: int = STARTING_BALANCE) -> None:
        self._starting_balance = starting_balance
        self._wallets: dict[str, int] = {}
        self._games: dict[str, GameEconomy] = {}
        self.ledger: list[LedgerEntry] = []

    def accepts_real_money(self) -> bool:
        return False

    def apply_event(self, event: Event) -> None:
        payload = event.payload
        if event.type in {"RolesAssigned", "CastingAnnouncement"}:
            self.open_game(
                event.game_id,
                players=_players_from_payload(payload),
                roles=_roles_from_payload(payload),
            )

        game = self._games.get(event.game_id)
        if game is not None:
            self._update_roles_from_event(game, event)

        if event.type in {"GameOver", "GameEnded"}:
            winning_role = _role(payload.get("winning_role") or payload.get("winner"))
            if winning_role is not None:
                self.settle_game(event.game_id, winning_role)

    def consume(self, events: Sequence[Event]) -> None:
        for event in events:
            self.apply_event(event)

    def open_game(
        self,
        game_id: str,
        *,
        players: Iterable[str] = (),
        roles: Mapping[str, Role] | None = None,
    ) -> GameEconomy:
        game = self._games.setdefault(game_id, GameEconomy(game_id=game_id))
        if game.settled:
            raise EconomyError("Cannot reopen a settled game.")

        game.players.update(players)
        if roles is not None:
            game.roles.update(roles)
            game.players.update(roles)

        game.markets.setdefault(
            "winner",
            Market(
                id="winner",
                kind="winner",
                outcomes=(Role.FAITHFUL.value, Role.TRAITOR.value),
            ),
        )
        for player_id in sorted(game.players):
            game.markets.setdefault(
                f"traitor:{player_id}",
                Market(
                    id=f"traitor:{player_id}",
                    kind="traitor",
                    player_id=player_id,
                    outcomes=("yes", "no"),
                ),
            )
        return game

    def place_position(
        self,
        game_id: str,
        bettor_id: str,
        market_id: str,
        outcome: str,
        amount: int,
    ) -> Position:
        self._require_positive(amount)
        game = self._game(game_id)
        market = self._market(game, market_id)
        if not market.open:
            raise EconomyError("Market is closed.")
        if outcome not in market.outcomes:
            raise EconomyError("Outcome is not available for this market.")

        self._debit(bettor_id, amount, "position", game_id, market_id)
        position = Position(bettor_id=bettor_id, outcome=outcome, amount=amount)
        market.positions.append(position)
        market.pool += amount
        return position

    def contribute_to_pot(
        self,
        game_id: str,
        contributor_id: str,
        amount: int,
    ) -> None:
        self._require_positive(amount)
        game = self._game(game_id)
        if game.settled:
            raise EconomyError("Game is already settled.")
        self._debit(contributor_id, amount, "community_pot", game_id, None)
        game.community_pot += amount

    def settle_game(self, game_id: str, winning_role: Role) -> None:
        game = self._game(game_id)
        if game.settled:
            return

        for market_id in sorted(game.markets):
            market = game.markets[market_id]
            market.open = False
            self._settle_market(game, market, winning_role)

        self._settle_pot(game, winning_role)
        game.settled = True

    def wallet_balance(self, account_id: str) -> int:
        return self._ensure_wallet(account_id)

    def market(self, game_id: str, market_id: str) -> Market:
        return self._market(self._game(game_id), market_id)

    def markets_for(self, game_id: str) -> tuple[Market, ...]:
        game = self._game(game_id)
        return tuple(game.markets[market_id] for market_id in sorted(game.markets))

    def community_pot(self, game_id: str) -> int:
        return self._game(game_id).community_pot

    def total_ledger_value(self) -> int:
        market_pools = sum(
            market.pool
            for game in self._games.values()
            for market in game.markets.values()
        )
        pots = sum(game.community_pot for game in self._games.values())
        return sum(self._wallets.values()) + market_pools + pots

    def _settle_market(
        self,
        game: GameEconomy,
        market: Market,
        winning_role: Role,
    ) -> None:
        if market.pool == 0:
            return

        winning_outcome = self._winning_outcome(game, market, winning_role)
        winners = [
            position for position in market.positions if position.outcome == winning_outcome
        ]
        if not winners:
            self._credit(
                COMMUNITY_ACCOUNT,
                market.pool,
                "unclaimed_market",
                game.game_id,
                market.id,
            )
            market.pool = 0
            return

        winner_stake = sum(position.amount for position in winners)
        payouts = _proportional_payouts(winners, market.pool, winner_stake)
        for position, payout in zip(winners, payouts, strict=True):
            self._credit(
                position.bettor_id,
                payout,
                "market_settlement",
                game.game_id,
                market.id,
            )
        market.pool = 0

    def _settle_pot(self, game: GameEconomy, winning_role: Role) -> None:
        if game.community_pot == 0:
            return

        if winning_role is Role.TRAITOR:
            recipients = sorted(
                player_id
                for player_id, role in game.roles.items()
                if role is Role.TRAITOR
            )
        else:
            recipients = [COMMUNITY_ACCOUNT]

        if not recipients:
            recipients = [COMMUNITY_ACCOUNT]

        payouts = _split_evenly(game.community_pot, recipients)
        for recipient, payout in zip(recipients, payouts, strict=True):
            self._credit(recipient, payout, "community_pot_settlement", game.game_id)
        game.community_pot = 0

    def _winning_outcome(
        self,
        game: GameEconomy,
        market: Market,
        winning_role: Role,
    ) -> str:
        if market.kind == "winner":
            return winning_role.value
        if market.player_id is None:
            raise EconomyError("Traitor market has no player.")
        return "yes" if game.roles.get(market.player_id) is Role.TRAITOR else "no"

    def _update_roles_from_event(self, game: GameEconomy, event: Event) -> None:
        game.roles.update(_roles_from_payload(event.payload))
        if event.type == "MurderNomination":
            traitor_id = _string(event.payload.get("traitor_id"))
            if traitor_id is not None:
                game.roles[traitor_id] = Role.TRAITOR
                game.players.add(traitor_id)

        if event.type in {"PlayerBanished", "PlayerMurdered"}:
            player_id = _string(event.payload.get("player_id"))
            role = _role(event.payload.get("role"))
            if player_id is not None and role is not None:
                game.roles[player_id] = role
                game.players.add(player_id)

    def _game(self, game_id: str) -> GameEconomy:
        try:
            return self._games[game_id]
        except KeyError as exc:
            raise EconomyError("Game has no economy markets.") from exc

    def _market(self, game: GameEconomy, market_id: str) -> Market:
        try:
            return game.markets[market_id]
        except KeyError as exc:
            raise EconomyError("Market does not exist.") from exc

    def _ensure_wallet(self, account_id: str) -> int:
        if account_id not in self._wallets:
            self._wallets[account_id] = self._starting_balance
            self.ledger.append(
                LedgerEntry(account_id=account_id, amount=self._starting_balance, reason="mint")
            )
        return self._wallets[account_id]

    def _debit(
        self,
        account_id: str,
        amount: int,
        reason: str,
        game_id: str | None,
        market_id: str | None,
    ) -> None:
        balance = self._ensure_wallet(account_id)
        if balance < amount:
            raise EconomyError("Insufficient play-money balance.")
        self._wallets[account_id] = balance - amount
        self.ledger.append(
            LedgerEntry(
                account_id=account_id,
                amount=-amount,
                reason=reason,
                game_id=game_id,
                market_id=market_id,
            )
        )

    def _credit(
        self,
        account_id: str,
        amount: int,
        reason: str,
        game_id: str | None,
        market_id: str | None = None,
    ) -> None:
        self._wallets.setdefault(account_id, 0)
        self._wallets[account_id] += amount
        self.ledger.append(
            LedgerEntry(
                account_id=account_id,
                amount=amount,
                reason=reason,
                game_id=game_id,
                market_id=market_id,
            )
        )

    def _require_positive(self, amount: int) -> None:
        if amount <= 0:
            raise EconomyError("Amount must be positive play-money.")


def _proportional_payouts(
    winners: Sequence[Position],
    pool: int,
    winner_stake: int,
) -> list[int]:
    payouts = [(pool * position.amount) // winner_stake for position in winners]
    remainder = pool - sum(payouts)
    for index in range(remainder):
        payouts[index] += 1
    return payouts


def _split_evenly(amount: int, recipients: Sequence[str]) -> list[int]:
    base = amount // len(recipients)
    payouts = [base for _ in recipients]
    for index in range(amount - sum(payouts)):
        payouts[index] += 1
    return payouts


def _players_from_payload(payload: Mapping[str, object]) -> set[str]:
    players: set[str] = set()
    listed_players = payload.get("players")
    if isinstance(listed_players, Iterable) and not isinstance(
        listed_players,
        str | bytes,
    ):
        for value in listed_players:
            player_id = _string(value)
            if player_id is not None:
                players.add(player_id)

    raw_roles = payload.get("roles")
    if isinstance(raw_roles, Mapping):
        for player_id in raw_roles:
            player = _string(player_id)
            if player is not None:
                players.add(player)
    return players


def _roles_from_payload(payload: Mapping[str, object]) -> dict[str, Role]:
    roles: dict[str, Role] = {}
    raw_roles = payload.get("roles")
    if isinstance(raw_roles, Mapping):
        for player_id, value in raw_roles.items():
            player = _string(player_id)
            role = _role(value)
            if player is not None and role is not None:
                roles[player] = role

    traitor_ids = payload.get("traitor_ids")
    if isinstance(traitor_ids, Iterable) and not isinstance(traitor_ids, str | bytes):
        for value in traitor_ids:
            traitor_id = _string(value)
            if traitor_id is not None:
                roles[traitor_id] = Role.TRAITOR
    return roles


def _role(value: object) -> Role | None:
    if isinstance(value, Role):
        return value
    if value == Role.FAITHFUL.value:
        return Role.FAITHFUL
    if value == Role.TRAITOR.value:
        return Role.TRAITOR
    return None


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
