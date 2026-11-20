"""Довідник регіону. Бот обслуговує лише Івано-Франківськ та область (Прикарпаттяобленерго)."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Provider:
    id: int
    name: str


@dataclass(frozen=True)
class City:
    key: str
    title: str
    oblast: str
    providers: tuple[Provider, ...] = field(default_factory=tuple)


CITIES: tuple[City, ...] = (
    City(
        key="if_oblast",
        title="Івано-Франківськ та область",
        oblast="Івано-Франківська",
        providers=(Provider(1, "Прикарпаттяобленерго"),),
    ),
)

# 6 черг × 2 підчерги
GROUPS: tuple[str, ...] = tuple(f"{g}.{s}" for g in range(1, 7) for s in (1, 2))


def oblasts() -> list[str]:
    seen: list[str] = []
    for c in CITIES:
        if c.oblast not in seen:
            seen.append(c.oblast)
    return seen


def cities_in(oblast: str) -> list[City]:
    return [c for c in CITIES if c.oblast == oblast]


def get_city(key: str) -> City:
    for c in CITIES:
        if c.key == key:
            return c
    raise KeyError(key)


def get_provider(city: City, provider_id: int) -> Provider:
    for p in city.providers:
        if p.id == provider_id:
            return p
    raise KeyError(provider_id)


def source_key(city: City, provider_id: int) -> str:
    """Унікальний ключ джерела даних: один запит до API = одне джерело."""
    return f"prk:{city.key}:{provider_id}"


def all_sources() -> list[tuple[str, City, int]]:
    return [(source_key(c, p.id), c, p.id) for c in CITIES for p in c.providers]
