from __future__ import annotations

"""Constantes e definições puras do sistema de XP."""

import discord
from discord import app_commands

from .utils import XpDifficulty

GuildChannelParam = discord.TextChannel | discord.VoiceChannel | discord.StageChannel | discord.ForumChannel

LEVEL_UP_MESSAGES = [
    "🔥 Magnífico! {mention} acaba de romper as correntes e alcançar o **Nível {level}**!",
    "✨ Incrível! {mention} transcendeu a realidade e chegou ao **Nível {level}**!",
    "⚔️ Que poder absurdo! {mention} upou e agora domina no **Nível {level}**!",
    "👑 Curvem-se! {mention} provou seu valor e subiu para o **Nível {level}**!",
    "🎮 GG WP! {mention} farmou XP o suficiente para chegar ao **Nível {level}**!",
    "🚀 Pra cima! {mention} decolou direto para o **Nível {level}**!",
    "🔮 As profecias estavam certas... {mention} despertou seu poder no **Nível {level}**!",
    "🌟 Um novo astro brilha! {mention} iluminou o servidor alcançando o **Nível {level}**!",
    "🎉 Parabéns, {mention}! Mais um degrau escalado rumo ao topo. Você está no **Nível {level}**!",
    "⚡ O poder de {mention} ultrapassou os limites! Novo **Nível {level}** desbloqueado!"
]

DIFFICULTY_CHOICES = [
    app_commands.Choice(name="🌸 Muito Fácil", value=XpDifficulty.VERY_EASY.value),
    app_commands.Choice(name="✨ Fácil", value=XpDifficulty.EASY.value),
    app_commands.Choice(name="🔥 Normal", value=XpDifficulty.NORMAL.value),
    app_commands.Choice(name="⚔️ Difícil", value=XpDifficulty.HARD.value),
    app_commands.Choice(name="👑 Insano", value=XpDifficulty.INSANE.value),
]
