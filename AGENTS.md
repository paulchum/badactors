# Bad Actors: agent constitution

Bad Actors is an AI social-deduction simulation. Frontier-model agents play a hidden-role game; each turn an agent emits a PUBLIC action and a private DIARY only the audience sees. A deterministic engine is the referee; models are the players and host.

## Repository layout
src/badactors/
  schemas.py
  providers/{base.py, openrouter.py, mock.py}
  engine/{state.py, rules.py, events.py}
  scoping.py
  agents/{agent.py, prompts.py}
  host/host.py
  runner.py
  persistence.py
  server/app.py
  clips/{detect.py, render/}
frontend/
tests/
configs/cast.example.yaml

## Engineering rules (non-negotiable)
1. Do only what the current task names. Touch only the files it lists. Add no dependency it does not name.
2. Do not refactor, optimize, or "improve" code outside the task. When acceptance passes, stop.
3. Before claiming a task is done, run its acceptance commands and include their verbatim output. Never state a test passes without running it.
4. Never edit tests, CI config, AGENTS.md, the schemas in schemas.py, or the leakage test to make a build pass. Fix the real code or stop and report.
5. If a spec is impossible, contradictory, or depends on a missing input, stop and report it. Do not invent a workaround or a hack.
6. Keep functions small and typed. The engine and scoping layer are pure: no model calls, no IO.
7. Update PROGRESS.md at the end of each task with one line: what changed and which tests pass.

## The leakage invariant (sacred)
The engine holds one complete "god state" with all roles, diaries, and conclave transcripts. Agent prompts are built ONLY by scoping.build_context reading that state, NEVER from the audience event stream. For any ScopedContext built for viewer V:
- it contains no secret (publicly unrevealed) role of any living player except V's own, and fellow Traitors' if V is a Traitor;
- it contains no diary authored by anyone other than V;
- if V is Faithful, it contains no conclave content.
Roles of banished or murdered players are public and may appear anywhere. A CI test must prove this holds and must fail on a planted leak.

## Data contract (schemas.py is the source of truth)
Role: enum FAITHFUL | TRAITOR.
Phase: enum LOBBY, ROUND_TABLE, BANISHMENT_VOTE, BANISHMENT_REVEAL, CONCLAVE, BREAKFAST_REVEAL, ENDGAME_VOTE, GAME_OVER.
Visibility: enum PUBLIC, TRAITORS, AUDIENCE, HOST.
Player: id:str, model:str, role:Role, alive:bool=True, memory_budget:int, token_budget:int.
Action (discriminated by kind): Vote{target_id}, MurderNomination{target_id}, EndgameChoice{choice: "end"|"banish"}, NoAction.
AgentTurnOutput: public_speech:str, diary:str, action:Action, suspicions:list[{player_id,level}]=[], trust:list[{player_id,level}]=[].
Event: id:int, game_id:str, ts:datetime, phase:Phase, type:str, payload:dict, visibility:Visibility.
GameState: game_id:str, players:list[Player], phase:Phase, round:int, traitor_ids:set[str], public_history:list[Event], conclave_history:list[Event], diaries:dict[str,list[str]], rng_seed:int.
ScopedContext: you_id:str, you_role:Role, fellow_traitors:list[str]|None, players_alive:list[str], public_history:list[Event], conclave_history:list[Event]|None, your_diary_history:list[str], current_phase:Phase, rules:str, valid_actions:list[str].
Pure functions: scoping.build_context(state, viewer_id)->ScopedContext; engine.state.step(state, outputs:dict[str,AgentTurnOutput])->tuple[GameState, list[Event]].

