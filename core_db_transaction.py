import asyncio
import time
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import aiosqlite
from discord.ext import tasks

logger = logging.getLogger("BaphometTransactionManager")

class SacrificeValidationError(Exception):
    """Exceção estruturada para falhas de validação de sacrifício."""
    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)

class BaphometTransactionManager:
    """
    Interface responsável por administrar operações atômicas de transação e
    Connection Pooling, impedindo ativamente Race Conditions e ataques de latência.
    """
    def __init__(self, db_path: str = "data/baphomet_transactions.sqlite3", pool_size: int = 5):
        self.db_path = db_path
        self.pool_size = pool_size
        self._pool: asyncio.Queue[aiosqlite.Connection] = asyncio.Queue(maxsize=pool_size)
        self._is_ready = False

    async def initialize(self) -> None:
        """Inicializa o Connection Pool e a infraestrutura de tabelas."""
        if self._is_ready:
            return

        for _ in range(self.pool_size):
            conn = await aiosqlite.connect(self.db_path)
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA journal_mode=WAL;")
            await conn.execute("PRAGMA synchronous=NORMAL;")
            await conn.execute("PRAGMA busy_timeout=5000;")
            await self._pool.put(conn)
        
        async with self.connection() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS user_economy (
                    user_id INTEGER,
                    guild_id INTEGER,
                    xp INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, guild_id)
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS guild_economy (
                    guild_id INTEGER PRIMARY KEY,
                    leviathan_jackpot REAL DEFAULT 0.0
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS escrows (
                    escrow_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    bet_amount INTEGER NOT NULL,
                    created_at REAL NOT NULL
                );
            """)

            # Novas Tabelas de Persistência Global do Cassino
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS casino_configs (
                    game_id TEXT PRIMARY KEY,
                    min_bet INTEGER DEFAULT 1,
                    max_bet INTEGER DEFAULT 1000000,
                    house_edge REAL DEFAULT 0.05,
                    is_enabled BOOLEAN DEFAULT 1
                );
            """)
            games = ["crash_abissal", "labirinto", "danca_negras", "pacto_cego", "oraculo", "ossos", "blackjack", "leviata", "pesados_pecados", "macabra"]
            for game in games:
                await conn.execute("INSERT OR IGNORE INTO casino_configs (game_id, min_bet) VALUES (?, 100)", (game,))

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS active_games_state (
                    session_id TEXT PRIMARY KEY,
                    game_type TEXT NOT NULL,
                    channel_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    message_id INTEGER DEFAULT NULL,
                    expires_at REAL NOT NULL
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS labyrinth_cells (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    x_idx INTEGER NOT NULL,
                    y_idx INTEGER NOT NULL,
                    is_mine BOOLEAN NOT NULL,
                    is_revealed BOOLEAN DEFAULT 0,
                    FOREIGN KEY (session_id) REFERENCES active_games_state (session_id) ON DELETE CASCADE
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS black_flames_participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    escrow_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES active_games_state (session_id) ON DELETE CASCADE
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS abyss_crash_state (
                    session_id TEXT PRIMARY KEY,
                    escrow_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    crash_point REAL NOT NULL,
                    current_multiplier REAL DEFAULT 1.0,
                    is_finalized BOOLEAN DEFAULT 0,
                    FOREIGN KEY (session_id) REFERENCES active_games_state (session_id) ON DELETE CASCADE
                );
            """)
            await conn.commit()

        self._is_ready = True
        self.orphan_escrow_reclaimer.start()

    async def close(self) -> None:
        """Encerra o Connection Pool e detém o coletor de escrows estagnados."""
        self.orphan_escrow_reclaimer.cancel()
        while not self._pool.empty():
            conn = await self._pool.get()
            await conn.close()
        self._is_ready = False

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[aiosqlite.Connection]:
        """Gerencia o ciclo de vida de uma conexão adquirida do Pool."""
        conn = await self._pool.get()
        try:
            yield conn
        finally:
            await self._pool.put(conn)

    async def get_casino_config(self, game_id: str) -> dict:
        async with self.connection() as conn:
            cursor = await conn.execute("SELECT min_bet, max_bet, house_edge, is_enabled FROM casino_configs WHERE game_id = ?", (game_id,))
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return {"min_bet": 100, "max_bet": 1000000, "house_edge": 0.05, "is_enabled": 1}

    async def update_casino_min_bet(self, game_id: str, min_bet: int) -> None:
        async with self.connection() as conn:
            await conn.execute("UPDATE casino_configs SET min_bet = ? WHERE game_id = ?", (min_bet, game_id))
            await conn.commit()

    async def _resolve_vinculo_context(self, user_id: int, guild_id: int, tx: aiosqlite.Connection) -> dict:
        """
        Processamento sob instrução BEGIN IMMEDIATE garantindo travas globais de linha
        (Row-Level Locking). Executado de maneira atômica e segura.
        """
        # Restrições programáticas vitais antes do bloqueio
        if bet_amount <= 0:
            raise SacrificeValidationError("O valor do sacrifício deve ser estritamente maior que zero.")
        
        if bet_amount > 9223372036854775807:
            raise SacrificeValidationError("Sobrecarga cataclísmica: a aposta excede 9223372036854775807 (Integer Overflow preventivo).")

        async with self.connection() as conn:
            try:
                # Instrução mandatória para lock pessimista global de database/linha no driver SQLite.
                await conn.execute("BEGIN IMMEDIATE;")
                
                # Garante pré-existência para a dedução evitar underflow implícito caso não existisse.
                await conn.execute(
                    "INSERT OR IGNORE INTO user_economy (user_id, guild_id, xp) VALUES (?, ?, 0)",
                    (user_id, guild_id)
                )

                # Dedução estrita: verificar saldo real
                async with conn.execute(
                    "SELECT xp FROM user_economy WHERE user_id = ? AND guild_id = ?",
                    (user_id, guild_id)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row is None or row["xp"] < bet_amount:
                        raise SacrificeValidationError("Recursos insuficientes (XP) para realizar este sacrifício.")

                # Consome o XP
                await conn.execute(
                    "UPDATE user_economy SET xp = xp - ? WHERE user_id = ? AND guild_id = ?",
                    (bet_amount, user_id, guild_id)
                )

                # Gera o contrato
                current_time = time.time()
                cursor = await conn.execute(
                    "INSERT INTO escrows (user_id, guild_id, bet_amount, created_at) VALUES (?, ?, ?, ?)",
                    (user_id, guild_id, bet_amount, current_time)
                )
                escrow_id = cursor.lastrowid
                
                await conn.commit()
                return escrow_id

            except Exception:
                await conn.rollback()
                raise

    async def resolve_escrow(self, escrow_id: int, payout_amount: int) -> None:
        """
        Encerra a garantia transacional, injetando o payout obtido e
        coletando sacrifícios falhos para o leviathan_jackpot.
        """
        async with self.connection() as conn:
            try:
                await conn.execute("BEGIN IMMEDIATE;")
                
                async with conn.execute(
                    "SELECT user_id, guild_id, bet_amount FROM escrows WHERE escrow_id = ?",
                    (escrow_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    
                if row is None:
                    raise SacrificeValidationError("Escrow não encontrado ou já processado/estagnado.")
                    
                user_id = row["user_id"]
                guild_id = row["guild_id"]
                bet_amount = row["bet_amount"]

                await conn.execute("DELETE FROM escrows WHERE escrow_id = ?", (escrow_id,))

                await conn.execute(
                    "UPDATE user_economy SET xp = xp + ? WHERE user_id = ? AND guild_id = ?",
                    (payout_amount, user_id, guild_id)
                )

                # Destina 1.5% do sacrifício falho à reserva leviathan_jackpot
                if payout_amount < bet_amount:
                    falha_amount = bet_amount - payout_amount
                    jackpot_cut = falha_amount * 0.015
                    
                    await conn.execute(
                        "INSERT OR IGNORE INTO guild_economy (guild_id, leviathan_jackpot) VALUES (?, 0.0)",
                        (guild_id,)
                    )
                    await conn.execute(
                        "UPDATE guild_economy SET leviathan_jackpot = leviathan_jackpot + ? WHERE guild_id = ?",
                        (jackpot_cut, guild_id)
                    )

                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

    @tasks.loop(minutes=5)
    async def orphan_escrow_reclaimer(self) -> None:
        """
        Projetado contra panes no host: ativado a cada 5 minutos, limpa a 
        tabela de Escrows estagnados devolvendo integralmente a vitalidade.
        """
        cutoff_time = time.time() - 300  # Limiar de 5 minutos (300 segundos)
        
        async with self.connection() as conn:
            try:
                await conn.execute("BEGIN IMMEDIATE;")
                
                async with conn.execute(
                    "SELECT escrow_id, user_id, guild_id, bet_amount FROM escrows WHERE created_at < ?",
                    (cutoff_time,)
                ) as cursor:
                    orphans = await cursor.fetchall()
                    
                if not orphans:
                    await conn.rollback()
                    return

                for orphan in orphans:
                    esc_id = orphan["escrow_id"]
                    u_id = orphan["user_id"]
                    g_id = orphan["guild_id"]
                    bet = orphan["bet_amount"]
                    
                    # Devolve integralmente a vitalidade ao portador inicial
                    await conn.execute(
                        "UPDATE user_economy SET xp = xp + ? WHERE user_id = ? AND guild_id = ?",
                        (bet, u_id, g_id)
                    )
                    # Limpa da tabela
                    await conn.execute("DELETE FROM escrows WHERE escrow_id = ?", (esc_id,))
                    
                await conn.commit()
                logger.info(f"[Orphan Reclaimer] {len(orphans)} escrows estagnados foram limpos e vitalidade devolvida.")
            except Exception as e:
                await conn.rollback()
                logger.error("Falha nativa no Orphan Escrow Reclaimer", exc_info=e)

    @orphan_escrow_reclaimer.before_loop
    async def before_reclaimer(self) -> None:
        """Assegura estabilidade inicial pre-loop da ext discord.ext.tasks."""
        await asyncio.sleep(5)
