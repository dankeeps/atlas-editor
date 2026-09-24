"""Fontes de contato e emoji disponíveis tanto no Linux quanto no Mac."""
import os
from PIL import Image, ImageDraw, ImageFont

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def primeiro(*caminhos):
    for p in caminhos:
        if p and os.path.isfile(p): return p
    raise FileNotFoundError("Fonte não instalada: " + ", ".join(p for p in caminhos if p))


def contato():
    return primeiro(os.environ.get("ESTUDIO_FONTE_CONTATO"),
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                    os.path.join(SKILL, "fontes", "AvenirNext-Bold.ttf"))


def emoji_img(ch, altura):
    caminho = primeiro(os.environ.get("ESTUDIO_FONTE_EMOJI"),
                       "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
                       "/System/Library/Fonts/Apple Color Emoji.ttc")
    # Noto Color Emoji só oferece a bitmap de 109 px; a altura final continua igual à prévia.
    tamanho = 109 if "NotoColorEmoji" in os.path.basename(caminho) else 160
    f = ImageFont.truetype(caminho, tamanho)
    caixa = f.getbbox(ch)
    im = Image.new("RGBA", (max(1, caixa[2] - caixa[0]) + 20, max(1, caixa[3] - caixa[1]) + 20))
    ImageDraw.Draw(im).text((10 - caixa[0], 10 - caixa[1]), ch, font=f, embedded_color=True)
    if im.getbbox(): im = im.crop(im.getbbox())
    return im.resize((max(1, int(im.width * altura / im.height)), altura), Image.Resampling.LANCZOS)
