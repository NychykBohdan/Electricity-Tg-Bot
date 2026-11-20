from abc import ABC, abstractmethod

from app.core.models import DaySchedule

# group ("3.2") -> список днів (сьогодні, завтра)
SourceData = dict[str, list[DaySchedule]]


class CollectorError(Exception):
    pass


class Collector(ABC):
    """Інтерфейс джерела. Кожне обленерго = окремий клас з методом fetch()."""

    @abstractmethod
    async def fetch(self, region_id: int, provider_id: int) -> SourceData: ...
