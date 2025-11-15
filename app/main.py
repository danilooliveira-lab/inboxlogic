# Importações principais do FastAPI
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from typing import Optional

# Importa as funções de lógica de IA do nosso arquivo nlp_utils
from app.nlp_utils import (
    extrair_texto,
    preprocessar_texto,
    classificar_com_openai,
    gerar_resposta_com_openai,
    gerar_analise_geral
)

# Define o caminho base do projeto (onde este arquivo main.py está)
BASE_DIR = Path(__file__).parent
# Define o diretório dos arquivos estáticos (CSS, JS, imagens)
STATIC_DIR = BASE_DIR / "static"

# Cria a instância principal do aplicativo FastAPI
app = FastAPI(title="AutoU - API")

# "Monta" o diretório estático. Isso permite que o navegador
# acesse arquivos como /static/img/logo.png
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=FileResponse)
async def index():
    # Rota principal (GET) que serve o nosso frontend
    # Simplesmente retorna o arquivo HTML estático
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/classify")
async def classify(text: Optional[str] = Form(None), file: Optional[UploadFile] = File(None)):
    """
    Endpoint para a aba "Classificar & Responder".
    Recebe 'text' (Form) ou 'file' (Upload). Retorna:
      { "classificacao": {"label":..., "score":...}, "resposta": "...", "meta": {...} }
    """
    texto_original = ""

    # Prioriza o processamento do arquivo (file) se ele for enviado
    if file:
        try:
            # Lê o arquivo como bytes e passa para a função de extração
            content = await file.read()
            texto_original = extrair_texto(content, file.filename) or ""
        except Exception as e:
            # Se falhar ao ler o arquivo, retorna um erro claro
            return JSONResponse(status_code=400, content={"erro": f"Erro ao extrair texto: {str(e)}"})
    else:
        # Se não houver arquivo, usa o texto vindo do formulário
        texto_original = text or ""

    # Validação: Garante que temos algum texto para analisar
    if not texto_original.strip():
        return JSONResponse(status_code=400, content={"erro": "Nenhum conteúdo enviado para análise."})

    # Limpa o texto antes de enviar para a IA
    texto_limpo = preprocessar_texto(texto_original)

    # --- 1ª Chamada de IA: Classificar ---
    try:
        # Tenta classificar o texto (pode ser um ou vários e-mails)
        label, score, meta_clf = classificar_com_openai(texto_limpo)
    except Exception as e:
        # Retorna um erro 500 se a IA falhar
        return JSONResponse(status_code=500, content={"erro": "Falha na classificação.", "detalhe": str(e)})

    # --- 2ª Chamada de IA: Gerar Resposta ---
    try:
        # Usa o 'label' da etapa anterior para gerar uma resposta contextual
        resposta, meta_resp = gerar_resposta_com_openai(texto_limpo, label)
    except Exception as e:
        # Se a geração falhar, o nlp_utils já tem um fallback,
        # mas garantimos que não quebre aqui.
        resposta = ""
        meta_resp = {"error": str(e)}

    # Combina os metadados das duas chamadas para debug no frontend
    meta_combined = {
        "origem": meta_clf.get("source", "openai"),
        "classificacao_raw": meta_clf,
        "resposta_raw": meta_resp
    }

    # Retorna a resposta final para o frontend
    return {
        "classificacao": {"label": label, "score": score},
        "resposta": resposta,
        "meta": meta_combined
    }


@app.post("/analise")
async def analise(text: Optional[str] = Form(None), file: Optional[UploadFile] = File(None)):
    # Endpoint para a aba "Análise & Insights"
    texto_original = ""

    # Lógica de extração de texto (idêntica ao /classify)
    if file:
        try:
            content = await file.read()
            texto_original = extrair_texto(content, file.filename) or ""
        except Exception as e:
            return JSONResponse(status_code=400, content={"erro": f"Erro ao extrair texto: {str(e)}"})
    else:
        texto_original = text or ""

    # Validação
    if not texto_original.strip():
        return JSONResponse(status_code=400, content={"erro": "Nenhum conteúdo enviado para análise."})

    # --- Chamada de IA: Gerar Análise ---
    try:
        # Chama a função que pede à IA um JSON estruturado (resumo, temas, ações)
        analise, meta = gerar_analise_geral(texto_original)
    except Exception as e:
        return JSONResponse(status_code=500, content={"erro": "Falha ao gerar análise.", "detalhe": str(e)})

    # Retorna o JSON de análise direto para o frontend
    return {"analise": analise, "meta": meta}