# Áudio para teste de portabilidade

`fala-pt-br.wav`: fala sintética gerada localmente com a voz Luciana (pt-BR) do macOS, velocidade 155 palavras/minuto. PCM mono, 16 kHz, 16 bits. Duas frases com uma pausa adicional de 1,5 s entre elas. Sem gravação de cliente e sem API paga.

Texto:

> Hoje vamos editar um vídeo. Cada palavra precisa aparecer no momento certo.
>
> Agora vamos salvar o arquivo. O editor mantém as palavras completas e remove os silêncios.

Teste real opt-in: `ESTUDIO_TESTE_TRANSCRICAO_REAL=1 python tests/test_transcription_real.py`.
