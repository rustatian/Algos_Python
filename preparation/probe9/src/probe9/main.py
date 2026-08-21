import itertools
from dataclasses import dataclass
from itertools import count


@dataclass
class Table:
    id: int
    seats: int


@dataclass
class Booking:
    id: int
    table_id: int
    start: int
    end: int


class ReservationBook:
    def __init__(self, tables: list[Table]):
        self._tables: list[Table] = sorted(tables, key=lambda t: t.seats)
        self._bookings: dict[int, list[Booking]] = {t.id: [] for t in tables}
        self._ids: count[int] = itertools.count(1)
        self._by_key: dict[str, Booking] = {}

    def _overlaps(self, b: Booking, start: int, end: int) -> bool:
        return b.start < end and b.end > start

    def _free(self, t: Table, start: int, end: int) -> bool:
        return all(not self._overlaps(b, start, end) for b in self._bookings[t.id])

    def book(self, party: int, start: int, end: int, key: str | None = None) -> Booking:
        if start > end:
            raise ValueError("start must be less than or equal to end")
        if key is not None and key in self._by_key:
            return self._by_key[key]

        for t in self._tables:
            if t.seats >= party and self._free(t, start, end):
                b = Booking(id=next(self._ids), table_id=t.id, start=start, end=end)
                self._bookings[t.id].append(b)
                if key is not None:
                    self._by_key[key] = b
                return b
        raise ValueError("no table available")
