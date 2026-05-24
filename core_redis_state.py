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

class ImproperlyConfiguredError(Exception):
    """Lançada quando variáveis de ambiente essenciais estão ausentes ou mal configuradas."""
    pass

logger = logging.getLogger("Baphomet.Redis")

class AbyssalRedisManager:
    """
    Gerenciador global de cache Level-1 do cassino via Redis.
    Opera como Singleton virtual, fornecendo pools para Distributed Lock e
    armazenamento in-memory chave-valor, provendo cross-guild isolation e auto-TTL.
    """
    def __init__(self, url: str = None):
        # Pilar 1: Desacoplamento Ambiental Estrito e Erradicação de Hardcoding.
        env_url = url or os.getenv("REDIS_URL")
        if not env_url or not env_url.strip():
            logger.critical("[Redis L1] FATAL BOOT: A variável REDIS_URL não está definida no ambiente.")
            raise ImproperlyConfiguredError("A variável REDIS_URL é obrigatória e não foi encontrada no ambiente.")
            
        self.is_production = os.getenv("ENV", "development").lower() in ("production", "prod")
        
        # Validação de URL (Sanity Check para TLS/SSL)
        if self.is_production and not env_url.startswith("rediss://"):
            logger.critical("[Redis L1] Configuração de Redis inválida para o ambiente de produção. O servidor Upstash exige SSL (rediss://).")
            raise ImproperlyConfiguredError("Configuração de Redis inválida para o ambiente de produção. O servidor Upstash exige SSL (rediss://).")
            
        self.url = env_url
        
        # Telemetria de Ambiente (Sanitização)
        parsed_test = urllib.parse.urlparse(self.url)
        safe_boot_url = self.url.replace(parsed_test.password, "******") if parsed_test.password else self.url
        logger.info(f"[Redis L1] Iniciando conexão Redis com URL: {safe_boot_url}. Modo: {'Produção' if self.is_production else 'Desenvolvimento'}")
        
        self._pool: Optional[redis.Redis] = None
        self._is_connected = False
        self._network_blackhole = False # Se True, interrompe loops de reconexão
        
        # Circuit Breaker Properties
        self._failed_attempts = 0
        self._last_failure_time = 0.0
        self._circuit_open_duration = 60.0  # Halted Mode após 3 falhas
        
        self._last_ping_latency = 0.0
        self._resolved_host = "Unknown"
        
    async def connect(self) -> bool:
        """
        Estabelece o pool de conexões assíncronas com o Redis de forma estrita.
        """
        if self._network_blackhole:
            return False

        parsed_url = urllib.parse.urlparse(self.url)
        host = parsed_url.hostname
        port = parsed_url.port or 6379
        
        if not host:
            raise ImproperlyConfiguredError(f"A REDIS_URL fornecida é malformada ou não possui hostname: {self.url}")
        
        logger.debug(f"[Redis L1] Iniciando diagnóstico de conexão para o host: {host}:{port}")

        # Active Network Probing (DNS Resolution Strict)
        try:
            resolved_ip = socket.gethostbyname(host)
            self._resolved_host = resolved_ip
            logger.debug(f"[Redis L1] Probe DNS SUCESSO: Host '{host}' foi resolvido para o IP {resolved_ip}.")
        except socket.gaierror as e:
            logger.error(f"[Redis L1] FATAL FORENSE: Falha de conexão: Host '{host}' não resolvido ou inacessível. Erro: {e}")
            self._pool = None
            self._is_connected = False
            self._network_blackhole = True # Pausa operações de cassino sem loop infinito
            return False

        try:
            start_ping = time.perf_counter()
            # Implementação de Arquitetura de Conexão Robusta (ConnectionPool + TLS)
            pool = redis.ConnectionPool.from_url(
                self.url, 
                ssl=self.url.startswith("rediss://"), 
                decode_responses=True, 
                socket_timeout=10.0, 
                retry_on_timeout=True
            )
            self._pool = redis.Redis(connection_pool=pool)
            await self._pool.ping()
            end_ping = time.perf_counter()
            self._last_ping_latency = (end_ping - start_ping) * 1000
            
            self._is_connected = True
            self._network_blackhole = False
            safe_url = self.url.replace(parsed_url.password, "******") if parsed_url.password else self.url
            logger.info(f"[Redis L1] Conexão estabelecida e PING validado ({self._last_ping_latency:.2f}ms): {safe_url}")
            return True
        except (RedisConnectionError, RedisTimeoutError, OSError) as e:
            error_str = str(e).lower()
            if "connection closed by server" in error_str:
                logger.error("[Redis L1] Falha de protocolo TLS/SSL. Verifique se o servidor (ex: Upstash) exige SSL e se a autenticação foi passada corretamente (rediss://).")
            else:
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
        Implementa padrão Aggressive Recovery (Retry-On-Demand / Lazy Connection).
        Se estiver offline, força um re-handshake instantâneo SEM cooldown.
        """
        if self._is_connected:
            return True
            
        current_time = time.time()
        
        # Circuit Breaker: Anti-Spam de Logs (Halted Mode)
        if self._failed_attempts >= 3:
            time_since_failure = current_time - self._last_failure_time
            if time_since_failure < self._circuit_open_duration:
                # Silencioso: não inunda o console
                return False
            else:
                logger.info("[Redis CB] Tempo de resfriamento esgotado. Tentando religar infraestrutura...")
                self._failed_attempts = 0

        logger.info("[Redis CB] Realizando tentativa de reconexão ativa...")
        await self.close() # Hard Reset explícito
        success = await self.connect()
        
        if success:
            self._failed_attempts = 0
            logger.info("[Redis CB] Circuito fechado. Alta Disponibilidade restaurada.")
            return True
        else:
            self._failed_attempts += 1
            self._last_failure_time = current_time
            if self._failed_attempts >= 3:
                logger.critical("[Redis CB] Sistema em modo de espera por falha de conexão persistente. Entrando em Halted Mode por 60s.")
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
