import re

# 1. ghost_cleanup.py
with open("cogs/ghost_cleanup.py", "r") as f:
    gc = f.read()

gc = re.sub(r'    @app_commands\.command\(\n        name="configurar_limpeza_saida",\n        description="[^"]+",\n    \)\n', '', gc)
gc = re.sub(r'    @app_commands\.command\(\n        name="status_limpeza_saida",\n        description="[^"]+",\n    \)\n', '', gc)

with open("cogs/ghost_cleanup.py", "w") as f:
    f.write(gc)


# 2. blacklist.py
with open("cogs/blacklist.py", "r") as f:
    bl = f.read()

bl = re.sub(r'    @commands\.hybrid_command\(\n        name="status-canais",\n        aliases=\["status_canais", "listanegra"\],\n        description="[^"]+",\n    \)\n', '', bl)

with open("cogs/blacklist.py", "w") as f:
    f.write(bl)


# 3. server_config.py names
with open("cogs/server_config.py", "r") as f:
    sc = f.read()

sc = sc.replace('name="status_limpeza"', 'name="status_limpeza_saida"')
sc = sc.replace('name="status_canais"', 'name="status-canais"')
sc = sc.replace('name="limpeza_saida"', 'name="configurar_limpeza_saida"')

with open("cogs/server_config.py", "w") as f:
    f.write(sc)

