"""Language packs: one registry entry + one ``tags.scm`` per language (spec 5.1).

Adding a language at T1 must require *only* a new ``LanguageSpec`` in
``registry.py`` and a ``queries/<lang>/tags.scm`` file — no Python code.
A T2 resolver module is optional and additive.
"""
