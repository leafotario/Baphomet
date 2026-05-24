import json
import os
import logging
import socket
import time
import urllib.parse
import traceback
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
        self._pool: Optional[redis.Redis] = None
        self._is_connected = False
        
        # Circuit Breaker Properties
        self._failed_attempts = 0
        self._last_failure_time = 0.0
        self._circuit_open_duration = 30.0  # Secs em Degraded Mode após 3 falhas

    async def connect(self) -> bool:
        """
        Estabelece o pool de conexões assíncronas com o Redis.
        Executa Diagnóstico de Rede (DNS Probe) e Healthcheck de Pré-Ignição (PING).
        Retorna True se a conexão foi bem-sucedida, False caso contrário.
        """
        parsed_url = urllib.parse.urlparse(self.url)
        host = parsed_url.hostname or "localhost"
        port = parsed_url.port or 6379
        
        logger.debug(f"[Redis L1] Iniciando diagnóstico de conexão para o host: {host}:{port}")

        # Active Network Probing (DNS Resolution)
        try:
            socket.getaddrinfo(host, port)
            logger.debug(f"[Redis L1] Probe DNS SUCESSO: '{host}' foi resolvido na rede local.")
        except socket.gaierror as e:
            logger.error(f"[Redis L1] FATAL FORENSE: Falha de resolução DNS. O contêiner não enxerga o host '{host}'. Erro: {e}")
            self._pool = None
            self._is_connected = False
            return False

        try:
            self._pool = redis.from_url(self.url, decode_responses=True)
            await self._pool.ping()
            self._is_connected = True
            safe_url = self.url.replace(parsed_url.password, "******") if parsed_url.password else self.url
            logger.info(f"[Redis L1] Conexão estabelecida e PING validado: {safe_url}")
            return True
        except (RedisConnectionError, RedisTimeoutError, OSError) as e:
            logger.error(f"[Redis L1] FALHA DE CONEXÃO ou AUTH: ({type(e).__name__}) O daemon Redis recusou ou demorou em '{host}:{port}'.\nTraceback Técnico:\n{traceback.format_exc()}")
            self._pool = None
            self._is_connected = False
            return False
        except Exception as e:
            logger.error(f"[Redis L1] Falha atípica estrutural ao inicializar driver Redis: {type(e).__name__}\nTraceback Técnico:\n{traceback.format_exc()}")
            self._pool = None
            self._is_connected = False
            return False

    async def ensure_connection(self) -> bool:
        """
        Garante que a conexão Redis está ativa antes de executar a transação.
        Implementa padrão Circuit Breaker para evitar hangup em quedas prolongadas.
        """
        if self._is_connected:
            return True
            
        current_time = time.time()
        
        # Se o circuito estiver aberto, abortamos imediatamente sem bloquear
        if self._failed_attempts >= 3:
            time_since_failure = current_time - self._last_failure_time
            if time_since_failure < self._circuit_open_duration:
                logger.warning(f"[Redis CB] Circuito Aberto. Modo Degredado mantido. Nova tentativa em {self._circuit_open_duration - time_since_failure:.1f}s.")
                return False
            else:
                logger.info("[Redis CB] Tempo de resfriamento esgotado. Fechando o circuito para Half-Open (Tentativa de Self-Healing)...")
                self._failed_attempts = 0 # Entra em Half-Open

        logger.info("[Redis CB] Realizando tentativa de reconexão ativa (Self-Healing)...")
        success = await self.connect()
        
        if success:
            self._failed_attempts = 0
            logger.success("[Redis CB] Self-Healing concluído com sucesso. Circuito Fechado. Alta Disponibilidade restaurada.")
            return True
        else:
            self._failed_attempts += 1
            self._last_failure_time = current_time
            if self._failed_attempts >= 3:
                logger.critical("[Redis CB] 3 tentativas de reconexão falharam. Circuito Aberto. Transições de cache desativadas temporariamente.")
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
