"""The Castle benchmark (SPEC section 7).

* ``ratings`` — the dependency-free OpenSkill-style (Weng-Lin) engine;
* ``seats`` — per-seat numbers from a finished game, behind a small protocol;
* ``leaderboard`` — deception and detection ratings keyed by who played each
  seat, with the reference bots on a board of their own;
* ``matrix`` — the attacker-by-target manipulation matrix, susceptibility by
  technique and usage rates;
* ``steer`` — the steer/resist fit of manipulation success, with bootstrap
  intervals;
* ``dryrun`` — playing a schedule with reference bot profiles.
"""
