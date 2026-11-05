# Publicação automática no Instagram

Este script publica Reels e posts sozinho, nos horários da agenda, pela **API oficial do Instagram**. Não pede senha, não há risco de bloqueio da conta e não tem custo: o GitHub Actions roda o agendamento na nuvem, com o notebook desligado.

| Horário (Brasília) | Conteúdo |
|---|---|
| 08h · 12h · 18h | Reels: pessoa física · empresa · IA/automação |
| 09h · 13h · 20h | Posts: carrossel empresa · alerta pessoa física · serviço |

O GitHub pode atrasar o disparo em 5 a 15 minutos nos horários de pico.

## Configuração (uma vez só)

Configuração atual (feita em 04.11.2026). Não é preciso ter uma Página do Facebook.

1. **Portfólio empresarial "Defainder"** (business.facebook.com, ID 5344919129066770). Ele contém a conta **@defainder.ofc** (`IG_USER_ID` 17841401079613299) e o app **defAInder Teste** (ID 1242429261070884).
2. **Permissões do app.** No caso de uso "Gerenciar mensagens e conteúdo no Instagram", as permissões abaixo estão como "Pronto para teste". O app fica em modo de desenvolvimento, porque publica só na própria conta e não precisa da revisão da Meta.
   - `instagram_basic`
   - `instagram_content_publish`
   - `instagram_manage_comments`
3. **Token permanente.** Em Configurações → **Usuários do sistema**, o usuário `publicador` (Admin) tem acesso total ao @defainder.ofc e ao app. O token é gerado em **Gerar token**, com expiração **Nunca** e as 3 permissões. Para trocar o token, use **Anular tokens**, gere um novo e atualize o secret `IG_TOKEN`.
4. **GitHub.**
   - Repositório **público** `defainder-social` com o conteúdo desta pasta.
   - Em Settings → Secrets and variables → Actions, crie os secrets `IG_USER_ID` e `IG_TOKEN`.
   - Crie a variável `MEDIA_BASE_URL` com o valor `https://raw.githubusercontent.com/<usuario>/defainder-social/main/midia`.
   - O repositório precisa ser público porque a API baixa as imagens por esse endereço. O token fica protegido nos secrets e nunca aparece no código.
5. **Teste.** Em Actions → *Publicar no Instagram* → **Run workflow**. Localmente, `python publicar.py testar` mostra a conta e a cota de 24h, desde que as variáveis estejam definidas no terminal.

## Rotina para agendar conteúdo

1. Coloque o texto em `legendas/<id>.txt`, com no máximo 5 hashtags.
2. Adicione o item em `agenda.json`:
   ```json
   {"id": "2026-11-07_09_carrossel_backup", "quando": "2026-11-07 09:00", "tipo": "carrossel",
    "origem": ["Dia 07.11/Posts/09h_backup_01.png", "Dia 07.11/Posts/09h_backup_02.png"],
    "legenda": "legendas/2026-11-07_09_carrossel_backup.txt", "comentario": "Salve para não esquecer."}
   ```
   - `tipo` pode ser `reel`, `imagem` ou `carrossel` (de 2 a 10 imagens).
   - `origem` é o caminho relativo à pasta Instagram.
   - `capa_ms` é opcional e define o momento do Reel usado como capa.
3. Rode `python publicar.py preparar`. O comando converte PNG para JPEG, copia os vídeos para `midia/` e valida legenda e hashtags.
4. Rode `python publicar.py publicar --dry-run` para conferir.
5. Faça commit e push. O GitHub publica no horário e registra cada publicação em `publicados.json`.

`python publicar.py status` mostra a fila. Um item que passar mais de 6 horas do horário não é publicado (aparece como PERDIDO). Para publicá-lo, mude o `quando` dele em `agenda.json`.

## O que a API não faz (fica manual, se quiser)

- Fixar o comentário. O script publica o comentário, mas fixar é feito pelo app.
- Música do catálogo do Instagram, Reel de teste, colaboradores e marcação de produtos.

## Stories diários (notícias reais)

O workflow **Gerar Stories do dia** roda às 05h35 (Brasília). Ele lê os feeds de CyberSec Brazil, The Hacker News, BleepingComputer, VentureBeat e MIT Technology Review. A IA do Google Gemini (camada gratuita, secret `GEMINI_API_KEY`) escolhe 3 notícias e escreve os textos em português, usando só o que está na matéria. O script `stories/gerar.py` monta as imagens com `stories/story.html` e agenda os Stories para 07h, 12h e 19h. O publicador posta como `tipo: "story"`.

- Teste sem publicar: Actions → *Gerar Stories do dia* → Run workflow → modo `teste` (as imagens ficam no artefato da execução).
- Publicar 1 Story agora: modo `agora` e depois rodar *Publicar no Instagram*.
- Fotos de fundo por tema em `stories/fotos/<categoria>_N.jpg` (ataque, vazamento, golpe, ia, tecnologia, empresa).
- Notícias já usadas ficam em `stories/usadas.json` (30 dias) para não repetir.
