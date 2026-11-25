"""Coleta estatisticas dos posts publicados (curtidas, comentarios e, se o token permitir, visualizacoes/alcance/salvos/compartilhamentos).

Uso (no GitHub Actions, com IG_TOKEN e IG_USER_ID): python estatisticas.py saida.json
O resultado vai como artefato do workflow (nao e commitado: o repositorio e publico).
"""
import json, os, sys, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

VER = os.environ.get("GRAPH_VERSION", "v23.0")
GRAPH = f"https://graph.facebook.com/{VER}"


def get(caminho, **params):
    params["access_token"] = os.environ["IG_TOKEN"]
    url = f"{GRAPH}{caminho}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        corpo = e.read().decode(errors="replace")
        try:
            msg = json.loads(corpo)["error"]["message"]
        except Exception:
            msg = corpo[:300]
        return {"_erro": msg}


def main():
    saida = sys.argv[1] if len(sys.argv) > 1 else "estatisticas.json"
    ig = os.environ.get("IG_USER_ID") or "me"
    pub = json.load(open("publicados.json", encoding="utf-8"))
    conta = get(f"/{ig}", fields="username,followers_count,follows_count,media_count")
    itens = []
    for id_, info in pub.items():
        tipo = id_.split("_")[2]
        if tipo == "story":
            continue
        mid = info["media_id"]
        m = get(f"/{mid}", fields="like_count,comments_count,media_type,media_product_type,permalink,timestamp")
        reg = {"id": id_, "media_id": mid, "publicado_em": info.get("publicado_em"), **m}
        metricas = "views,reach,saved,shares,total_interactions"
        if tipo == "reel":
            metricas += ",ig_reels_avg_watch_time,ig_reels_video_view_total_time"
        ins = get(f"/{mid}/insights", metric=metricas)
        if "_erro" in ins and tipo == "reel":
            ins = get(f"/{mid}/insights", metric="views,reach,saved,shares,total_interactions")
        if "_erro" in ins:
            reg["insights_erro"] = ins["_erro"]
        else:
            for d in ins.get("data", []):
                v = d.get("values", [{}])[0].get("value")
                reg[d["name"]] = v
        itens.append(reg)
    agora = datetime.now(timezone(timedelta(hours=-3))).isoformat(timespec="minutes")
    json.dump({"coletado_em": agora, "conta": conta, "itens": itens}, open(saida, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    erros = sum(1 for i in itens if "insights_erro" in i)
    print(f"{len(itens)} posts coletados; {erros} sem insights")
    if erros:
        print("exemplo de erro de insights:", next(i["insights_erro"] for i in itens if "insights_erro" in i))


if __name__ == "__main__":
    main()
