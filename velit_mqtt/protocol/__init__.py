"""Pure Velit wire-protocol implementation.

Packet construction, parsing, and checksums for the heater (V1.02) and AC
(V1.01) protocols. These modules have no I/O and no external dependencies —
they turn (func, data) into bytes and bytes back into a parsed dict, and are
exercised directly by the unit tests.
"""
