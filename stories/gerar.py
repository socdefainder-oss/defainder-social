"""Gera os 3 Stories do dia (07h, 12h e 19h) a partir de noticias reais e agenda na agenda.json.

Fluxo: le os feeds RSS das fontes -> a IA (Google Gemini, camada gratuita) escolhe 3 noticias e escreve o texto em pt-BR
(so com o que esta na noticia) -> renderiza 1080x1920 com story.html -> grava midia/<id>.jpg e agenda.

Uso:
  python stories/gerar.py                 gera os Stories de hoje (no GitHub Actions)
  python stories/gerar.py --teste         so renderiza em stories/teste/, sem mexer na agenda
  python stories/gerar.py --agora         gera e agenda 1 Story para agora (teste real)
Variaveis: GEMINI_API_KEY (secret do repositorio), MODELO (opcional, ex.: gemini-flash-latest)
"""
import html
import json
import os
import random
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
MIDIA = RAIZ / "midia"
BRT = timezone(timedelta(hours=-3))
UA = {"User-Agent": "Mozilla/5.0 (compatible; defainder-stories/1.0; +https://defainder.com.br)"}
HORARIOS = ["07:00", "12:00", "19:00"]
JANELA = timedelta(hours=36)
GEMINI = "https://generativelanguage.googleapis.com/v1beta"

FONTES = {
    "CyberSec Brazil": "https://www.cybersecbrazil.com.br/blog-feed.xml",
    "The Hacker News": "https://feeds.feedburner.com/TheHackersNews",
    "BleepingComputer": "https://www.bleepingcomputer.com/feed/",
    "VentureBeat": "https://venturebeat.com/feed/",
    "MIT Technology Review": "https://www.technologyreview.com/feed/",
    "Tecnoblog": "https://tecnoblog.net/feed/",
    "Olhar Digital": "https://olhardigital.com.br/feed/",
    "Canaltech": "https://canaltech.com.br/rss/",
}
POR_FONTE = 10            # noticias mais recentes de cada fonte (equilibra seguranca x IA/tecnologia)
CATEGORIAS = {  # categoria -> (selo, tom, rotulo da dica)
    "ataque": ("Ataque hacker", "warn", "O que fazer"),
    "vazamento": ("Vazamento", "warn", "O que fazer"),
    "golpe": ("Golpe em alta", "warn", "Como se proteger"),
    "ia": ("IA em alta", "", "Por que importa"),
    "tecnologia": ("Tecnologia", "", "Por que importa"),
    "empresa": ("Para empresas", "", "Por que importa"),
}

INSTRUCOES = """Você é o editor de Stories do Instagram da defAInder, empresa brasileira de cibersegurança, IA e desenvolvimento.
Receberá notícias recentes (muitas em inglês). Escolha as 3 MELHORES para Stories de hoje e escreva cada uma em português do Brasil.

Escolha UMA notícia para cada horário:
- "07" (segurança): ataque hacker, vazamento de dados ou golpe relevante (categoria ataque, vazamento ou golpe).
- "12" (IA): novidade, lançamento, avanço ou impacto de inteligência artificial no trabalho, nos negócios ou no dia a dia (categoria ia). Prefira IA FORA do tema segurança.
- "19" (tecnologia/negócios): tecnologia que muda o dia a dia, transformação digital, novidades úteis para empresas (categoria tecnologia ou empresa). NÃO use segurança, golpe ou vazamento neste horário; só se não existir nenhuma notícia de tecnologia/negócios aproveitável, omita o horário.
Prefira notícias relevantes para brasileiros comuns e donos de pequenas/médias empresas.
Evite: ofertas, promoções e preços de produtos, games e entretenimento, ciência fora de tecnologia, política partidária, guerra, mortes, conteúdo sensível, fofoca, notícias muito técnicas (CVE sem impacto claro), rodadas de investimento sem interesse geral, notícias repetidas.

Escreva (tom profissional, direto, sem sensacionalismo, sem gírias):
- manchete: até 60 caracteres, forte e clara. Envolva 1 a 3 palavras-chave entre *asteriscos* para destaque.
- resumo: até 170 caracteres, 1 ou 2 frases explicando o fato.
- dica: até 95 caracteres. Para segurança: uma ação prática de proteção. Para IA/tecnologia: por que isso importa para pessoas ou empresas.
- categoria: uma de: ataque, vazamento, golpe, ia, tecnologia, empresa.

Regras obrigatórias:
- Use SOMENTE fatos presentes no título/resumo da notícia. Não invente números, nomes, datas, causas nem consequências.
- Nomes próprios de empresas e produtos ficam como no original. Não traduza nomes.
- Se não houver notícia boa para um horário, omita esse horário.

Responda apenas JSON: {"stories": [{"horario": "07|12|19", "indice": <número da notícia>, "categoria": "...", "manchete": "...", "resumo": "...", "dica": "..."}]}"""


def baixar(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read()


def limpar(t):
    t = html.unescape(re.sub(r"<[^>]+>", " ", t or ""))
    return re.sub(r"\s+", " ", t).strip()


def noticias(usadas):
    agora = datetime.now(timezone.utc)
    lista = []
    for fonte, url in FONTES.items():
        try:
            raiz = ET.fromstring(baixar(url))
        except Exception as e:
            print(f"aviso: {fonte} indisponivel ({e})")
            continue
        for it in raiz.iter("item"):
            link = (it.findtext("link") or "").strip()
            try:
                data = parsedate_to_datetime(it.findtext("pubDate"))
            except Exception:
                continue
            if not link or link in usadas or agora - data > JANELA:
                continue
            lista.append({"fonte": fonte, "titulo": limpar(it.findtext("title")), "resumo": limpar(it.findtext("description"))[:500],
                          "link": link, "data": data.astimezone(BRT)})
    lista.sort(key=lambda n: n["data"], reverse=True)
    por_fonte = {}
    return [n for n in lista if por_fonte.setdefault(n["fonte"], []).append(n) or len(por_fonte[n["fonte"]]) <= POR_FONTE]


def modelos_gemini(chave):
    """Prefere o apelido estavel; se nao existir, pega o Flash mais novo disponivel para a chave."""
    nomes = [os.environ["MODELO"]] if os.environ.get("MODELO") else []
    nomes += ["gemini-flash-latest", "gemini-flash-lite-latest"]   # apelidos com camada gratuita
    try:
        lst = json.load(urllib.request.urlopen(f"{GEMINI}/models?key={chave}&pageSize=200", timeout=30))["models"]
        flash = [m["name"].split("/")[-1] for m in lst if "generateContent" in m.get("supportedGenerationMethods", [])
                 and m["name"].split("/")[-1].startswith("gemini-") and "flash" in m["name"]
                 and not any(x in m["name"] for x in ("omni", "image", "tts", "audio", "live", "exp", "preview"))]
        print("modelos flash disponiveis:", ", ".join(flash))
        nomes += sorted(flash, reverse=True)[:4]
    except Exception as e:
        print(f"aviso: lista de modelos indisponivel ({e})")
    return list(dict.fromkeys(nomes))


def perguntar_ia(lista):
    chave = os.environ["GEMINI_API_KEY"]
    texto = "\n\n".join(f"[{i}] ({n['fonte']}, {n['data']:%d/%m %H:%M}) {n['titulo']}\n{n['resumo']}" for i, n in enumerate(lista))
    body = json.dumps({"systemInstruction": {"parts": [{"text": INSTRUCOES}]},
                       "contents": [{"role": "user", "parts": [{"text": texto}]}],
                       "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json"}}).encode()
    modelos = modelos_gemini(chave)
    for rodada in range(3):                       # 503 (sobrecarga) costuma passar em segundos
        for modelo in modelos:
            req = urllib.request.Request(f"{GEMINI}/models/{modelo}:generateContent", data=body,
                                         headers={"x-goog-api-key": chave, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    resp = json.load(r)
                txt = "".join(p.get("text", "") for p in resp["candidates"][0]["content"]["parts"])
                return json.loads(txt)["stories"], modelo
            except urllib.error.HTTPError as e:
                print(f"aviso: modelo {modelo} falhou (HTTP {e.code}: {e.read()[:160]!r})")
            except Exception as e:
                print(f"aviso: modelo {modelo} falhou ({e})")
        time.sleep(45)
    return [], None


def valido(s, lista):
    lim = {"manchete": 66, "resumo": 190, "dica": 105}
    if not isinstance(s.get("indice"), int) or not 0 <= s["indice"] < len(lista) or s.get("categoria") not in CATEGORIAS:
        return False
    return all(isinstance(s.get(k), str) and 0 < len(s[k].replace("*", "")) <= v for k, v in lim.items())


def destacar(t):
    t = html.escape(t)
    return re.sub(r"\*(.+?)\*", r"<em>\1</em>", t).replace("*", "")


def renderizar(itens, saida):
    from playwright.sync_api import sync_playwright
    tpl = (AQUI / "story.html").read_text(encoding="utf-8")
    fotos = {c: sorted(p.name for p in (AQUI / "fotos").glob(f"{c}_*.jpg")) for c in CATEGORIAS}
    usadas_fotos = set()
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1080, "height": 1920})
        for it in itens:
            s, n = it["story"], it["noticia"]
            selo, tom, rotulo = CATEGORIAS[s["categoria"]]
            opcoes = [f for f in fotos[s["categoria"]] if f not in usadas_fotos] or fotos[s["categoria"]]
            foto = random.choice(opcoes); usadas_fotos.add(foto)
            pag = (tpl.replace("{{POS}}", "50% 35%").replace("{{FOTO}}", foto).replace("{{TOM}}", tom)
                   .replace("{{PILL}}", "warn" if tom else "").replace("{{SELO}}", selo).replace("{{DATA}}", it["dia"])
                   .replace("{{MANCHETE}}", destacar(s["manchete"])).replace("{{RESUMO}}", html.escape(s["resumo"].replace("*", "")))
                   .replace("{{DICA_LB}}", rotulo).replace("{{DICA}}", html.escape(s["dica"].replace("*", "")))
                   .replace("{{FONTE}}", html.escape(n["fonte"])).replace("{{DATA_FONTE}}", f"{n['data']:%d/%m/%Y}"))
            tmp = AQUI / "_story.html"
            tmp.write_text(pag, encoding="utf-8")
            pg.goto(tmp.as_uri()); pg.wait_for_load_state("networkidle"); pg.evaluate("document.fonts.ready"); pg.wait_for_timeout(300)
            png = saida / f"{it['id']}.png"
            pg.locator(".story").screenshot(path=str(png))
            from PIL import Image
            Image.open(png).convert("RGB").save(saida / f"{it['id']}.jpg", "JPEG", quality=92); png.unlink()
            print(f"story {it['id']}: {s['manchete']}  [{n['fonte']}]")
        b.close()
    (AQUI / "_story.html").unlink(missing_ok=True)


def salvar_agenda(a):
    a = sorted(a, key=lambda i: i["quando"])
    (RAIZ / "agenda.json").write_text("[\n" + ",\n".join("  " + json.dumps(i, ensure_ascii=False) for i in a) + "\n]\n", encoding="utf-8")


def slug(t):
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return "-".join(t.split("-")[:5]) or "noticia"


def main():
    teste, agora_ = "--teste" in sys.argv, "--agora" in sys.argv
    hoje = datetime.now(BRT)
    usadas_p = AQUI / "usadas.json"
    usadas = json.loads(usadas_p.read_text(encoding="utf-8")) if usadas_p.exists() else {}
    agenda = json.loads((RAIZ / "agenda.json").read_text(encoding="utf-8"))
    dia = hoje.strftime("%Y-%m-%d")
    if not teste and any(i["tipo"] == "story" and i["quando"].startswith(dia) for i in agenda) and not agora_:
        print("stories de hoje ja gerados"); return
    lista = noticias(usadas)
    print(f"{len(lista)} noticias nas ultimas {JANELA}")
    if not lista:
        return
    escolhas, modelo = perguntar_ia(lista)
    escolhas = [s for s in escolhas if valido(s, lista)]
    vistos, horas, itens = set(), set(), []
    livres = [h for h in HORARIOS]
    for s in sorted(escolhas, key=lambda s: str(s.get("horario", "99"))):
        h = f"{str(s.get('horario', ''))[:2]}:00"
        if s["indice"] in vistos or not livres:
            continue
        h = h if h in livres else livres[0]      # horario invalido ou repetido: usa o proximo livre
        livres.remove(h); vistos.add(s["indice"])
        n = lista[s["indice"]]
        hora = hoje.strftime("%H:%M") if agora_ else h
        itens.append({"story": s, "noticia": n, "dia": hoje.strftime("%d/%m"), "hora": hora,
                      "id": f"{dia}_{hora[:2]}_story_{slug(s['manchete'].replace('*', ''))}"})
        if len(itens) == 3 or agora_:          # --agora: so 1 Story, para teste real
            break
    print(f"modelo: {modelo} · {len(itens)} stories")
    if not itens:
        return
    saida = AQUI / "teste" if teste else MIDIA
    saida.mkdir(exist_ok=True)
    renderizar(itens, saida)
    if teste:
        return
    for it in itens:
        agenda.append({"id": it["id"], "quando": f"{dia} {it['hora']}", "tipo": "story", "origem": [],
                       "fonte": it["noticia"]["link"], "texto": it["story"]})
        usadas[it["noticia"]["link"]] = dia
    salvar_agenda(agenda)
    corte = (hoje - timedelta(days=30)).strftime("%Y-%m-%d")
    usadas_p.write_text(json.dumps({k: v for k, v in usadas.items() if v >= corte}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
