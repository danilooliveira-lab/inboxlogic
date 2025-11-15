import os
import io
import re
import json
import requests
from PyPDF2 import PdfReader
from dotenv import load_dotenv
import email

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
# Usando o gpt-4o-mini, que é mais rápido e barato para esta tarefa.
OPENAI_MODEL = "gpt-4o-mini"


def extrair_texto(file_bytes: bytes, filename: str) -> str:
    """
    Extrai texto de .txt, .pdf, .mbox (fallback: tenta leitura como texto).
    Retorna string limpa.
    """
    if not file_bytes:
        return ""
    name = filename.lower()
    
    if name.endswith(".txt"):
        try:
            return file_bytes.decode("utf-8", errors="ignore")
        except Exception:
            return file_bytes.decode("latin-1", errors="ignore")
    
    elif name.endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(file_bytes))
            textos = []
            for p in reader.pages:
                try:
                    textos.append(p.extract_text() or "")
                except Exception:
                    textos.append("")
            return "\n".join(textos).strip()
        except Exception as e:
            # Fallback: se a leitura do PDF falhar, tenta ler como texto
            try:
                return file_bytes.decode("utf-8", errors="ignore")
            except Exception:
                return ""
    
    elif name.endswith(".mbox"):
        # Tenta a extração estruturada de e-mails do mbox
        try:
            return extrair_emails_de_mbox(file_bytes)
        except Exception:
            # Se falhar, trata como um arquivo de texto normal
            try:
                return file_bytes.decode("utf-8", errors="ignore")
            except Exception:
                return ""
    else:
        # Padrão para outros tipos de arquivo: tenta decodificar como texto
        try:
            return file_bytes.decode("utf-8", errors="ignore")
        except Exception:
            return ""


def extrair_emails_de_mbox(file_bytes: bytes) -> str:
    """
    Extrai mensagens de um arquivo .mbox fornecido como bytes.
    Retorna uma única string que concatena (subject + corpo) de cada mensagem,
    separadas por duas quebras de linha.
    """
    if not file_bytes:
        return ""

    # Divide o arquivo mbox em mensagens individuais.
    # O separador padrão de mbox é uma linha que começa com "From "
    parts = re.split(rb'\n(?=From )', file_bytes)
    textos = []

    for part in parts:
        part = part.strip()
        if not part:
            continue
        try:
            # Usa a lib 'email' para parsear os bytes de cada mensagem
            msg = email.message_from_bytes(part)
        except Exception:
            # Fallback se o parse falhar: decodifica o bloco como texto puro
            try:
                txt = part.decode("utf-8", errors="ignore")
            except Exception:
                txt = part.decode("latin-1", errors="ignore")
            textos.append(txt)
            continue

        subj = msg.get('subject', '') or ''
        body = ""

        # Tenta extrair o corpo (body) da mensagem
        if msg.is_multipart():
            # Se for multipart, procura pela parte 'text/plain'
            for p in msg.walk():
                ctype = p.get_content_type()
                disp = str(p.get('Content-Disposition') or "")
                if ctype == 'text/plain' and 'attachment' not in disp:
                    try:
                        payload = p.get_payload(decode=True) or b""
                        # Tenta decodificar com o charset_or 'utf-8'
                        body = payload.decode(p.get_content_charset() or 'utf-8', errors='ignore')
                        break
                    except Exception:
                        # Fallback para latin-1 se utf-8 falhar
                        try:
                            body = payload.decode('latin-1', errors='ignore')
                            break
                        except Exception:
                            body = ""
        else:
            # Se for mensagem simples, pega o payload direto
            try:
                payload = msg.get_payload(decode=True)
                if payload is None:
                    raw = msg.get_payload()
                    if isinstance(raw, str):
                        body = raw
                    else:
                        body = str(raw)
                else:
                    body = payload.decode(msg.get_content_charset() or 'utf-8', errors='ignore')
            except Exception:
                try:
                    body = msg.get_payload()
                    if isinstance(body, bytes):
                        body = body.decode('utf-8', errors='ignore')
                except Exception:
                    body = ""

        subj = subj.strip()
        body = body.strip()

        # Concatena assunto e corpo para formar o texto final da mensagem
        if subj and body:
            textos.append(f"Assunto: {subj}\n\n{body}")
        elif subj:
            textos.append(f"Assunto: {subj}")
        elif body:
            textos.append(body)

    # Junta todas as mensagens extraídas com um separador claro (---)
    # Este separador é usado depois para dividir os e-mails para a IA
    return "\n\n---\n\n".join(textos)


def preprocessar_texto(texto: str) -> str:
    """
    Limpeza simples de texto: normaliza espaços, remove caracteres estranhos básicos.
    """
    if not texto:
        return ""
    texto = texto.replace("\r", "\n")
    # Remove caracteres não-imprimíveis ou estranhos, mantendo acentuação
    texto = re.sub(r"[^\x00-\x7F\u00C0-\u017F\n\t,.!?;:()\"'@%$ªºº\-—\s]", " ", texto)
    # Normaliza espaços e quebras de linha
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


def _call_openai_system_user(system_prompt: str, user_prompt: str, max_tokens: int = 1000, temperature: float = 0.0):
    """
    Wrapper simples para chamada ao endpoint de chat completions usando requests.
    Retorna (conteudo_texto, data_response_json)
    """
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY não configurada no ambiente.")

    payload = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": max_tokens,
        "temperature": temperature # 0.0 para respostas determinísticas
    }

    resp = requests.post(OPENAI_URL, headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}, json=payload, timeout=30)
    # Garante que a API retornou um status OK (ex: 200)
    resp.raise_for_status()
    data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except Exception:
        content = ""
    return content, data


# Classificador heurístico simples para usar como fallback
def simple_heuristic_classifier(texto: str):
    """
    Heurística simples: procura palavras-chave para decidir label.
    Retorna (label, score)
    (mantém labels esperados: 'produtivo', 'improdutivo', 'neutro')
    """
    if not texto:
        return "neutro", 0.0

    txt = texto.lower()
    # Palavras-chave que indicam produtividade ou necessidade de ação
    productive_keywords = ["reunião", "deadline", "prazo", "entrega", "concluir", "aprovado", "confirma", "ok", "agendar", "agendada", "projeto", "tarefa", "pendente", "prioridade", "ajuda"]
    # Palavras-chave que indicam spam ou e-mails inúteis
    unproductive_keywords = ["spam", "promoção", "oferta", "unsubscribe", "loteria", "ganhou", "propaganda", "anúncio", "fake"]

    prod_score = sum(1 for k in productive_keywords if k in txt)
    unprod_score = sum(1 for k in unproductive_keywords if k in txt)

    if prod_score == 0 and unprod_score == 0:
        # Se não encontrar nada, considera neutro
        return "neutro", 0.3
    if prod_score >= unprod_score:
        # normaliza para 0.5 .. 0.9
        score = 0.5 + min(prod_score / len(productive_keywords), 0.4)
        return "produtivo", round(score, 2)
    else:
        score = 0.5 + min(unprod_score / len(unproductive_keywords), 0.4)
        return "improdutivo", round(score, 2)


def classificar_com_openai(texto: str, max_por_lote: int = 10):
    """
    Entrada: texto (pode conter vários e-mails separados por '\n\n---\n\n').
    Retorna:
      - se múltiplos: lista de dicts [{"label":..., "score":...}, ...] com mesmo comprimento;
      - se single: (label, score, meta)
    Estratégia:
      1) split em partes,
      2) processar por lotes (max_por_lote),
      3) para cada lote, exigir JSON array estrito no prompt,
      4) validar comprimento, retry/reformat, fallback heurístico por item faltante.
    """
    if not texto:
        raise ValueError("Texto vazio para classificação.")

    # Divide o texto em e-mails individuais usando o separador (definido no extrair_emails_de_mbox)
    partes = [p.strip() for p in texto.split("\n\n---\n\n") if p.strip()]
    multiple = len(partes) > 1

    # --- Rota 1: E-mail único ---
    # Se for apenas um e-mail, o fluxo é mais simples
    if not multiple:
        # Prompt curto e rígido para JSON único
        system = "Você é um classificador. Responda SOMENTE com um JSON: {\"label\":\"produtivo|improdutivo|neutro\",\"score\":0-1}"
        user = f"Classifique este texto:\n\n{texto}\n\nResposta: JSON."
        conteudo, data = _call_openai_system_user(system, user, max_tokens=800, temperature=0.0)
        meta = {"source": "openai", "raw": conteudo, "usage": data.get("usage")}
        try:
            # Tenta parsear a resposta da IA
            parsed = json.loads(conteudo)
            label = parsed.get("label", "neutro").lower()
            score = float(parsed.get("score", 0.0))
            return label, score, meta
        except Exception:
            # Fallback: Se a IA falhar ou o JSON for inválido, usa a heurística
            lab, sc = simple_heuristic_classifier(texto)
            meta["fallback"] = "heuristic_single"
            meta["heuristic_details"] = {"label": lab, "score": sc}
            return lab, sc, meta

    # --- Rota 2: Múltiplos e-mails (processamento em lote) ---
    aggregated = []
    meta = {"source": "openai", "batches": []}
    for i in range(0, len(partes), max_por_lote):
        lote = partes[i:i + max_por_lote]
        system = (
            "Você é um classificador que deve retornar EXATAMENTE um JSON ARRAY, sem texto extra. "
            "Cada item do array corresponde a uma mensagem na ordem recebida e deve ter: "
            "{\"label\":\"produtivo|improdutivo|neutro\",\"score\":<0-1>}."
        )
        user = "Classifique as mensagens abaixo (mantenha a ordem):\n\n" + "\n\n---\n\n".join(lote) + "\n\nResposta: JSON array."
        
        # Tentativa 1: Chamar a IA para o lote
        conteudo, data = _call_openai_system_user(system, user, max_tokens=600, temperature=0.0)

        parsed = None
        # Tenta parsear o JSON direto
        try:
            parsed = json.loads(conteudo)
        except Exception:
            # Se falhar, tenta limpar a resposta (IA às vezes adiciona texto "```json" ou "aqui está:")
            s = conteudo.find("[")
            e = conteudo.rfind("]")
            if s != -1 and e != -1 and e > s:
                try:
                    parsed = json.loads(conteudo[s:e+1])
                except Exception:
                    parsed = None

        batch_meta = {"raw": conteudo, "usage": data.get("usage")}
        
        # Se deu tudo certo (é uma lista e o tamanho bate com o lote), adiciona
        if isinstance(parsed, list) and len(parsed) == len(lote):
            normalized = []
            for it in parsed:
                if isinstance(it, dict):
                    lab = (it.get("label") or "neutro").lower()
                    try:
                        sc = float(it.get("score", 0.0))
                    except Exception:
                        sc = 0.0
                else:
                    lab, sc = simple_heuristic_classifier(str(it))
                normalized.append({"label": lab, "score": sc})
            aggregated.extend(normalized)
            batch_meta["status"] = "ok"
            meta["batches"].append(batch_meta)
            continue # Vai para o próximo lote

        # Tentativa 2: Reformatar (se a Tentativa 1 falhou)
        # Pede à IA para corrigir a saída anterior (que provavelmente veio com texto extra)
        try:
            system2 = "A saída anterior não estava no formato correto. Refaça retornando APENAS um JSON ARRAY com os campos label, score."
            user2 = "Reformate a saída anterior como JSON array."
            conteudo2, data2 = _call_openai_system_user(system2, user2, max_tokens=600, temperature=0.0)
            parsed2 = None
            try:
                parsed2 = json.loads(conteudo2)
            except Exception:
                # Tenta limpar o JSON de novo
                s = conteudo2.find("[")
                e = conteudo2.rfind("]")
                if s != -1 and e != -1 and e > s:
                    try:
                        parsed2 = json.loads(conteudo2[s:e+1])
                    except Exception:
                        parsed2 = None
            batch_meta["reformat_attempt"] = conteudo2
            batch_meta["usage_reformat"] = data2.get("usage")
            
            # Se a reformatação funcionou, adiciona
            if isinstance(parsed2, list) and len(parsed2) == len(lote):
                normalized = []
                for it in parsed2:
                    if isinstance(it, dict):
                        lab = (it.get("label") or "neutro").lower()
                        try:
                            sc = float(it.get("score", 0.0))
                        except Exception:
                            sc = 0.0
                    else:
                        lab, sc = simple_heuristic_classifier(str(it))
                    normalized.append({"label": lab, "score": sc})
                aggregated.extend(normalized)
                batch_meta["status"] = "reformatted_ok"
                meta["batches"].append(batch_meta)
                continue
        except Exception:
            pass # Se a reformatação falhar, segue para a heurística

        # Fallback Final: Heurística por item
        # Se as duas tentativas da IA falharem, usa a heurística item por item
        filled = []
        if isinstance(parsed, list):
            # Usa os itens que vieram e aplica heurística para os faltantes
            for j in range(len(lote)):
                if j < len(parsed) and isinstance(parsed[j], dict):
                    lab = (parsed[j].get("label") or "neutro").lower()
                    try:
                        sc = float(parsed[j].get("score", 0.0))
                    except Exception:
                        sc = 0.0
                elif j < len(parsed):
                    lab, sc = simple_heuristic_classifier(str(parsed[j]))
                else:
                    lab, sc = simple_heuristic_classifier(lote[j])
                filled.append({"label": lab, "score": sc})
        else:
            # Nenhum parse: aplicar heurística completa no lote
            for msg in lote:
                lab, sc = simple_heuristic_classifier(msg)
                filled.append({"label": lab, "score": sc})

        aggregated.extend(filled)
        batch_meta["status"] = "heuristic_fallback"
        meta["batches"].append(batch_meta)

    return aggregated, meta


def gerar_resposta_com_openai(texto: str, label: str = None):
    """
    Gera uma resposta automática baseada no conteúdo do e-mail e na classificação.
    Se a chamada ao OpenAI falhar, retorna fallback de texto padrão.
    """
    if not texto:
        return "", {"source": "none"}
    system = "Você é um assistente que escreve respostas curtas e profissionais a e-mails, " \
             "contextualizando pela classificação fornecida."
    user = (
    f"Classificação: {label}\n\n"
    f"Texto:\n{texto}\n\n"
    "Escreva uma resposta curta (2-6 sentenças).\n"
    # Instrução chave para o caso de uso de múltiplos e-mails (como no PDF)
    "Você pode ultrapassar esse limite apenas se houver 2 ou mais e-mails; "
    "nesse caso, gere 2-6 sentenças para cada e-mail e organize as respostas separadamente dividindo os por `---`."
)

    try:
        # max_tokens alto (2500) para garantir que a IA consiga gerar
        # respostas para todos os e-mails em um lote grande (essa foi a correção anterior).
        conteudo, data = _call_openai_system_user(system, user, max_tokens=2500, temperature=0.2)
        resposta = conteudo.strip()
        meta = {"source": "openai", "raw": conteudo, "usage": data.get("usage")}
        return resposta, meta
    except Exception as e:
        # Fallback de resposta: Se a API da OpenAI falhar, retorna uma
        # resposta padrão para não quebrar a interface do usuário.
        fallback = "Obrigado pelo envio. Recebi seu e-mail e vou analisar os pontos e retornar em breve."
        meta = {"source": "fallback", "error": str(e), "fallback_response": True}
        return fallback, meta


def gerar_analise_geral(texto: str):
    """
    Função que recebe um texto (ou vários e-mails concatenados) e pede à IA
    um JSON com insights: { "sugestoes": [...], "temas": [...], "resumo": "..." }
    Retorna (analise_json, meta)
    """
    if not texto:
        return {}, {"source": "none"}

    system = "Você é um assistente que analisa múltiplos e-mails e fornece um JSON com insights."
    user = (
    # O prompt exige um JSON EXCLUSIVO para facilitar o parse no backend
    "Receba um ou vários e-mails e devolva EXCLUSIVAMENTE um JSON com as chaves abaixo "
    "(sem comentários, sem texto fora do JSON):\n\n"
    " - resumo: string com 3 a 8 frases claras e objetivas\n"
    " - temas: lista com EXATAMENTE os 10 principais temas identificados\n"
    "          (cada item deve ser uma frase curta representando um tópico)\n"
    " - acoes: lista com EXATAMENTE as 5 ações mais importantes e práticas\n"
    "          (cada item deve ser curto, direto e acionável)\n\n"
    "Regras obrigatórias:\n"
    " • NÃO devolva mais que 10 temas.\n"
    " • NÃO devolva mais que 5 ações.\n"
    " • NÃO devolva listas vazias.\n"
    " • NÃO adicione textos fora do JSON.\n"
    " • NÃO inclua explicações, justificativas ou observações.\n"
    " • Se identificar muitos temas semelhantes, agrupe-os e escolha apenas os mais relevantes.\n\n"
    "Conteúdo para análise:\n" + texto + "\n\n"
    "Retorne SOMENTE o JSON final, nada além disso."
    )

    conteudo, data = _call_openai_system_user(system, user, max_tokens=1000, temperature=0.0)

    try:
        # Tenta parsear o JSON
        analise_json = json.loads(conteudo)
    except json.JSONDecodeError:
        # Fallback: Se a IA adicionou texto extra, tenta encontrar o JSON
        start = conteudo.find("{")
        end = conteudo.rfind("}")
        if start != -1 and end != -1:
            try:
                analise_json = json.loads(conteudo[start:end+1])
            except Exception as e:
                raise ValueError("Resposta da IA não era JSON válido e não pôde ser recuperada.")
        else:
            raise ValueError("Resposta da IA não continha JSON válido.")

    meta = {"source": "openai", "raw": conteudo, "usage": data.get("usage")}
    return analise_json, meta