import json
import os
import logging
from typing import Dict, Any, Optional

try:
    import redis.asyncio as redis
    from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError
except ImportError:
    redis = None
    RedisConnectionError = OSError
    RedisTimeoutError = OSError

logger = logging.getLogger("Baphomet.Redis")

class AbyssalRedisManager:
    """
    Gestor de Estado L1 (Layer 1 Cache) para o Cassino Abissal.
    Substitui a retenção efêmera de dicts locais (e.g. self.active_games) por
    armazenamento in-memory chave-valor, provendo cross-guild isolation e auto-TTL.
    """
    def __init__(self, url: str = None):
        # Pilar 1: Desacoplamento Ambiental Estrito.
        # A URL do Redis DEVE ser injetada via variável de ambiente REDIS_URL.
        # O fallback para localhost existe apenas para desenvolvimento local.
        self.url = url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._pool = None
        self._is_connected = False

    async def connect(self) -> bool:
        """
        Estabelece o pool de conexões assíncronas com o Redis.
        Executa um Healthcheck de Pré-Ignição (PING) para validar
        a topologia de rede antes de permitir operações de I/O.
        Retorna True se a conexão foi bem-sucedida, False caso contrário.
        """
        try:
            self._pool = redis.from_url(self.url, decode_responses=True)
            # Healthcheck de Pré-Ignição: valida que o daemon Redis está vivo e respondendo
            await self._pool.ping()
            self._is_connected = True
            logger.info(f"[Redis L1] Conexão estabelecida e PING validado: {self.url}")
            return True
        except (RedisConnectionError, RedisTimeoutError, OSError) as e:
            logger.error(f"[Redis L1] FALHA DE CONEXÃO — o daemon Redis não respondeu em '{self.url}': {e}")
            self._pool = None
            self._is_connected = False
            return False
        except Exception as e:
            logger.error(f"[Redis L1] Exceção inesperada durante o boot de conexão: {e}")
            self._pool = None
            self._is_connected = False
            return False

    async def close(self):
        """Drena a conexão durante o shutdown gracioso."""
        if self._pool:
            await self._pool.close()

    def _build_key(self, game_type: str, guild_id: int, channel_id: int, user_id: int) -> str:
        """
        Gera uma chave composta imutável.
        A combinação de guild_id, channel_id e user_id assegura que a mesma entidade
        operando simultaneamente em dois canais ou dois servidores não colida a memória.
        """
        return f"baphomet:games:{game_type}:{guild_id}:{channel_id}:{user_id}"

    def _validate_serialization(self, state: Dict[str, Any]):
        """
        Memory Leak Protection.
        Impede serialização caso existam referências a instâncias de Objetos Complexos (discord.Member).
        Garante que apenas IDs (inteiros) circulem na camada de estado.
        """
        import discord
        for key, value in state.items():
            if isinstance(value, (discord.Member, discord.User, discord.Guild, discord.TextChannel)):
                raise ValueError(
                    f"Memory Leak Detectado! Tentativa de reter o objeto complexo '{type(value).__name__}' "
                    f"na chave '{key}'. Use o ID inteiro e puxe on-the-fly com bot.get_user()."
                )

    async def set_game_state(self, game_type: str, guild_id: int, channel_id: int, user_id: int, state: Dict[str, Any], ttl: int = 3600) -> None:
        """
        Serializa e salva um estado no Redis, aplicando o Time-To-Live rigoroso.
        :param state: Dicionário contendo dados primitivos da partida (multiplicadores, apostas).
        :param ttl: O tempo de vida natural (default 1 hora) para purgar sessões presas.
        """
        self._validate_serialization(state)
        key = self._build_key(game_type, guild_id, channel_id, user_id)
        
        # Serialização eficiente sem overhead de Pickle
        payload = json.dumps(state)
        
        await self._pool.setex(name=key, time=ttl, value=payload)

    async def get_game_state(self, game_type: str, guild_id: int, channel_id: int, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Recupera o estado de uma partida isolada.
        :return: Dict primitivo ou None caso o Redis tenha obliterado a chave pelo TTL.
        """
        key = self._build_key(game_type, guild_id, channel_id, user_id)
        data = await self._pool.get(key)
        
        if data:
            return json.loads(data)
        return None

    async def delete_game_state(self, game_type: str, guild_id: int, channel_id: int, user_id: int) -> bool:
        """
        Destrói a alocação de memória no Redis após a resolução do Escrow (Vitória ou Obliterada).
        """
        key = self._build_key(game_type, guild_id, channel_id, user_id)
        deleted = await self._pool.delete(key)
        return bool(deleted)

    async def scan_active_games_by_guild(self, guild_id: int) -> list[str]:
        """
        Busca global isolada de O(N).
        Permite ao administrador localizar chaves retidas apenas dentro da guilda alvo.
        """
        pattern = f"baphomet:games:*:{guild_id}:*:*"
        keys = []
        async for key in self._pool.scan_iter(match=pattern):
            keys.append(key)
        return keys

# Singleton ou dependência gerenciada
# redis_manager = AbyssalRedisManager()
