"""Python imports this at start-up in the environment of an acceptance check.

A check must judge the program, not the machine's resolver. ``http.server`` asks
``socket.getfqdn()``, a reverse DNS lookup, to name itself *before* it starts listening; on a
machine whose resolver is slow that holds a correct server back for tens of seconds, the check gives
up, and a working program is scored as failing a criterion. Naming oneself needs no lookup here.
"""

import socket


def _getfqdn(name: str = "") -> str:
    return name or socket.gethostname()


socket.getfqdn = _getfqdn
