import time
import logging
import json
import redis.asyncio as redis
from redis.exceptions import LockError

logger = logging.getLogger(__name__)

class DuelEngine:
    def __init__(self, redis_url: str = "redis://localhost"):
        # Usamos decode_responses para facilitar manipulação de Hash maps (recebe string em vez de bytes)
        self.redis = redis.from_url(redis_url, decode_responses=True)
        self.ttl = 7200  # 2 horas em segundos para limite de vida

    async def setup_duel(self, duel_id: str, p1_id: int, p1_hp: int, p2_id: int, p2_hp: int):
        """
        Aloca os mapeamentos Hash para inicializar o Duelo em memória e define TTL
        para prevenir memory leaks caso o duelo não seja finalizado explicitamente.
        """
        info_key = f"tcg:duel:{duel_id}:info"
        p1_state_key = f"tcg:duel:{duel_id}:player:{p1_id}:state"
        p2_state_key = f"tcg:duel:{duel_id}:player:{p2_id}:state"
        queue_key = f"tcg:duel:{duel_id}:queue"

        async with self.redis.pipeline(transaction=True) as pipe:
            # Informações gerais da instância do duelo
            pipe.hset(info_key, mapping={
                "turn_player_id": str(p1_id),
                "rodada": "1",
                "timestamp": str(int(time.time()))
            })

            # Estado HP e Cooldowns - Jogador 1
            pipe.hset(p1_state_key, mapping={
                "hp_atual": str(p1_hp),
                "hp_max": str(p1_hp),
                "cooldown": "{}"
            })

            # Estado HP e Cooldowns - Jogador 2
            pipe.hset(p2_state_key, mapping={
                "hp_atual": str(p2_hp),
                "hp_max": str(p2_hp),
                "cooldown": "{}"
            })

            # Fila de logs do duelo (Pilha)
            pipe.lpush(queue_key, f"Duelo {duel_id} iniciado. {p1_id} vs {p2_id}.")

            # Aplicação de TTL obrigatório para limpeza
            pipe.expire(info_key, self.ttl)
            pipe.expire(p1_state_key, self.ttl)
            pipe.expire(p2_state_key, self.ttl)
            pipe.expire(queue_key, self.ttl)

            await pipe.execute()
            
        logger.info(f"Estruturas em memória de Redis configuradas para o duelo {duel_id}.")

    async def process_attack(self, duel_id: str, author_id: int, enemy_id: int, damage: int) -> bool:
        """
        Mecanismo central de disparo de ataque usando Controle Pessimista de Concorrência.
        Garante Atomicidade nos cálculos para cenários de high-concurrency e UI latency (Discord Buttons).
        """
        lock_name = f"tcg:duel:{duel_id}:lock"
        # O timeout do Lock atua como failsafe caso um Worker morra com a trava
        lock = self.redis.lock(lock_name, timeout=5)

        # A trava tenta bloquear, falha na hora (blocking=False) se alguém já pegou
        acquired = await lock.acquire(blocking=False)
        
        if not acquired:
            logger.warning(f"Trava falhou. Abortando coroutine de ataque do autor {author_id} no duelo {duel_id}. Prevenção de spam de botões.")
            return False

        try:
            info_key = f"tcg:duel:{duel_id}:info"
            
            # Cruza a identidade (author_id) com quem deve jogar (turn_player_id)
            turn_player = await self.redis.hget(info_key, "turn_player_id")
            
            if not turn_player or int(turn_player) != author_id:
                logger.info(f"Clique rejeitado: O turno pertencia ao jogador {turn_player}, mas {author_id} tentou agir.")
                return False

            enemy_state_key = f"tcg:duel:{duel_id}:player:{enemy_id}:state"
            queue_key = f"tcg:duel:{duel_id}:queue"

            # HGET do HP do alvo
            enemy_hp_str = await self.redis.hget(enemy_state_key, "hp_atual")
            if not enemy_hp_str:
                return False
                
            enemy_hp = int(enemy_hp_str)
            
            # Subtrai o HP
            novo_hp = max(0, enemy_hp - damage)

            # Atomic Pipeline
            async with self.redis.pipeline(transaction=True) as pipe:
                # Subtrai o HP via HSET no inimigo
                pipe.hset(enemy_state_key, "hp_atual", str(novo_hp))
                
                # Altera o turno devolvendo-o ao inimigo
                pipe.hset(info_key, "turn_player_id", str(enemy_id))
                
                # Adiciona log na fila tcg:duel:{id}:queue
                log_data = json.dumps({"action": "attack", "author": author_id, "damage": damage, "new_hp": novo_hp})
                pipe.lpush(queue_key, log_data)
                
                await pipe.execute()

            return True

        finally:
            # Em qualquer cenário (Sucesso, erro de código, turno errado), libera o Mutex
            try:
                await lock.release()
            except LockError:
                # Silencioso, significa que expirou pelo timeout failsafe 
                pass
