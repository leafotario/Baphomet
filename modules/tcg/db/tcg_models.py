from dataclasses import dataclass
from datetime import datetime

@dataclass
class Player:
    id_usuario: int
    saldo: int
    xp_global: int
    data_entrada: datetime
    total_mensagens: int

@dataclass
class CardTemplate:
    id_serial: int
    nome_moldura: str
    raridade: str
    mascara: str
    multiplicador: float
    avatar_url: str = ""

@dataclass
class CardInstance:
    uuid: str
    dono_id: int
    modelo_id: int
    atk: int
    defesa: int
    spd: int
    passiva: str

@dataclass
class Deck:
    player_id: int
    carta_uuid: str
    # O limite mecânico de 3 a 5 cartas deve ser validado na camada de casos de uso (Use Cases)
