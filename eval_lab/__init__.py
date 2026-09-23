"""
eval_lab/ — the lab that looks for the limits of JARVIS.

    python -m eval_lab --help
    python -m eval_lab.selftest

A root package in the sense of docs/V2_CONTRACT.md §3: it imports context/,
server/, presence/ and avatar/ to exercise them, and NOTHING imports it.
Deleting this directory leaves JARVIS exactly as it was; `python main.py`
never loads a line of it.

    scenario.py     the canonical format, ids, validation, oracle matching
    world.py        simulated clock, isolation, the SUT/lab boundary
    surfaces.py     scenario -> real code -> trace, one function per layer
    face.py         the avatar surface, through Node
    properties.py   invariants every scenario of a surface must satisfy
    runner.py       verdicts, results, resume, shards
    legacy.py       the existing tests, as canonical scenarios
"""
