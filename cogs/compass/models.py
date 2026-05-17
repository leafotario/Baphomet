from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Final, Literal, TypedDict


COMPASS_ROTULOS: Final[tuple[str, str, str, str]] = (
    "Superior",
    "Inferior",
    "Esquerdo",
    "Direito",
)
COMPASS_ITEM_KEYS: Final[frozenset[str]] = frozenset({"tipo", "conteudo", "coordenadas"})
COORDINATE_MIN: Final[float] = -10.0
COORDINATE_MAX: Final[float] = 10.0
COORDINATE_DECIMALS: Final[int] = 4

CompassItemKind = Literal["texto", "url", "avatar_id"]


class CompassItemPayload(TypedDict):
    tipo: CompassItemKind
    conteudo: str
    coordenadas: tuple[float, float]


def clamp_coordinate(value: object) -> float:
    try:
        parsed = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError) as exc:
        raise ValueError("coordenadas devem ser numeros validos.") from exc

    if not isfinite(parsed):
        raise ValueError("coordenadas devem ser numeros finitos.")

    parsed = max(COORDINATE_MIN, min(COORDINATE_MAX, parsed))
    return round(parsed, COORDINATE_DECIMALS)


def build_compass_item(
    *,
    tipo: CompassItemKind,
    conteudo: object,
    abscissa: object,
    ordenada: object,
) -> CompassItemPayload:
    normalized_content = " ".join(str(conteudo).split())
    if not normalized_content:
        raise ValueError("conteudo nao pode ser vazio.")

    return {
        "tipo": tipo,
        "conteudo": normalized_content,
        "coordenadas": (
            clamp_coordinate(abscissa),
            clamp_coordinate(ordenada),
        ),
    }


@dataclass(slots=True)
class CompassState:
    autor_id: int
    titulo: str
    rotulos: tuple[str, str, str, str] = COMPASS_ROTULOS
    lista_itens: list[CompassItemPayload] = field(default_factory=list)

    def __post_init__(self) -> None:
        if isinstance(self.autor_id, bool) or not isinstance(self.autor_id, int):
            raise TypeError("autor_id deve ser um inteiro estrito.")

        self.titulo = " ".join(str(self.titulo).split())
        if not self.titulo:
            raise ValueError("titulo nao pode ser vazio.")

        if tuple(self.rotulos) != COMPASS_ROTULOS:
            raise ValueError("rotulos deve seguir a sequencia: Superior, Inferior, Esquerdo, Direito.")

    def anexar_item(self, item: CompassItemPayload) -> None:
        if set(item.keys()) != COMPASS_ITEM_KEYS:
            raise ValueError("item Compass deve conter exatamente as chaves: tipo, conteudo, coordenadas.")

        coordenadas = item["coordenadas"]
        if not isinstance(coordenadas, tuple) or len(coordenadas) != 2:
            raise ValueError("coordenadas deve ser uma tupla imutavel com abscissa e ordenada.")

        item["coordenadas"] = (
            clamp_coordinate(coordenadas[0]),
            clamp_coordinate(coordenadas[1]),
        )
        self.lista_itens.append(item)
