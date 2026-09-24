# Adaptações do Atlas para Linux

- Um interpretador (`sys.executable`) para servidor, Kanban, edição, transcrição, chat e render. Dependências Python 3.12 fixadas em `requirements.txt`.
- `ESTUDIO_RAIZ`, `ESTUDIO_BROLLS`, `ESTUDIO_BIBLIOTECA` e `ESTUDIO_CHAVES` separam projetos, mídias e configuração privada. A estrutura interna dos projetos não mudou.
- `ESTUDIO_MODELOS` guarda os modelos ONNX e o cache `whisper/`. Pré-instalar na imagem evita download na primeira edição.
- `ESTUDIO_PORTA` controla a porta (4123 local). `ESTUDIO_URL_PUBLICA` é o endereço externo que o chat usa nos links; `ESTUDIO_CHAT_CWD` é opcional e o padrão é a raiz do Atlas.
- `faster-whisper` roda `large-v3-turbo`, CPU/int8, português, tempos por palavra e sem condicionamento no trecho anterior. `ESTUDIO_WHISPER_THREADS` limita threads. O adaptador conserva espaços à esquerda e tempos em segundos; fala sem alinhamento é recusada antes dos cortes.
- `fontes/AvenirNext-Bold.ttf` e `fontes/AvenirNext-DemiBold.ttf` são as faces 0 e 2 da coleção Avenir Next presente no Mac de origem, extraídas sem mudar os glifos. Prévia e render usam as mesmas faces. Playfair permanece original.
- Emojis usam Noto Color Emoji no Linux, bitmap de 109 px redimensionada à altura final. Folhas de contato usam DejaVu Sans Bold. Podem ser definidos `ESTUDIO_FONTE_EMOJI` e `ESTUDIO_FONTE_CONTATO`.
- Pose: `python lib/pose.py --instalar` instala o ONNX YOLOv8n-pose de [Xenova](https://huggingface.co/Xenova/yolov8n-pose), revisão `da4224085e2f6ce4c9ff9b670e28765194619db2`, SHA-256 `04f6d2416266f2aba6c5ba8b26de33ed9eba3279f972ac02e33f9f1366547586` (13.484.153 bytes; pesos AGPL-3.0). Ombros e quadris seguem COCO; o pescoço é o ponto médio dos ombros. Detecção multi-pessoa + NMS, maior tronco, mediana das amostras e coordenadas com origem em cima. Sem o modelo ou sem pessoa detectada, mantém o fallback já previsto pelo motor. A referência AM71 não veio no clone e sua comparação visual deve ser feita quando esse projeto estiver disponível.
- Recorte RVM, nomes de máscaras, lógica de composição e prompts de edição permanecem intactos. O RVM só passou a usar `ESTUDIO_MODELOS`.

Verificação de contratos: `python -m unittest discover -s tests -p test_runtime.py -v`.

O teste de transcrição sem rede usa respostas estruturadas simuladas para verificar o contrato e cortes fora das palavras. A inferência com áudio real exige o modelo instalado; comparação de precisão com MLX exige o áudio de referência original.
