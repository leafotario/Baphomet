import math
import uuid
from datetime import datetime, timezone
import discord
from modules.tcg.db.tcg_models import CardTemplate, CardInstance, Player
from modules.tcg.db.tcg_repository import TCGRepository

class CardService:
    def __init__(self, repository: TCGRepository):
        self.repository = repository

    async def mint_card(
        self, 
        player: Player, 
        template: CardTemplate, 
        member: discord.Member, 
        reactions_received: int, 
        is_voice_active: bool, 
        is_moderator: bool
    ) -> CardInstance:
        """
        Gera uma nova carta com motor preditivo baseado em atividade do Discord
        (sem uso de RNG puro para os status básicos).
        """
        # ATK: Calculado pelo histórico de mensagens normalizado + Log(XP Bruto)
        coef_norm = 100.0
        # Evita log(0) ou log(negativo) no caso de XP recém-iniciado
        xp_bruto = player.xp_global if player.xp_global > 0 else 1
        base_atk = (player.total_mensagens / coef_norm) + math.log(xp_bruto)
        atk = max(1, int(base_atk * template.multiplicador))

        # DEF: Derivado da quantidade de dias desde o "Join Date" do servidor
        dias_no_servidor = 1
        if member.joined_at:
            now = datetime.now(timezone.utc)
            # member.joined_at já é datetime com tzinfo em discord.py v2
            dias_no_servidor = max(1, (now - member.joined_at).days)
        
        defesa = max(1, int(dias_no_servidor * template.multiplicador))

        # SPD: Favorece alto número de reações/thumbs up (exemplo: 1 spd base para cada 10 reações antes do multi)
        spd = max(1, int((reactions_received / 10.0) * template.multiplicador))

        # Habilidade Passiva Única baseada em cargos/atividade
        # Hierarquia de atribuição de passivas: Mod > Voice Active > Default
        passiva = "nenhuma"
        if is_moderator:
            # ignore_defense_chance: causa dano verdadeiro
            passiva = "ignore_defense_chance"
        elif is_voice_active:
            # stun_chance: inverte o ponteiro de turno
            passiva = "stun_chance"

        card_uuid = str(uuid.uuid4())

        nova_carta = CardInstance(
            uuid=card_uuid,
            dono_id=player.id_usuario,
            modelo_id=template.id_serial,
            atk=atk,
            defesa=defesa,
            spd=spd,
            passiva=passiva
        )

        # Persiste a carta no banco de dados através do repositório
        await self.repository.save_card_instance(nova_carta)

        return nova_carta
