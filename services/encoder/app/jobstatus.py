"""Gedeelde jobstatussen, zodat legacy-code en de MediaGuard-brug hetzelfde tellen.

In handoff-modus eindigt een geslaagde job niet op ``klaar`` maar wacht hij in
staging op core (``wacht_op_commit``) en eindigt hij op ``gecommit`` of
``geweigerd``. Een gestaged resultaat bewaakt het bestand nog (actief); een
gecommit resultaat telt als voltooid, net als ``klaar`` in direct-modus.
"""

from __future__ import annotations

STATUS_WACHT_OP_COMMIT = "wacht_op_commit"
STATUS_GECOMMIT = "gecommit"
STATUS_GEWEIGERD = "geweigerd"

# Bewaakt het bestand: er mag geen tweede job voor starten.
ACTIVE_STATUSES: tuple[str, ...] = ("wacht", "bezig", "verifieren", STATUS_WACHT_OP_COMMIT)
# Er loopt (nog) een mediaproces of verificatie.
RUNNING_STATUSES: tuple[str, ...] = ("bezig", "verifieren")
# Bron is (door de encoder of door core) vervangen.
DONE_STATUSES: tuple[str, ...] = ("klaar", STATUS_GECOMMIT)


def sql_in(statuses: tuple[str, ...]) -> str:
    """Constante SQL-lijst ``('a','b')`` voor vaste, interne statuswaarden."""

    return "(" + ",".join(f"'{status}'" for status in statuses) + ")"


ACTIVE_SQL = sql_in(ACTIVE_STATUSES)
DONE_SQL = sql_in(DONE_STATUSES)
