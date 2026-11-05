"""Gera os 3 Stories do dia (07h, 12h e 19h) a partir de noticias reais e agenda na agenda.json.

Fluxo: le os feeds RSS das fontes -> a IA do GitHub Models escolhe 3 noticias e escreve o texto em pt-BR
(so com o que esta na noticia) -> renderiza 1080x1920 com story.html -> grava midia/<id>.jpg e agenda.

Uso:
  python stories/gerar.py                 gera os Stories de hoje (no GitHub Actions, com GITHUB_TOKEN)
  python stories/gerar.py --teste         so renderiza em stories/teste/, sem mexer na agenda
  python stories/gerar.py --agora         como o normal, mas agenda o 1o Story para agora (teste real)
Variaveis: GITHUB_TOKEN (permissao models: read), MODELO (opcional)
"""
import html
import json
import os
import random
import re
import sys
import unicodedata
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
MODELOS = [os.environ.get("MODELO", "openai/gpt-4.1"), "openai/gpt-4.1-mini"]

FONTES = {
    "CyberSec Brazil": "https://www.cybersecbrazil.com.br/blog-feed.xml",
    "The Hacker News": "https://feeds.feedburner.com/TheHackersNews",
    "BleepingComputer": "https://www.bleepingcomputer.com/feed/",
    "VentureBeat": "https://venturebeat.com/feed/",
    "MIT Technology Review": "https://www.technologyreview.com/feed/",
}
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

Escolha:
- Notícias reais e relevantes para brasileiros comuns e donos de pequenas/médias empresas: ataques e vazamentos grandes, golpes, avanços de IA, tecnologia que muda o dia a dia.
- Varie os assuntos: no máximo 2 de segurança (ataque/vazamento/golpe); inclua pelo menos 1 de IA ou tecnologia quando houver.
- Evite: notícias muito técnicas (CVE sem impacto claro), política partidária, guerra, mortes, conteúdo sensível, fofoca, notícias de fundraising sem interesse geral, notícias repetidas.

Escreva (tom profissional, direto, sem sensacionalismo, sem gírias):
- manchete: até 60 caracteres, forte e clara. Envolva 1 a 3 palavras-chave entre *asteriscos* para destaque.
- resumo: até 170 caracteres, 1 ou 2 frases explicando o fato.
- dica: até 95 caracteres. Para segurança: uma ação prática de proteção. Para IA/tecnologia: por que isso importa para pessoas ou empresas.
- categoria: uma de: ataque, vazamento, golpe, ia, tecnologia, empresa.

Regras obrigatórias:
- Use SOMENTE fatos presentes no título/resumo da notícia. Não invente números, nomes, datas, causas nem consequências.
- Nomes próprios de empresas e produtos ficam como no original. Não traduza nomes.
- Se não houver 3 notícias boas, devolva menos.

Responda apenas JSON: {"stories": [{"indice": <número da notícia>, "categoria": "...", "manchete": "...", "resumo": "...", "dica": "..."}]}"""


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
    return lista[:45]


def perguntar_ia(lista):
    texto = "\n\n".join(f"[{i}] ({n['fonte']}, {n['data']:%d/%m %H:%M}) {n['titulo']}\n{n['resumo']}" for i, n in enumerate(lista))
    for modelo in MODELOS:
        body = {"model": modelo, "temperature": 0.4, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": INSTRUCOES}, {"role": "user", "content": texto}]}
        req = urllib.request.Request("https://models.github.ai/inference/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                                              "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                resp = json.load(r)
            return json.loads(resp["choices"][0]["message"]["content"])["stories"], modelo
        except Exception as e:
            print(f"aviso: modelo {modelo} falhou ({e})")
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
    vistos, itens = set(), []
    for s in escolhas:
        if s["indice"] in vistos:
            continue
        vistos.add(s["indice"])
        n = lista[s["indice"]]
        hora = hoje.strftime("%H:%M") if (agora_ and not itens) else HORARIOS[len(itens)]
        itens.append({"story": s, "noticia": n, "dia": hoje.strftime("%d/%m"), "hora": hora,
                      "id": f"{dia}_{hora[:2]}_story_{slug(s['manchete'].replace('*', ''))}"})
        if len(itens) == 3:
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
