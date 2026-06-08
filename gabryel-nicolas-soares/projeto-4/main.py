"""
Ponto de Entrada Principal do Pipeline UDA
==========================================
Orquestra os três modos de execução:

  python main.py api        → Sobe a API REST (FastAPI)
  python main.py crawl      → Inicia monitoramento contínuo das fontes de RI
  python main.py ingerir <caminho_pdf>  → Processa um PDF local manualmente
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from db.database import init_db


def modo_api():
    import uvicorn
    print("[MAIN] Iniciando API REST na porta 8000...")
    print("[MAIN] Documentação disponível em: http://localhost:8000/docs")
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")


def modo_crawl():
    from crawler.ri_crawler import iniciar_monitoramento_continuo
    intervalo = int(os.getenv("CRAWLER_INTERVALO_HORAS", "24"))
    print(f"[MAIN] Iniciando crawler com intervalo de {intervalo}h...")
    iniciar_monitoramento_continuo(intervalo_horas=intervalo)


def modo_ingerir(caminho_pdf: str):
    from crawler.ri_crawler import processar_pdf_local
    if not os.path.exists(caminho_pdf):
        print(f"[MAIN] ✗ Arquivo não encontrado: {caminho_pdf}")
        sys.exit(1)
    empresa = os.getenv("EMPRESA_FONTE", "Manual")
    boletim = processar_pdf_local(caminho_pdf=caminho_pdf, empresa_fonte=empresa, url_origem=f"local://{os.path.abspath(caminho_pdf)}")
    if boletim:
        print(f"\n[MAIN] ✓ Extração concluída!")
        print(f"  Período   : {boletim.ano} / T{boletim.trimestre}")
        print(f"  Empresas  : {[e.empresa for e in boletim.empresas]}")
        if boletim.observacao_editorial:
            print(f"  Editorial : {boletim.observacao_editorial[:120]}...")


def main():
    init_db()
    if len(sys.argv) < 2:
        print(__doc__)
        print("Uso:")
        print("  python main.py api")
        print("  python main.py crawl")
        print("  python main.py ingerir <caminho_do_pdf>")
        sys.exit(0)
    comando = sys.argv[1].lower()
    if comando == "api":
        modo_api()
    elif comando == "crawl":
        modo_crawl()
    elif comando == "ingerir":
        if len(sys.argv) < 3:
            print("[MAIN] ✗ Informe o caminho do PDF: python main.py ingerir <arquivo.pdf>")
            sys.exit(1)
        modo_ingerir(sys.argv[2])
    else:
        print(f"[MAIN] ✗ Comando desconhecido: '{comando}'")
        print("Comandos disponíveis: api | crawl | ingerir")
        sys.exit(1)


if __name__ == "__main__":
    main()