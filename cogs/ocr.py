import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import json

class OCRAPICog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Substitua pela sua chave ou use variável de ambiente
        self.api_key = "K85923424888957" 
        self.api_url = "https://api.ocr.space/parse/image"

    @app_commands.command(name="ocr", description="Lê texto de uma imagem usando API externa.")
    @app_commands.describe(arquivo="A imagem para ler")
    async def ocr_api(self, it: discord.Interaction, arquivo: discord.Attachment):
        if not arquivo.content_type or not arquivo.content_type.startswith("image"):
            return await it.response.send_message("❌ Envie uma imagem válida!", ephemeral=True)

        await it.response.defer(thinking=True)

        # Prepara os dados para enviar para a API
        payload = {
            "apikey": self.api_key,
            "url": arquivo.url,
            "language": "por", # Português
            "isOverlayRequired": False,
            "FileType": ".Auto",
            "IsCreateSearchablePDF": False,
            "isSearchablePdfHideTextLayer": True
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.api_url, data=payload) as resp:
                    result = await resp.json()

            if result.get("OCRExitCode") == 1:
                # Pega o texto de todas as "páginas" (no caso, da imagem)
                texto_final = ""
                for parsed_result in result.get("ParsedResults", []):
                    texto_final += parsed_result.get("ParsedText", "")

                if not texto_final.strip():
                    return await it.edit_original_response(content="🔍 Nenhum texto encontrado.")

                # Formatação para o Discord
                if len(texto_final) > 1900:
                    texto_final = texto_final[:1900] + "..."

                embed = discord.Embed(
                    title="📄 Texto Extraído (via API)",
                    description=f"```text\n{texto_final}\n```",
                    color=discord.Color.green()
                )
                await it.edit_original_response(embed=embed)
            else:
                erro = result.get("ErrorMessage", ["Erro desconhecido"])[0]
                await it.edit_original_response(content=f"❌ Erro na API: {erro}")

        except Exception as e:
            print(f"Erro no OCR API: {e}")
            await it.edit_original_response(content="❌ Erro ao conectar com o serviço de OCR.")

async def setup(bot):
    await bot.add_cog(OCRAPICog(bot))