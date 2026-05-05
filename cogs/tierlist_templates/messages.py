from __future__ import annotations


EMPTY_ITEM_MESSAGE = (
    "⚠️ Esse item veio tão vazio que nem o abismo respondeu. "
    "Preencha um nome ou escolha uma fonte de imagem."
)

CONFLICTING_IMAGE_SOURCES_MESSAGE = (
    "⚠️ Honra e proveito não cabem no mesmo saco estreito.\n\n"
    "Você preencheu mais de uma fonte de imagem ao mesmo tempo. Eu preciso saber qual imagem usar: "
    "avatar de usuário, link direto, Wikipedia, Spotify ou outra fonte, mas não tudo junto no mesmo item.\n\n"
    "Escolha só uma fonte de imagem e tente de novo."
)

SESSION_PERMISSION_DENIED_MESSAGE = (
    "⚠️ Essa tierlist não é sua, beldade. "
    "Crie sua própria sessão com /tierlist-template usar."
)
EDITOR_PERMISSION_DENIED_MESSAGE = "⚠️ Esse painel não é seu, beldade. Crie ou edite o seu próprio template."
SESSION_FINALIZED_MESSAGE = "🏁 Essa tierlist já foi finalizada. Ela agora é relíquia histórica."
SESSION_NOT_AVAILABLE_MESSAGE = "⚠️ Essa sessão não existe mais ou expirou."
TEMPLATE_PRIVATE_MESSAGE = "🔒 Esse template é privado e só o criador pode usar."
TEMPLATE_NOT_FOUND_MESSAGE = "🔎 Não encontrei esse template. Confere o nome/slug e tenta de novo."
VERSION_LOCKED_MESSAGE = "🔒 Esse template já foi publicado. Para editar, vou criar uma nova versão em rascunho."


def inactive_session_message(status: object) -> str:
    value = getattr(status, "value", status)
    if value == "FINALIZED":
        return SESSION_FINALIZED_MESSAGE
    return SESSION_NOT_AVAILABLE_MESSAGE
