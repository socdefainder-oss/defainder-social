"""Publica posts e Reels agendados no Instagram pela API oficial (Instagram Graph API).

Uso:
  python publicar.py preparar             copia/converte a midia da agenda para midia/ e valida legendas
  python publicar.py publicar [--dry-run] publica o que ja venceu e ainda nao foi publicado
  python publicar.py status               mostra a fila
Stories (tipo "story") sao gerados todo dia por stories/gerar.py, que ja grava a imagem em midia/.
  python publicar.py testar               confere token, conta e cota de publicacao
  python publicar.py renovar ARQUIVO      renova o token (vale 60 dias) e grava o novo em ARQUIVO

Token: usuario do sistema do portfolio Defainder (login unificado, nao expira, nao precisa de Pagina).
Variaveis de ambiente: IG_USER_ID, IG_TOKEN, MEDIA_BASE_URL (endereco publico da pasta midia/),
GRAPH_HOST e GRAPH_VERSION (opcionais; GRAPH_HOST=graph.instagram.com para token de login do Instagram)
"""
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

AQUI = Path(__file__).resolve().parent
ORIGEM = AQUI.parent                      # pasta Instagram (onde ficam "Dia XX.11/...")
MIDIA = AQUI / "midia"
BRT = timezone(timedelta(hours=-3))
VER = os.environ.get("GRAPH_VERSION", "v23.0")
GRAPH = f"https://{os.environ.get('GRAPH_HOST', 'graph.facebook.com')}/{VER}"
RUPLOAD = f"https://rupload.facebook.com/ig-api-upload/{VER}"
ATRASO_MAX = timedelta(hours=6)           # passou disso, nao publica (evita post fora de hora)
MAX_HASHTAGS = 5


def ler_json(nome, padrao):
    p = AQUI / nome
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else padrao


def salvar_json(nome, dados):
    (AQUI / nome).write_text(json.dumps(dados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def arquivos(item):
    """Nomes finais em midia/: imagens viram JPEG (a API nao aceita PNG), Reels ficam em MP4."""
    if item["tipo"] == "reel":
        return [f"{item['id']}.mp4"]
    if item["tipo"] == "story":                # gerado por stories/gerar.py direto em midia/
        return [f"{item['id']}.jpg"]
    return [f"{item['id']}_{n:02d}.jpg" for n in range(1, len(item["origem"]) + 1)]


def quando(item):
    return datetime.strptime(item["quando"], "%Y-%m-%d %H:%M").replace(tzinfo=BRT)


def legenda(item):
    if not item.get("legenda"):
        return ""
    return (AQUI / item["legenda"]).read_text(encoding="utf-8").strip()


# ---------------------------------------------------------------- preparar

def preparar():
    from PIL import Image
    MIDIA.mkdir(exist_ok=True)
    erros = 0
    for item in ler_json("agenda.json", []):
        if item["tipo"] == "story":
            continue
        problemas = []
        n = len(item["origem"])
        if item["tipo"] == "carrossel" and not 2 <= n <= 10:
            problemas.append(f"carrossel precisa de 2 a 10 imagens (tem {n})")
        if item["tipo"] in ("imagem", "reel") and n != 1:
            problemas.append(f"{item['tipo']} usa 1 arquivo (tem {n})")
        texto = legenda(item)
        tags = [w for w in texto.split() if w.startswith("#")]
        if len(tags) > MAX_HASHTAGS:
            problemas.append(f"{len(tags)} hashtags (o Instagram aceita {MAX_HASHTAGS})")
        if len(texto) > 2200:
            problemas.append(f"legenda com {len(texto)} caracteres (limite 2200)")
        for src, dst in zip(item["origem"], arquivos(item)):
            s, d = ORIGEM / src, MIDIA / dst
            if not s.exists():
                problemas.append(f"arquivo nao encontrado: {src}")
            elif d.exists() and d.stat().st_mtime >= s.stat().st_mtime:
                continue
            elif item["tipo"] == "reel":
                shutil.copy2(s, d)
            else:
                Image.open(s).convert("RGB").save(d, "JPEG", quality=95)
        if problemas:
            erros += 1
            print(f"ERRO {item['id']}: " + "; ".join(problemas))
        else:
            print(f"ok   {item['id']}")
    sys.exit(1 if erros else 0)


# ---------------------------------------------------------------- API

def api(metodo, caminho, **params):
    params["access_token"] = os.environ["IG_TOKEN"]
    dados = urllib.parse.urlencode(params).encode()
    url = GRAPH + caminho
    if metodo == "GET":
        req = urllib.request.Request(f"{url}?{dados.decode()}")
    else:
        req = urllib.request.Request(url, data=dados, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{caminho}: {e.read().decode(errors='replace')}") from None


def enviar_video(container, caminho):
    """Upload direto do MP4 (resumable): o video nao precisa estar hospedado."""
    dados = caminho.read_bytes()
    req = urllib.request.Request(f"{RUPLOAD}/{container}", data=dados, method="POST", headers={
        "Authorization": f"OAuth {os.environ['IG_TOKEN']}",
        "offset": "0",
        "file_size": str(len(dados)),
    })
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"upload do video: {e.read().decode(errors='replace')}") from None


def aguardar(container, limite=900):
    fim = time.time() + limite
    while time.time() < fim:
        st = api("GET", f"/{container}", fields="status_code,status")
        if st.get("status_code") == "FINISHED":
            return
        if st.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"container {container}: {st.get('status')}")
        time.sleep(10)
    raise RuntimeError(f"container {container}: tempo esgotado no processamento")


def publicar_item(item, texto):
    ig = os.environ.get("IG_USER_ID") or "me"
    base = os.environ["MEDIA_BASE_URL"].rstrip("/")
    nomes = arquivos(item)
    if item["tipo"] == "reel":
        reel = dict(media_type="REELS", caption=texto, share_to_feed="true", thumb_offset=str(item.get("capa_ms", 0)))
        try:
            cid = api("POST", f"/{ig}/media", upload_type="resumable", **reel)["id"]
            enviar_video(cid, MIDIA / nomes[0])
        except RuntimeError as e:
            # plano B: a Meta baixa o MP4 pelo link publico do repositorio
            print(f"upload direto falhou ({e}); tentando por video_url")
            cid = api("POST", f"/{ig}/media", video_url=f"{base}/{nomes[0]}", **reel)["id"]
    elif item["tipo"] == "story":
        cid = api("POST", f"/{ig}/media", media_type="STORIES", image_url=f"{base}/{nomes[0]}")["id"]
    elif item["tipo"] == "carrossel":
        filhos = [api("POST", f"/{ig}/media", image_url=f"{base}/{n}", is_carousel_item="true")["id"] for n in nomes]
        for f in filhos:
            aguardar(f)
        cid = api("POST", f"/{ig}/media", media_type="CAROUSEL", children=",".join(filhos), caption=texto)["id"]
    else:
        cid = api("POST", f"/{ig}/media", image_url=f"{base}/{nomes[0]}", caption=texto)["id"]
    aguardar(cid)
    return api("POST", f"/{ig}/media_publish", creation_id=cid)["id"]


# ---------------------------------------------------------------- comandos

def publicar(dry):
    agora = datetime.now(BRT)
    feitos = ler_json("publicados.json", {})
    falhas = 0
    for item in sorted(ler_json("agenda.json", []), key=quando):
        if item["id"] in feitos or quando(item) > agora:
            continue
        if agora - quando(item) > ATRASO_MAX:
            print(f"pulado {item['id']}: passou mais de {ATRASO_MAX} do horario; reagende em agenda.json")
            continue
        texto = legenda(item)
        if dry:
            print(f"[dry-run] publicaria {item['tipo']} {item['id']} ({len(texto)} caracteres)")
            continue
        try:
            mid = publicar_item(item, texto)
            if item.get("comentario") and item["tipo"] != "story":
                try:
                    api("POST", f"/{mid}/comments", message=item["comentario"])
                except RuntimeError as e:
                    print(f"aviso {item['id']}: comentario nao enviado ({e})")
        except RuntimeError as e:
            falhas += 1
            print(f"FALHA {item['id']}: {e}")
            continue
        feitos[item["id"]] = {"media_id": mid, "publicado_em": datetime.now(BRT).isoformat(timespec="seconds")}
        salvar_json("publicados.json", feitos)
        print(f"publicado {item['id']} -> {mid}")
    sys.exit(1 if falhas else 0)


def status():
    feitos = ler_json("publicados.json", {})
    agora = datetime.now(BRT)
    for item in sorted(ler_json("agenda.json", []), key=quando):
        if item["id"] in feitos:
            st = "publicado"
        elif quando(item) <= agora - ATRASO_MAX:
            st = "PERDIDO"
        else:
            st = "na fila"
        print(f"{item['quando']}  {item['tipo']:9}  {st:9}  {item['id']}")


def testar():
    ig = os.environ.get("IG_USER_ID") or "me"
    conta = api("GET", f"/{ig}", fields="id,username,media_count")
    cota = api("GET", f"/{ig}/content_publishing_limit", fields="config,quota_usage")
    print(f"conta @{conta['username']} (id {conta.get('id')}) · {conta['media_count']} publicacoes")
    print(f"cota de 24h: {json.dumps(cota.get('data', cota), ensure_ascii=False)}")


def renovar(destino):
    """Troca o token por um novo de 60 dias. O token precisa ter mais de 24h; antes disso a Meta recusa."""
    q = urllib.parse.urlencode({"grant_type": "ig_refresh_token", "access_token": os.environ["IG_TOKEN"]})
    try:
        with urllib.request.urlopen(f"https://graph.instagram.com/refresh_access_token?{q}", timeout=60) as r:
            dados = json.load(r)
    except urllib.error.HTTPError as e:
        print(f"token nao renovado: {e.read().decode(errors='replace')}")
        sys.exit(0)
    Path(destino).write_text(dados["access_token"], encoding="utf-8")
    print(f"token renovado, valido por {int(dados.get('expires_in', 0)) // 86400} dias")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "preparar":
        preparar()
    elif cmd == "publicar":
        publicar("--dry-run" in sys.argv)
    elif cmd == "testar":
        testar()
    elif cmd == "renovar":
        renovar(sys.argv[2])
    else:
        status()
