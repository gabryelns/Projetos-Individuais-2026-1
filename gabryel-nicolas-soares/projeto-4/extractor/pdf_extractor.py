"""
Módulo de Extração de Dados (UDA - Unstructured Data Analysis)
==============================================================
Estratégia: FULL-SCAN
Justificativa: O Boletim de Conjuntura é um documento curto (1-3 páginas)
com tabelas concentradas. Enviar o texto integral ao GPT-4 garante
que o modelo veja o contexto completo sem risco de perda de dados
entre chunks. Custo de tokens é irrelevante para documentos desta dimensão.

Fluxo:
  PDF → PyMuPDF (extração de texto) → GPT-4 (extração semântica) → Pydantic (validação)
"""

import os
import json
import fitz  # PyMuPDF
from openai import OpenAI
from typing import Optional

import sys
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from models.schema import BoletimConjuntura


# ─── Prompt do Sistema ────────────────────────────────────────────────────────
# O prompt é o "Contrato Semântico" do pipeline.
# Ele blinda o banco contra alucinações, forçando o LLM a:
# 1. Extrair APENAS valores numéricos presentes no documento
# 2. Usar null para campos ausentes (nunca inventar valores)
# 3. Ignorar percentuais de destaque de marketing e focar nas tabelas
# 4. Retornar JSON estruturado e nada mais

SYSTEM_PROMPT = """
Você é um extrator especializado de dados financeiros e operacionais do setor habitacional brasileiro.
Sua função é extrair EXCLUSIVAMENTE os dados presentes nas tabelas do documento fornecido.

REGRAS OBRIGATÓRIAS:
1. Extraia APENAS valores numéricos EXPLICITAMENTE presentes no texto. NUNCA invente ou infira valores.
2. Se um valor não estiver presente no documento, use null. NUNCA use 0 como substituto de dado ausente.
3. Os valores percentuais devem ser números decimais SEM o símbolo de %. Ex: "-32%" → -32, "+14%" → 14.
4. Ignore textos de marketing, destaques editoriais e variações enfatizadas fora das tabelas principais.
5. Foque nas tabelas estruturadas de LANÇAMENTOS e VENDAS por empresa.
6. O campo "observacao_editorial" deve conter o texto de conclusão/destaque em caixa alta, se existir.
7. Retorne APENAS o JSON válido, sem markdown, sem texto explicativo, sem blocos de código.

ESTRUTURA OBRIGATÓRIA DO JSON:
{
  "ano": <int>,
  "trimestre": <int entre 1 e 4>,
  "empresas": [
    {
      "empresa": "<nome>",
      "lancamentos_vs_trimestre_anterior": <float ou null>,
      "lancamentos_vs_mesmo_trimestre_ano_anterior": <float ou null>,
      "lancamentos_acumulado_9m_ano_anterior": <float ou null>,
      "lancamentos_acumulado_9m_atual": <float ou null>,
      "vendas_vs_trimestre_anterior": <float ou null>,
      "vendas_vs_mesmo_trimestre_ano_anterior": <float ou null>,
      "vendas_acumulado_9m_ano_anterior": <float ou null>,
      "vendas_acumulado_9m_atual": <float ou null>
    }
  ],
  "totais_setor": {
    "total_lancamentos_vs_trimestre_anterior": <float ou null>,
    "total_lancamentos_vs_mesmo_trimestre_ano_anterior": <float ou null>,
    "total_lancamentos_acumulado_9m_ano_anterior": <float ou null>,
    "total_lancamentos_acumulado_9m_atual": <float ou null>,
    "total_vendas_vs_trimestre_anterior": <float ou null>,
    "total_vendas_vs_mesmo_trimestre_ano_anterior": <float ou null>,
    "total_vendas_acumulado_9m_ano_anterior": <float ou null>,
    "total_vendas_acumulado_9m_atual": <float ou null>
  },
  "observacao_editorial": "<string ou null>"
}

Mapeamento das colunas das tabelas para os campos JSON:
- Coluna "X 2T25" ou "X TRI ANTERIOR" → lancamentos_vs_trimestre_anterior / vendas_vs_trimestre_anterior
- Coluna "X 3T24" ou "X MESMO TRI ANO ANT" → lancamentos_vs_mesmo_trimestre_ano_anterior / vendas_vs_mesmo_trimestre_ano_anterior
- Coluna "9m 24/23" → lancamentos_acumulado_9m_ano_anterior / vendas_acumulado_9m_ano_anterior
- Coluna "9m 25/24" → lancamentos_acumulado_9m_atual / vendas_acumulado_9m_atual
"""


def extrair_texto_pdf(caminho_pdf: str) -> str:
    """
    Extrai todo o texto de um PDF usando PyMuPDF (Full-Scan).
    Concatena o texto de todas as páginas com separador de página.
    """
    doc = fitz.open(caminho_pdf)
    paginas = []
    for numero, pagina in enumerate(doc, start=1):
        texto = pagina.get_text("text")
        paginas.append(f"=== PÁGINA {numero} ===\n{texto}")
    doc.close()

    texto_completo = "\n\n".join(paginas)
    print(f"[EXTRACTOR] Texto extraído: {len(texto_completo)} caracteres, {len(doc_pages := paginas)} páginas.")
    return texto_completo


def extrair_dados_com_llm(texto_pdf: str, modelo: str = "gpt-4o") -> BoletimConjuntura:
    """
    Envia o texto completo do PDF ao GPT-4 e valida a resposta com Pydantic.

    Estratégia Full-Scan: texto integral no prompt do usuário.
    O sistema retorna JSON que é validado pelo contrato BoletimConjuntura.
    """
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    print(f"[LLM] Enviando texto ao {modelo}...")

    resposta = client.chat.completions.create(
        model=modelo,
        temperature=0,          # Zero para máxima determinismo e reprodutibilidade
        response_format={"type": "json_object"},  # Força saída JSON nativa do GPT-4
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Extraia os dados do seguinte Boletim de Conjuntura do Setor Habitacional.\n\n"
                    f"CONTEÚDO DO DOCUMENTO:\n{texto_pdf}\n\n"
                    "Retorne APENAS o JSON conforme o schema especificado."
                ).replace("texto_completo_str", texto_pdf)
            }
        ],
        max_tokens=2000
    )

    conteudo_raw = resposta.choices[0].message.content
    print(f"[LLM] Resposta recebida ({len(conteudo_raw)} chars). Validando com Pydantic...")

    # Limpeza defensiva: remove blocos markdown caso o modelo os inclua
    conteudo_limpo = conteudo_raw.strip()
    if conteudo_limpo.startswith("```"):
        conteudo_limpo = conteudo_limpo.split("```")[1]
        if conteudo_limpo.startswith("json"):
            conteudo_limpo = conteudo_limpo[4:]

    # Parse JSON e validação pelo contrato Pydantic
    dados_json = json.loads(conteudo_limpo)
    boletim = BoletimConjuntura(**dados_json)

    print(f"[PYDANTIC] Dados validados: {boletim.ano}/T{boletim.trimestre}, {len(boletim.empresas)} empresas.")
    return boletim


def processar_pdf(caminho_pdf: str, modelo: str = "gpt-4o") -> BoletimConjuntura:
    """
    Pipeline completo de processamento de um PDF:
    1. Extrai texto via PyMuPDF (Full-Scan)
    2. Envia ao GPT-4 com contrato semântico
    3. Valida resposta com Pydantic
    4. Retorna objeto BoletimConjuntura pronto para persistência
    """
    if not os.path.exists(caminho_pdf):
        raise FileNotFoundError(f"Arquivo não encontrado: {caminho_pdf}")

    print(f"\n[PIPELINE] Iniciando processamento: {os.path.basename(caminho_pdf)}")

    # Etapa 1: Extração de texto (Full-Scan)
    texto = extrair_texto_pdf(caminho_pdf)

    if not texto.strip():
        raise ValueError(
            "PDF sem texto extraível (pode ser imagem escaneada). "
            "Considere adicionar OCR com pytesseract para este caso."
        )

    # Etapa 2 + 3: LLM + Validação Pydantic
    boletim = extrair_dados_com_llm(texto, modelo=modelo)

    return boletim
