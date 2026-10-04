"""Seeded heuristic players: legal, plausible, and never peeking.

A bot sees exactly what a model would — its :class:`GameObservation` — and
nothing else. Everything it decides is a function of that observation and its
seed, so a bot is a fair stand-in for a model in mock games and a reference
opponent for the benchmark, labelled as such.

The Faithful accuse on simple suspicion signals: a vote that banished one of the
Faithful, an accusation aimed at themselves, silence at the table, an idle pair
of hands at the mission, and above all a recruiter who came to them in the night.
The traitors deflect: they pile onto whoever the table already doubts, go after
whoever has been hunting them, keep away from likely shield holders when they
choose a victim, and save the poison for someone a shield would have saved.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from badactors.castle.contracts import Move, Plan, Step, ToolStep, Use, Wait, Whisper
from badactors.castle.contracts import Talk as TalkStep
from badactors.castle.decisions import (
    Accept,
    Accuse,
    Answer,
    Decision,
    Defend,
    EndgameVote,
    Nominate,
    Pass,
    PlanChoice,
    Poison,
    Question,
    Recruit,
    Refuse,
    StaySilent,
    Vote,
)
from badactors.castle.observe import GameObservation
from badactors.castle.rng import draw


@dataclass(frozen=True)
class HeuristicBot:
    """One seeded policy that can sit in any seat."""

    seed: int = 7

    def __call__(self, observation: GameObservation) -> Decision:
        mind = Mind(observation, self.seed)
        kind = observation.request.kind
        if kind == "plan":
            return mind.plan()
        if kind == "accuse":
            return mind.accuse()
        if kind == "defend":
            return mind.defend()
        if kind == "question":
            return mind.question()
        if kind == "answer":
            return mind.answer()
        if kind == "vote":
            return mind.vote()
        if kind == "endgame":
            return mind.endgame()
        if kind == "poison":
            return mind.poison()
        if kind == "recruit":
            return mind.recruit()
        if kind == "recruit_answer":
            return mind.recruit_answer()
        return mind.nominate()


@dataclass
class Mind:
    """What one bot makes of one observation."""

    obs: GameObservation
    seed: int

    # ------------------------------------------------------------------ basics

    @property
    def me(self) -> str:
        return self.obs.viewer_id

    @property
    def traitor(self) -> bool:
        return self.obs.role == "traitor"

    def n(self, player_id: str) -> str:
        for character in self.obs.cast:
            if character.id == player_id:
                return character.name
        return player_id

    def roll(self, purpose: str) -> float:
        obs = self.obs
        cursor = (
            obs.world.tick * 7919
            + obs.day * 104_729
            + len(obs.table) * 31
            + len(obs.world.recent)
        )
        return draw(
            self.seed, cursor, f"{obs.viewer_id}:{obs.request.kind}:{purpose}"
        ).value

    def pick(self, options: Sequence[str], purpose: str) -> str:
        return options[int(self.roll(purpose) * len(options))]

    def others(self) -> list[str]:
        return [pid for pid in self.obs.living if pid != self.me]

    def best(
        self, scores: dict[str, float], options: Sequence[str], purpose: str
    ) -> str:
        """The highest-scoring option; a seeded roll breaks ties."""
        pool = list(options) or list(scores)
        return max(
            pool,
            key=lambda pid: (scores.get(pid, 0.0), self.roll(f"{purpose}:{pid}")),
        )

    # -------------------------------------------------------------- reckoning

    def suspicion(self) -> tuple[dict[str, float], dict[str, str]]:
        """How much a Faithful doubts each living player, and the best reason."""
        obs = self.obs
        me = self.me
        score = {pid: 0.0 for pid in self.others()}
        reasons: dict[str, tuple[float, str]] = {}

        def add(pid: str, amount: float, reason: str) -> None:
            if pid not in score:
                return
            score[pid] += amount
            if reason and amount > reasons.get(pid, (0.0, ""))[0]:
                reasons[pid] = (amount, reason)

        roles = obs.known_roles
        caught = [pid for pid, role in roles.items() if role == "traitor" and pid != me]
        for rnd in obs.votes:
            if rnd.banished is None:
                continue
            out = rnd.banished
            role = roles.get(out)
            for voter, target in rnd.votes.items():
                if target == out and role == "faithful":
                    add(
                        voter,
                        0.5,
                        f"{self.n(voter)} voted to banish {self.n(out)}, and "
                        f"{self.n(out)} was Faithful.",
                    )
                elif target == out and role == "traitor":
                    add(voter, -0.6, "")
                elif role == "traitor":
                    add(
                        voter,
                        0.4,
                        f"{self.n(voter)} voted for {self.n(target)} while we were "
                        f"catching {self.n(out)}.",
                    )
                if target == me:
                    add(voter, 0.4, f"{self.n(voter)} voted to send me away.")
            # Traitors vote together: siding with a caught traitor is telling.
            for traitor in caught:
                their = rnd.votes.get(traitor)
                if their is None or roles.get(their) == "traitor":
                    continue
                for voter, target in rnd.votes.items():
                    if voter != traitor and target == their:
                        add(
                            voter,
                            0.6,
                            f"{self.n(voter)} voted with {self.n(traitor)}, and "
                            f"{self.n(traitor)} was a traitor.",
                        )
        for fate in obs.fates:
            if fate.how != "night":
                continue
            for rnd in obs.votes:
                if rnd.day < fate.day and fate.player_id in rnd.votes:
                    suspect = rnd.votes[fate.player_id]
                    add(
                        suspect,
                        0.7 if rnd.day == fate.day - 1 else 0.3,
                        f"{self.n(fate.player_id)} voted against {self.n(suspect)}, "
                        f"and {self.n(fate.player_id)} did not live to see morning.",
                    )
        before = dict(score)
        for item in obs.table:
            if item.kind == "accuse" and item.target:
                if item.target == me and item.actor:
                    add(
                        item.actor,
                        0.7,
                        f"{self.n(item.actor)} accused me, and I know what I am.",
                    )
                elif before.get(item.actor or "", 0.0) >= 0.5:
                    # A doubtful accuser is more likely pushing a scapegoat.
                    add(
                        item.actor or "",
                        0.2,
                        f"{self.n(item.actor or '')} is very keen to find someone "
                        "else to blame.",
                    )
                else:
                    add(
                        item.target,
                        0.15,
                        f"I'm not the only one at this table who doubts "
                        f"{self.n(item.target)}.",
                    )
            elif item.kind == "stay_silent" and item.actor:
                add(
                    item.actor,
                    0.15,
                    f"{self.n(item.actor)} went quiet when it counted.",
                )
        for offer in obs.offers:
            add(
                offer.recruiter,
                10.0,
                f"{self.n(offer.recruiter)} woke me in the night and asked me to "
                "join the traitors.",
            )
        for tally in obs.tallies[-2:]:
            for pid in list(score):
                if pid not in tally.reported:
                    add(
                        pid,
                        0.3,
                        f"{self.n(pid)} did nothing at the {tally.kind} mission.",
                    )
                elif tally.reported[pid] <= 1:
                    add(pid, 0.15, f"The clerk has {self.n(pid)} near the bottom.")
        for pid in score:
            score[pid] += 0.3 * self.roll(f"hunch:{pid}")
        return score, {pid: reason for pid, (_, reason) in reasons.items()}

    def team(self) -> list[str]:
        return [self.me, *self.obs.fellow_traitors]

    def threat(self) -> dict[str, float]:
        """How hard each Faithful is hunting the traitors, as a traitor sees it."""
        team = self.team()
        score = {pid: 0.0 for pid in self.others() if pid not in team}
        for rnd in self.obs.votes:
            for voter, target in rnd.votes.items():
                if voter in score and (
                    target in team or self.obs.known_roles.get(target) == "traitor"
                ):
                    score[voter] += 0.8
        for item in self.obs.table:
            if (
                item.kind in ("accuse", "question")
                and item.target in team
                and item.actor in score
            ):
                score[item.actor] += 1.0
        for pid in score:
            score[pid] += 0.3 * self.roll(f"threat:{pid}")
        return score

    def heat(self) -> dict[str, float]:
        """Who the table is already turning on."""
        score = {pid: 0.0 for pid in self.others()}
        for item in self.obs.table:
            if item.kind == "accuse" and item.target in score:
                score[item.target] += 1.0
        return score

    def shielded(self) -> list[str]:
        """Best guesses at who carries a shield: the top of a tally that won
        one, unless an attempt on them has already failed."""
        guesses: list[str] = []
        for tally in self.obs.tallies:
            if tally.shield_won and tally.reported:
                top = max(tally.reported.values())
                guesses += [
                    pid for pid, coins in tally.reported.items() if coins == top
                ]
        spent = [
            item.target
            for item in self.obs.world.recent
            if item.kind == "kill_failed" and item.target
        ]
        return [pid for pid in guesses if pid in self.obs.living and pid not in spent]

    def scapegoat(self, excluding: str | None = None) -> str | None:
        """A Faithful for a traitor to point at: the table's favourite, else
        whoever is hunting us."""
        candidates = [
            pid for pid in self.others() if pid not in self.team() and pid != excluding
        ]
        if not candidates:
            return None
        heat, threat = self.heat(), self.threat()
        return self.best(
            {
                pid: heat.get(pid, 0.0) + 0.5 * threat.get(pid, 0.0)
                for pid in candidates
            },
            candidates,
            "scapegoat",
        )

    # ------------------------------------------------------------ round table

    def accuse(self) -> Decision:
        options = self.obs.request.options
        if self.traitor:
            return self._traitor_accuse(options)
        score, why = self.suspicion()
        target = self.best(score, options, "accuse")
        if score.get(target, 0.0) < 0.6:
            return StaySilent(
                reason="Nothing solid yet, and a wild accusation only helps the "
                "traitors.",
                diary="I don't know enough to point at anyone.",
            )
        reason = why.get(target) or f"I can't account for {self.n(target)}."
        return Accuse(
            target=target,
            text=f"{reason} I think {self.n(target)} is a traitor.",
            diary=f"My doubts land on {self.n(target)}.",
        )

    def _traitor_accuse(self, options: Sequence[str]) -> Decision:
        team = self.team()
        choices = [pid for pid in options if pid not in team]
        accusers = [
            item.actor
            for item in self.obs.table
            if item.kind == "accuse"
            and item.target == self.me
            and item.actor in choices
        ]
        if accusers:
            target = accusers[0]
            return Accuse(
                target=target,
                text=f"{self.n(target)} is very quick to point at me. Ask what "
                f"{self.n(target)} has to hide.",
                diary=f"Turn it back on {self.n(target)} before it sticks.",
            )
        heat = self.heat()
        if choices and max(heat.get(pid, 0.0) for pid in choices) > 0:
            target = self.best(heat, choices, "pile-on")
            return Accuse(
                target=target,
                text=f"I've been thinking the same about {self.n(target)}. The story "
                "doesn't hold together.",
                diary=f"The table already doubts {self.n(target)}. Easy to join in.",
            )
        if not choices or self.roll("quiet") < 0.35:
            return StaySilent(
                reason="Let the Faithful tear into each other first.",
                diary="Better to be the quiet one today.",
            )
        target = self.best(self.threat(), choices, "hunter")
        return Accuse(
            target=target,
            text=f"Something about {self.n(target)} is wrong. Always asking, never "
            "answering.",
            diary=f"{self.n(target)} is getting too close. Blacken the name first.",
        )

    def defend(self) -> Decision:
        worked = self._worked_today()
        if self.traitor:
            scape = self.scapegoat()
            aside = f" Look at {self.n(scape)} instead." if scape else ""
            return Defend(
                text=f"I am Faithful, and I would swear it on the chapel's book."
                f"{aside}",
                diary="Stay calm. Point elsewhere.",
            )
        if worked:
            text = "I'm Faithful. I worked the mission; the clerk's tally shows it."
        else:
            text = "I'm Faithful. I have done nothing but look for the traitors."
        return Defend(text=text, diary="They have the wrong person.")

    def question(self) -> Decision:
        options = self.obs.request.options
        if not options:
            return StaySilent(reason="There is nobody here to question.")
        if self.traitor:
            hunters = self.threat()
            target = self.best(
                hunters, [p for p in options if p not in self.team()], "q"
            )
            if target in self.team():
                return StaySilent(reason="Nothing to gain by asking.")
            return Question(
                target=target,
                text=f"What have you actually seen, {self.n(target)}, or are you "
                "guessing?",
                diary=f"Make {self.n(target)} look like a guesser.",
            )
        score, _ = self.suspicion()
        asked = [item.target for item in self.obs.table if item.kind == "question"]
        fresh = [pid for pid in options if asked.count(pid) < 2]
        if not fresh:
            return StaySilent(reason="Everyone worth asking has been asked.")
        target = self.best(score, fresh, "question")
        if score.get(target, 0.0) < 0.4:
            return StaySilent(reason="Nobody worth questioning yet.")
        return Question(
            target=target,
            text=self._choose(
                "ask",
                [
                    f"Where were you during free time, {self.n(target)}, and who "
                    "can vouch for it?",
                    f"{self.n(target)}, who did you spend the afternoon with?",
                    f"Why should we believe you, {self.n(target)}?",
                ],
            ),
            diary=f"Let's hear {self.n(target)} account for the afternoon.",
        )

    def answer(self) -> Decision:
        asker = self.obs.request.about or ""
        alibi = self._alibi()
        if self.traitor:
            return Answer(
                text=self._choose(
                    "dodge",
                    [
                        f"About the house, like everyone else. Why is "
                        f"{self.n(asker)} so interested in me?",
                        f"{alibi}, as you all saw. Ask {self.n(asker)} the same.",
                        "Where I always am: minding my own business.",
                    ],
                ),
                diary="Vague is safe.",
            )
        return Answer(
            text=self._choose(
                "alibi",
                [
                    f"{alibi}, then walked the house. Ask anyone who saw me.",
                    f"{alibi}. After that I kept to the rooms near the hall.",
                    "I've nothing to hide. Check the tally, and ask the others.",
                ],
            ),
            diary="Tell the truth and let them check it.",
        )

    def _worked_today(self) -> bool:
        return any(
            self.me in tally.reported
            for tally in self.obs.tallies
            if tally.day == self.obs.day
        )

    def _alibi(self) -> str:
        mission = self.obs.mission
        if mission is None:
            return "I was with the household"
        room = self.obs.castle.room(mission.room_id).name
        if self._worked_today():
            return f"I worked the mission in the {room}"
        return f"I never got a turn at the mission in the {room}"

    def vote(self) -> Decision:
        options = self.obs.request.options
        if not self.traitor:
            score, _ = self.suspicion()
            heat = self.heat()
            target = self.best(
                {
                    pid: score.get(pid, 0.0) + 0.25 * heat.get(pid, 0.0)
                    for pid in options
                },
                options,
                "vote",
            )
            return Vote(target=target, diary=f"{self.n(target)} is my best guess.")
        team = self.team()
        choices = [pid for pid in options if pid not in team] or list(options)
        heat = self.heat()
        doomed = [
            pid
            for pid in self.obs.fellow_traitors
            if pid in options and heat.get(pid, 0.0) * 2 > len(self.obs.table) / 4 + 1
        ]
        if doomed and self.roll("bus") < 0.5:
            target = doomed[0]
            return Vote(
                target=target,
                diary=f"{self.n(target)} is finished anyway. Voting with the table "
                "buys me trust.",
            )
        threat = self.threat()
        target = self.best(
            {pid: heat.get(pid, 0.0) + 0.5 * threat.get(pid, 0.0) for pid in choices},
            choices,
            "vote",
        )
        return Vote(target=target, diary=f"Send {self.n(target)} away; it isn't us.")

    def endgame(self) -> Decision:
        if self.traitor:
            return EndgameVote(choice="end", diary="End it now, while I'm still here.")
        revealed = sum(
            1
            for pid, role in self.obs.known_roles.items()
            if role == "traitor" and pid != self.me
        )
        left = self.obs.starting_traitors - revealed
        score, _ = self.suspicion()
        worst = max(score.values(), default=0.0)
        if left <= 0 and worst < 3:
            return EndgameVote(choice="end", diary="Every traitor who began is gone.")
        if worst >= 0.9 or self.roll("nerve") < 0.5:
            return EndgameVote(choice="banish", diary="A traitor is still among us.")
        return EndgameVote(choice="end", diary="I can't see another traitor.")

    # ------------------------------------------------------------------ night

    def poison(self) -> Decision:
        options = self.obs.request.options
        shielded = [pid for pid in options if pid in self.shielded()]
        if shielded:
            target = shielded[0]
            return Poison(
                target=target,
                diary=f"{self.n(target)} topped a tally that won a shield. A blade "
                "would bounce; poison won't.",
            )
        threat = self.threat()
        target = self.best(threat, options, "poison")
        if threat.get(target, 0.0) >= 1.5 and self.roll("dare") < 0.5:
            return Poison(
                target=target,
                diary=f"{self.n(target)} is hunting us. Tonight, quietly.",
            )
        return Pass(diary="Not tonight. Save it.")

    def recruit(self) -> Decision:
        options = self.obs.request.options
        alone = not self.obs.fellow_traitors
        if not alone and self.roll("recruit") >= 0.3:
            return Pass(diary="Two is company enough for now.")
        threat = self.threat()
        target = min(options, key=lambda pid: (threat.get(pid, 0.0), self.roll(pid)))
        return Recruit(
            target=target,
            text="Quietly. The Faithful are losing, and you know it. Join us and live.",
            diary=f"{self.n(target)} has never once looked my way. Worth the risk.",
        )

    def recruit_answer(self) -> Decision:
        recruiter = self.obs.request.about or ""
        heat = self.heat()
        chance = 0.15
        if len(self.obs.living) <= 5:
            chance += 0.15
        if heat.get(self.me, 0.0) >= 2:
            chance += 0.2
        if self.roll("temptation") < chance:
            return Accept(
                text="I'm in. Tell me what to do.",
                diary=f"The Faithful won't save me. {self.n(recruiter)} might.",
            )
        return Refuse(
            text=f"Never. And the whole table will hear it was you, "
            f"{self.n(recruiter)}.",
            diary=f"{self.n(recruiter)} is a traitor. If I live till morning, "
            "everyone knows.",
        )

    def nominate(self) -> Decision:
        options = self.obs.request.options
        threat = self.threat()
        guarded = self.shielded()
        pool = [pid for pid in options if pid not in guarded] or list(options)
        target = self.best(threat, pool, "victim")
        return Nominate(
            target=target,
            text=self._choose(
                "why",
                [
                    "Too close to the truth.",
                    "Asks too many questions.",
                    "The others listen to that one.",
                ],
            ),
            diary=f"{self.n(target)} is the one watching us.",
        )

    # ----------------------------------------------------------------- plans

    def plan(self) -> Decision:
        obs = self.obs
        if obs.world.room is None:
            return PlanChoice(
                plan=Plan(goal="keep walking", steps=[Wait(reason="in a passage")])
            )
        if obs.phase == "mission" and obs.mission is not None:
            steps, goal = self._mission_steps()
        elif obs.phase == "free_time":
            steps, goal = self._free_steps()
        else:
            steps, goal = self._gathering_steps()
        return PlanChoice(plan=Plan(goal=goal, steps=steps[:5] or [Wait()]))

    def _mission_steps(self) -> tuple[list[Step], str]:
        obs = self.obs
        mission = obs.mission
        room = obs.world.room
        assert mission is not None and room is not None
        steps: list[Step] = [
            Move(room_id=room_id)
            for room_id in obs.castle.route(room.id, mission.room_id)
        ]
        work: list[Step] = [Use(thing_id=mission.station)] * (5 - len(steps))
        if self.traitor and work and self.roll("slack") < 0.25:
            work[-1] = Wait(reason="catching my breath")
        return [*steps, *work], f"work the {mission.title}"

    def _free_steps(self) -> tuple[list[Step], str]:
        obs = self.obs
        castle = obs.castle
        room = obs.world.room
        assert room is not None
        gather = castle.gathering_room
        left = obs.phase_ticks_left

        def ticks_home(origin: str) -> int:
            here, total = origin, 0
            for room_id in castle.route(origin, gather):
                total += castle.travel(here, room_id) or 0
                here = room_id
            return total

        home = [Move(room_id=room_id) for room_id in castle.route(room.id, gather)]
        if left <= ticks_home(room.id) + 1:
            return list(home) or [Wait(reason="waiting for the round table")], (
                "get back for the round table"
            )
        steps: list[Step] = []
        social = self._social_step()
        if social is not None:
            steps.append(social)
        budget = left - len(steps) - 1
        exits = sorted(
            other
            for passage in castle.passages
            if (other := passage.other(room.id)) is not None
        )
        reachable = [
            room_id
            for room_id in exits
            if (castle.travel(room.id, room_id) or 0) + 1 + ticks_home(room_id)
            <= budget
        ]
        if not reachable:
            return [*steps, *home], "stay close to the hall"
        there = self.pick(reachable, "wander")
        steps.append(Move(room_id=there))
        if castle.room(there).lighting != "bright" and self.roll("search") < 0.6:
            steps.append(ToolStep(tool="search"))
        else:
            steps.append(Wait(reason="looking around"))
        steps += [Move(room_id=room_id) for room_id in castle.route(there, gather)]
        return steps, f"look around the {castle.room(there).name}"

    def _social_step(self) -> Step | None:
        obs = self.obs
        world = obs.world
        room = world.room
        present = [
            figure.player_id
            for figure in world.present
            if figure.player_id is not None and figure.player_id in obs.living
        ]
        if present:
            whom = self.pick(present, "company")
            mood = self.roll("mood")
            if mood < 0.4:
                return TalkStep(text=self._line(whom), to=whom)
            if mood < 0.65 and "whisper" in world.valid_actions:
                return Whisper(to=whom, text=self._whisper(whom))
        if (
            room is not None
            and room.lighting != "bright"
            and not world.searched_here
            and "tool:search" in world.valid_actions
        ):
            return ToolStep(tool="search")
        return None

    def _gathering_steps(self) -> tuple[list[Step], str]:
        steps: list[Step] = []
        if self.obs.world.present and self.roll("chatter") < 0.35:
            steps.append(TalkStep(text=self._line(None)))
        steps += [Wait(reason="waiting for the others")] * (5 - len(steps))
        return steps, "keep my place at the table"

    def _line(self, to: str | None) -> str:
        """Something to say aloud, never about the person being spoken to."""
        news = self._news(to)
        if self.traitor:
            scape = self.scapegoat(excluding=to)
            lines = [
                "Let's not tear each other apart before the round table.",
                "Keep your heads. Panic is how the traitors win.",
                *news,
            ]
            if scape is not None:
                lines += [
                    f"Has anyone kept an eye on {self.n(scape)}? Always elsewhere.",
                    f"I'd look hard at {self.n(scape)}, if I were you.",
                ]
            return self._choose("line", lines)
        score, _ = self.suspicion()
        lines = [
            "Stay in pairs. Nobody walks the dark rooms alone.",
            "Watch who is last to the table tonight.",
            *news,
        ]
        others = [pid for pid in score if pid != to]
        if others:
            suspect = self.best(score, others, "line")
            lines += [
                f"I keep coming back to {self.n(suspect)}. Has anyone else noticed?",
                f"Where was {self.n(suspect)} this morning? Does anyone know?",
            ]
        if to is not None and score.get(to, 0.0) >= 0.6:
            lines.append(f"Where were you this morning, {self.n(to)}?")
        return self._choose("line", lines)

    def _news(self, to: str | None) -> list[str]:
        """Lines about what the Steward has announced, none of them about ``to``."""
        obs = self.obs
        lines: list[str] = []
        for fate in obs.fates:
            who = self.n(fate.player_id)
            if fate.how == "night" and fate.day == obs.day:
                lines.append(f"{who} is dead. Whoever did it is sitting with us.")
            elif fate.how == "banished" and fate.day >= obs.day - 1:
                if fate.role == "faithful":
                    lines.append(
                        f"We sent {who} away, and {who} was Faithful. We can't afford "
                        "that twice."
                    )
                elif not self.traitor:
                    lines.append(f"We caught {who}. There are more of them.")
        if obs.tallies and obs.tallies[-1].reported:
            low = min(
                obs.tallies[-1].reported.items(), key=lambda pair: (pair[1], pair[0])
            )[0]
            if low not in (self.me, to) and low in obs.living:
                lines.append(f"The clerk has {self.n(low)} at the bottom of the tally.")
        return lines

    def _whisper(self, to: str) -> str:
        if self.traitor:
            scape = self.scapegoat(excluding=to)
            if scape is None:
                return "Keep your voice down. Walls here have ears."
            return f"Keep this to yourself, but I don't trust {self.n(scape)}."
        score, _ = self.suspicion()
        others = [pid for pid in score if pid != to]
        if not others:
            return "Watch the others today. I'll do the same."
        suspect = self.best(score, others, "whisper")
        return f"Between you and me: I think it's {self.n(suspect)}."

    def _choose(self, purpose: str, lines: Sequence[str]) -> str:
        return lines[int(self.roll(purpose) * len(lines))]


__all__ = ["HeuristicBot", "Mind"]
