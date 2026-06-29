"""
Teste de validação do InventoryRenderer.

Cria um banco de dados temporário em memória, popula com dados de teste,
e renderiza o inventário para verificar o output visual.
"""
import asyncio
import io
import os
import sys
import aiosqlite

# Adicionar o path do projeto
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.tcg.rendering.inventory_renderer import InventoryRenderer

DB_PATH = "data/test_inventory.db"

async def setup_test_db():
    """Cria o banco de teste com dados simulados."""
    os.makedirs("data", exist_ok=True)
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            DROP TABLE IF EXISTS card_instances;
            DROP TABLE IF EXISTS card_templates;
            
            CREATE TABLE card_templates (
                id_serial INTEGER PRIMARY KEY AUTOINCREMENT,
                nome_moldura TEXT NOT NULL,
                raridade TEXT NOT NULL,
                mascara TEXT NOT NULL,
                multiplicador REAL NOT NULL,
                avatar_url TEXT DEFAULT ''
            );
            
            CREATE TABLE card_instances (
                uuid TEXT PRIMARY KEY,
                dono_id INTEGER NOT NULL,
                modelo_id INTEGER NOT NULL,
                atk INTEGER NOT NULL,
                defesa INTEGER NOT NULL,
                spd INTEGER NOT NULL,
                passiva TEXT NOT NULL
            );
            
            -- Templates
            INSERT INTO card_templates (nome_moldura, raridade, mascara, multiplicador, avatar_url)
                VALUES ('Guerreiro das Sombras', 'Comum', 'mask.png', 1.0, 'https://github.com/fluidicon.png');
            INSERT INTO card_templates (nome_moldura, raridade, mascara, multiplicador, avatar_url)
                VALUES ('Mago Espectral', 'Raro', 'mask.png', 1.5, '');
            INSERT INTO card_templates (nome_moldura, raridade, mascara, multiplicador, avatar_url)
                VALUES ('Guardiao Abissal', 'Epico', 'mask.png', 2.0, 'https://github.com/fluidicon.png');
            INSERT INTO card_templates (nome_moldura, raridade, mascara, multiplicador, avatar_url)
                VALUES ('Dragao Ancestral', 'Lendario', 'mask.png', 3.0, '');
            INSERT INTO card_templates (nome_moldura, raridade, mascara, multiplicador, avatar_url)
                VALUES ('Entidade Cosmica', 'Mitico', 'mask.png', 5.0, 'https://github.com/fluidicon.png');
            
            -- Instancias (12 cartas para testar paginacao)
            INSERT INTO card_instances VALUES ('uuid-0001', 999, 1, 15, 120, 8, 'nenhuma');
            INSERT INTO card_instances VALUES ('uuid-0002', 999, 2, 42, 85, 23, 'stun_chance');
            INSERT INTO card_instances VALUES ('uuid-0003', 999, 3, 78, 200, 45, 'ignore_defense_chance');
            INSERT INTO card_instances VALUES ('uuid-0004', 999, 1, 10, 90, 5, 'nenhuma');
            INSERT INTO card_instances VALUES ('uuid-0005', 999, 4, 150, 340, 88, 'stun_chance');
            INSERT INTO card_instances VALUES ('uuid-0006', 999, 2, 38, 110, 19, 'nenhuma');
            INSERT INTO card_instances VALUES ('uuid-0007', 999, 5, 250, 500, 120, 'ignore_defense_chance');
            INSERT INTO card_instances VALUES ('uuid-0008', 999, 3, 65, 180, 40, 'nenhuma');
            INSERT INTO card_instances VALUES ('uuid-0009', 999, 1, 12, 100, 6, 'nenhuma');
            INSERT INTO card_instances VALUES ('uuid-0010', 999, 4, 140, 310, 80, 'stun_chance');
            INSERT INTO card_instances VALUES ('uuid-0011', 999, 2, 35, 95, 18, 'nenhuma');
            INSERT INTO card_instances VALUES ('uuid-0012', 999, 5, 220, 480, 110, 'ignore_defense_chance');
        """)
        await db.commit()

async def main():
    await setup_test_db()
    
    renderer = InventoryRenderer(db_path=DB_PATH)
    
    # Pagina 1 (9 cartas)
    buf = await renderer.gerar_imagem_inventario("999", pagina=0)
    with open("test_inventory_p1.png", "wb") as f:
        f.write(buf.getvalue())
    print("[OK] Pagina 1 renderizada: test_inventory_p1.png")
    
    # Pagina 2 (3 cartas restantes)
    buf2 = await renderer.gerar_imagem_inventario("999", pagina=1)
    with open("test_inventory_p2.png", "wb") as f:
        f.write(buf2.getvalue())
    print("[OK] Pagina 2 renderizada: test_inventory_p2.png")
    
    # Inventario vazio
    buf3 = await renderer.gerar_imagem_inventario("000", pagina=0)
    with open("test_inventory_empty.png", "wb") as f:
        f.write(buf3.getvalue())
    print("[OK] Estado vazio renderizado: test_inventory_empty.png")
    
    # Cleanup
    os.remove(DB_PATH)
    print("[OK] Banco de teste removido.")

if __name__ == "__main__":
    asyncio.run(main())
