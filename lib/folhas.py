"""Folha de contato com legenda embaixo de cada imagem (para o Claude olhar quadros e B-rolls de uma vez).
uso: python3 folhas.py <saida.jpg> <colunas> <largura da célula> <altura da célula> imagem::legenda [imagem::legenda ...]"""
import sys, textwrap
from PIL import Image, ImageDraw, ImageFont

from fontes import contato
FONTE = contato()

def folha(saida, cols, cw, ch, itens):
    f = ImageFont.truetype(FONTE, max(13, cw // 16)); lh = int(f.size * 1.25); faixa = lh * 3 + 8
    rows = (len(itens) + cols - 1) // cols
    img = Image.new("RGB", (cols * cw + (cols + 1) * 6, rows * (ch + faixa) + (rows + 1) * 6), (18, 18, 22)); d = ImageDraw.Draw(img)
    for k, (arq, leg) in enumerate(itens):
        x = 6 + (k % cols) * (cw + 6); y = 6 + (k // cols) * (ch + faixa + 6)
        try:
            im = Image.open(arq).convert("RGB"); s = min(cw / im.width, ch / im.height)
            im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.LANCZOS)
            img.paste(im, (x + (cw - im.width) // 2, y + (ch - im.height) // 2))
        except Exception:
            d.rectangle([x, y, x + cw, y + ch], outline=(90, 90, 90)); d.text((x + 8, y + 8), "sem imagem", font=f, fill=(160, 160, 160))
        for j, l in enumerate(textwrap.wrap(leg, width=max(12, int(cw / (f.size * 0.55))))[:3]):
            d.text((x + 2, y + ch + 4 + j * lh), l, font=f, fill=(255, 255, 255) if j == 0 else (190, 190, 200))
    img.save(saida, quality=85)

if __name__ == "__main__":
    a = sys.argv[1:]
    folha(a[0], int(a[1]), int(a[2]), int(a[3]), [x.split("::", 1) if "::" in x else (x, "") for x in a[4:]])
