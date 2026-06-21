import aiosqlite
import logging

logger = logging.getLogger(__name__)

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
