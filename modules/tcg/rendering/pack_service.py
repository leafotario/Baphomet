import io
from typing import List
from PIL import Image

class PackService:
    def __init__(self):
        pass

    async def render_booster_pack(self, card_images: List[io.BytesIO]) -> io.BytesIO:
        """
        Recebe múltiplas instâncias de cartas (buffers de memória) renderizadas e cria um 
        Lienzo Mestre. Cola as cartas iterativamente no eixo X para reduzir as
        requisições I/O no Discord ao enviar uma única imagem unificada de 'Booster Pack'.
        """
        if not card_images:
            raise ValueError("Não há cartas para montar o Booster Pack.")

        # Reverte o buffer de cada carta gerada para instâncias Pillow
        images = []
        for buffer in card_images:
            buffer.seek(0)
            images.append(Image.open(buffer).convert("RGBA"))
        
        # Configurações de layout
        spacing = 40
        # Pega a altura máxima caso existam gabaritos de cartas de tamanhos diferentes
        max_height = max(img.height for img in images)
        
        # Largura total = (soma da largura das cartas) + (espaçamento total)
        total_width = sum(img.width for img in images) + (spacing * (len(images) - 1))
        
        # Inicializa o canvas transparente mestre
        master_canvas = Image.new("RGBA", (total_width, max_height), (0, 0, 0, 0))
        
        current_x = 0
        for img in images:
            # Centraliza no eixo Y para alinhamento uniforme
            y_offset = (max_height - img.height) // 2
            
            # Cola a imagem sobre o Lienzo Mestre
            # O terceiro argumento (img) força a preservação do canal Alpha transparente original
            master_canvas.paste(img, (current_x, y_offset), img)
            
            # Atualiza o ponteiro X iterativamente (Largura atual + Constante de Spacing)
            current_x += img.width + spacing

        # Converte a moldura unificada final para Buff Binário
        output_buffer = io.BytesIO()
        master_canvas.save(output_buffer, format="PNG")
        output_buffer.seek(0)
        
        return output_buffer
