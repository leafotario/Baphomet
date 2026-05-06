# Feature `/ficha`

## Arquitetura

- `cog.py`: comandos slash, permissões, listeners do canal de apresentação e tratamento amigável de erro.
- `field_registry.py`: contrato declarativo dos campos persistidos e suas regras de UX/moderação/render.
- `models.py` e `schemas.py`: dataclasses tipadas para registros persistidos e snapshots de render.
- `repositories/profile_repository.py`: acesso async ao SQLite, sempre com uma sessão/conexão por operação.
- `services/profile_service.py`: orquestra persistência, validação, moderação e dados vivos.
- `services/profile_render_service.py`: renderer Pillow em PNG, cache de template por tema e cache curto por revisão.
- `services/presentation_channel_service.py`: sync do canal de apresentação com burst merge e debounce por usuário.
- `services/level_provider.py`: protocolo/adapters para consumir XP sem acoplar a ficha ao runtime concreto.

## Fluxo de dados

A ficha é sempre escopada por `(guild_id, user_id)`.

Persistimos somente:

- metadados da ficha em `profiles`;
- campos autorais/visuais em `profile_fields`;
- configuração do canal em `guild_profile_settings`;
- eventos administrativos em `profile_moderation_events`.

Não persistimos como verdade definitiva: `member.display_name`, username,
avatar, user id, XP, level e cargo-insígnia. Esses dados são lidos ao vivo ao
montar `ProfileSnapshot`.

## Canal de apresentação

Use `/ficha set-apresentacao canal`.

O bot precisa de:

- `Message Content Intent` ativo no portal do Discord e na inicialização;
- permissão para ver o canal;
- permissão para ler histórico se precisar recompor mensagens antigas.

Mensagens do mesmo usuário no canal configurado, dentro da janela de burst,
são agrupadas e gravadas em `basic_info` com `source_type=presentation_channel`
e `source_message_ids` preenchido. Edição e deleção recompilam o bloco. A
exclusão de dados do usuário limpa campos persistidos e pendências em memória.

## Fontes e assets

O renderer usa apenas fontes locais em `assets/fonts`:

- `Poppins-Regular.ttf`
- `Poppins-Bold.ttf`
- `Montserrat-Black.ttf`

Se faltar alguma fonte, o renderer loga `profile_assets_missing` no startup e
usa fallback previsível do Pillow.

## Temas

Temas vivem em `THEMES`, em `services/profile_render_service.py`, como
`ThemePreset`. Para adicionar um tema:

1. Crie um novo `ThemePreset`.
2. Adicione a chave em `theme_preset.choices` no `FieldRegistry`.
3. Exponha o label no select visual em `views.py`.
4. Rode os smoke tests do renderer.

Templates estáticos são cacheados por tema. A imagem final tem cache curto por
`guild_id`, `user_id`, `render_revision` e assinatura dos dados vivos relevantes.

## XP

A ficha consome `LevelProvider`. O adapter atual (`XpRuntimeLevelProvider`)
consulta `bot.xp_runtime` quando disponível e degrada para `NullLevelProvider`
se o runtime falhar. Futuras mudanças no XP devem manter o contrato de
`LevelSnapshot`: total, level, progresso, restante e cargo-insígnia vivo.

## QA manual

- Criar perfil novo com `/ficha criar`.
- Editar texto em `/ficha editar` > Texto.
- Editar visual em `/ficha editar` > Visual.
- Gerar prévia no editor.
- Renderizar outro usuário com `/ficha ver member`.
- Remover campo com `/ficha admin remover-campo`.
- Restaurar campo com `/ficha admin restaurar-campo`.
- Sincronizar `basic_info` via canal de apresentação.
- Editar uma mensagem de apresentação e conferir recompilação.
- Deletar parcialmente e totalmente mensagens de apresentação.
- Alterar XP/level e conferir barra e labels ao vivo.
- Alterar cargo-insígnia e conferir brasão/slot ao vivo.
- Rodar `/ficha excluir-meus-dados` e conferir que a ficha volta sem campos persistidos.
