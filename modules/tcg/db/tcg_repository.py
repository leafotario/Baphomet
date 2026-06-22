import asyncio
import aiosqlite
import logging
import random

logger = logging.getLogger(__name__)

async def with_backoff(coro_func, *args, max_retries=5, **kwargs):
    """
    SRE Design: Exponential Backoff com Jitter.
    Força retentativas inteligentes antes de estourar a exceção de I/O bloqueado.
    """
    retries = 0
    base_delay = 0.1
    while True:
        try:
            return await coro_func(*args, **kwargs)
        except aiosqlite.OperationalError as e:
            if "database is locked" in str(e).lower() and retries < max_retries:
                # Delay exponencial + Jitter (ruído aleatório) para evitar colisão sequencial de workers
                delay = (base_delay * (2 ** retries)) + random.uniform(0, 0.1)
                logger.warning(f"SQLite Locked. Retentativa acionada. Backoff: {delay:.2f}s (Tentativa {retries+1}/{max_retries})")
                await asyncio.sleep(delay)
                retries += 1
            else:
                raise

class TCGRepository:
    def __init__(self, db_path: str = "data/baphomet_tcg.db"):
        self.db_path = db_path

    async def init_db(self):
        """
        Inicializa a conexão assíncrona com o SQLite e cria as tabelas
        necessárias, ativando o modo WAL para alta concorrência.
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Ativando o modo WAL para eliminar contenção entre leitura e escrita
                await db.execute("PRAGMA journal_mode=WAL;")
                
                # Habilita chaves estrangeiras
                await db.execute("PRAGMA foreign_keys = ON;")

                # Criação do Schema
                await db.executescript("""
                    -- Tabela de Jogadores
                    CREATE TABLE IF NOT EXISTS players (
                        id_usuario INTEGER PRIMARY KEY,
                        saldo INTEGER NOT NULL DEFAULT 0,
                        xp_global INTEGER NOT NULL DEFAULT 0,
                        data_entrada TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        total_mensagens INTEGER NOT NULL DEFAULT 0
                    );
                    -- Índice no saldo
                    CREATE INDEX IF NOT EXISTS idx_players_saldo ON players(saldo);

                    -- Tabela de Modelos de Cartas (Templates)
                    CREATE TABLE IF NOT EXISTS card_templates (
                        id_serial INTEGER PRIMARY KEY AUTOINCREMENT,
                        nome_moldura TEXT NOT NULL,
                        raridade TEXT NOT NULL,
                        mascara TEXT NOT NULL,
                        multiplicador REAL NOT NULL
                    );
                    -- Índice na raridade
                    CREATE INDEX IF NOT EXISTS idx_templates_raridade ON card_templates(raridade);

                    -- Tabela de Instâncias de Cartas (Cartas dos jogadores)
                    CREATE TABLE IF NOT EXISTS card_instances (
                        uuid TEXT PRIMARY KEY,
                        dono_id INTEGER NOT NULL,
                        modelo_id INTEGER NOT NULL,
                        atk INTEGER NOT NULL,
                        defesa INTEGER NOT NULL,
                        spd INTEGER NOT NULL,
                        passiva TEXT NOT NULL,
                        FOREIGN KEY (dono_id) REFERENCES players(id_usuario) ON DELETE CASCADE,
                        FOREIGN KEY (modelo_id) REFERENCES card_templates(id_serial) ON DELETE CASCADE
                    );
                    -- Índice composto Dono + Modelo
                    CREATE INDEX IF NOT EXISTS idx_instances_dono_modelo ON card_instances(dono_id, modelo_id);

                    -- Tabela de Decks
                    CREATE TABLE IF NOT EXISTS decks (
                        player_id INTEGER NOT NULL,
                        carta_uuid TEXT UNIQUE NOT NULL,
                        FOREIGN KEY (player_id) REFERENCES players(id_usuario) ON DELETE CASCADE,
                        FOREIGN KEY (carta_uuid) REFERENCES card_instances(uuid) ON DELETE CASCADE
                    );
                """)
                await db.commit()
                logger.info("Banco de dados TCG inicializado com sucesso (WAL mode ativado).")
        except Exception as e:
            logger.error(f"Erro ao inicializar o banco de dados TCG: {e}")
            raise

    async def _trade_cards_internal(self, player1_id: int, card1_uuid: str, player2_id: int, card2_uuid: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE;")
            
            try:
                # AppSec Design: Atomic UPDATEs.
                # Não lemos e depois atualizamos. Injetamos a validação de posse rigorosamente 
                # no momento microscópico do UPDATE via cláusula WHERE. Previne 'Double Spending'.
                
                cursor1 = await db.execute(
                    "UPDATE card_instances SET dono_id = ? WHERE uuid = ? AND dono_id = ?", 
                    (player2_id, card1_uuid, player1_id)
                )
                # O cursor aponta 0 rows alteradas se a query não bater com o verdadeiro dono
                if cursor1.rowcount == 0:
                    raise ValueError(f"Race Condition Vetada: Jogador {player1_id} não possui a carta {card1_uuid} ou não é mais dono.")
                
                cursor2 = await db.execute(
                    "UPDATE card_instances SET dono_id = ? WHERE uuid = ? AND dono_id = ?", 
                    (player1_id, card2_uuid, player2_id)
                )
                if cursor2.rowcount == 0:
                    raise ValueError(f"Race Condition Vetada: Jogador {player2_id} não possui a carta {card2_uuid} ou não é mais dono.")

                await db.commit()
                logger.info(f"Trade atômico concluído: {card1_uuid} (P1:{player1_id}) <-> {card2_uuid} (P2:{player2_id})")

            except Exception as e:
                await db.rollback()
                logger.error(f"Falha de transação no trade, ROLLBACK ativado. Erro: {e}")
                raise

    async def trade_cards(self, player1_id: int, card1_uuid: str, player2_id: int, card2_uuid: str):
        """
        Orquestra a troca de cartas entre dois jogadores usando contexto transacional
        com proteção ativa via Exponential Backoff em caso de timeout de lock WAL.
        """
        return await with_backoff(self._trade_cards_internal, player1_id, card1_uuid, player2_id, card2_uuid)

    async def save_card_instance(self, card: "CardInstance"):
        """
        Salva uma nova instância de carta gerada (mint) no banco de dados.
        """
        from modules.tcg.db.tcg_models import CardInstance
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO card_instances (uuid, dono_id, modelo_id, atk, defesa, spd, passiva)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (card.uuid, card.dono_id, card.modelo_id, card.atk, card.defesa, card.spd, card.passiva)
            )
            await db.commit()

    async def get_player(self, player_id: int):
        from modules.tcg.db.tcg_models import Player
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM players WHERE id_usuario = ?", (player_id,))
            row = await cursor.fetchone()
            if row:
                return Player(**dict(row))
            return None

    async def save_player(self, player):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO players (id_usuario, saldo, xp_global, total_mensagens)
                VALUES (?, ?, ?, ?)
                """,
                (player.id_usuario, player.saldo, player.xp_global, player.total_mensagens)
            )
            await db.commit()

    async def get_all_templates(self):
        from modules.tcg.db.tcg_models import CardTemplate
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM card_templates")
            rows = await cursor.fetchall()
            return [CardTemplate(**dict(r)) for r in rows]

    async def get_or_create_member_template(self, member_name: str, raridade: str, mascara: str, multiplicador: float):
        from modules.tcg.db.tcg_models import CardTemplate
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            # Tenta buscar
            cursor = await db.execute("SELECT * FROM card_templates WHERE nome_moldura = ?", (member_name,))
            row = await cursor.fetchone()
            if row:
                return CardTemplate(**dict(row))
            
            # Se não existir, insere e retorna
            await db.execute(
                "INSERT INTO card_templates (nome_moldura, raridade, mascara, multiplicador) VALUES (?, ?, ?, ?)",
                (member_name, raridade, mascara, multiplicador)
            )
            await db.commit()
            
            cursor = await db.execute("SELECT * FROM card_templates WHERE nome_moldura = ?", (member_name,))
            row = await cursor.fetchone()
            return CardTemplate(**dict(row))

    async def ensure_default_templates(self):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM card_templates")
            count = (await cursor.fetchone())[0]
            if count == 0:
                await db.executescript("""
                    INSERT INTO card_templates (nome_moldura, raridade, mascara, multiplicador) VALUES ('Guerreiro das Sombras', 'Comum', 'mask_comum.png', 1.0);
                    INSERT INTO card_templates (nome_moldura, raridade, mascara, multiplicador) VALUES ('Mago Espectral', 'Raro', 'mask_raro.png', 1.5);
                    INSERT INTO card_templates (nome_moldura, raridade, mascara, multiplicador) VALUES ('Guardião Abissal', 'Épico', 'mask_epico.png', 2.0);
                """)
                await db.commit()

    async def get_profile_data(self, player_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM players WHERE id_usuario = ?", (player_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            
            cursor2 = await db.execute("SELECT COUNT(*) as total_cartas FROM card_instances WHERE dono_id = ?", (player_id,))
            cartas = (await cursor2.fetchone())['total_cartas']
            
            data = dict(row)
            data['total_cartas'] = cartas
            return data

    async def get_inventory_page(self, player_id: int, limit: int, offset: int):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT i.uuid, i.atk, i.defesa, i.spd, i.passiva, t.nome_moldura as nome, t.raridade
                FROM card_instances i
                JOIN card_templates t ON i.modelo_id = t.id_serial
                WHERE i.dono_id = ?
                LIMIT ? OFFSET ?
            """, (player_id, limit, offset))
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_available_deck_cards(self, player_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT i.uuid, t.nome_moldura as nome, t.raridade
                FROM card_instances i
                JOIN card_templates t ON i.modelo_id = t.id_serial
                WHERE i.dono_id = ?
            """, (player_id,))
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def set_main_deck(self, player_id: int, uuids: list):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE;")
            try:
                await db.execute("DELETE FROM decks WHERE player_id = ?", (player_id,))
                for uuid in uuids:
                    await db.execute("INSERT INTO decks (player_id, carta_uuid) VALUES (?, ?)", (player_id, uuid))
                await db.commit()
            except Exception:
                await db.rollback()
                raise
