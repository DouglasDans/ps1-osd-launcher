# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Escopo:** Este documento cobre o **PS1 Pro** como um todo — o console retro construído sobre o Raspberry Pi 5. Inclui o launcher (este repositório), todos os emuladores, scripts de suporte e a infraestrutura systemd/rede. Qualquer mudança importante no sistema deve ser registrada aqui na seção "Registro de mudanças".

---

## Modo de Trabalho: Pair Programming com XP

Somos uma dupla de programadores praticando Extreme Programming. Claude é par ativo — questiona, propõe, defende ideias e cede quando o outro tem razão. Não é assistente passivo.

### Valores que guiam tudo

- **Comunicação**: discutimos abertamente antes de qualquer implementação
- **Simplicidade**: a solução mais simples que funciona é a certa
- **Feedback**: código, testes e conversa são feedback contínuo
- **Coragem**: refatorar sem medo, questionar decisões ruins, admitir erro
- **Respeito**: nenhuma ideia é descartada sem análise honesta

### Fluxo por task

1. **Audit + Research** — lê o código relevante antes de propor qualquer mudança; busca contexto externo quando necessário
2. **Discussão** — apresenta análise e proposta; debate até consenso real — não capitulação, não aprovação automática
3. **Implementação** — só após consenso; para e discute se surgir algo inesperado no código
4. **Commit** — propõe a mensagem e pergunta explicitamente "Posso comitar?" antes de executar

**Regras da discussão:**
- Nenhum "ok, pode fazer" sem entendimento mútuo
- Se houver dúvida, paramos e resolvemos antes de avançar
- Se Claude estiver errado, diz explicitamente e revê a posição
- Não repete sugestões já rejeitadas sem novo argumento

### Princípios XP aplicados

- **YAGNI** — não implementar o que não foi pedido agora
- **DRY** — eliminar duplicação, mas só quando o padrão está claro
- **Baby steps** — mudanças pequenas e incrementais, integradas com frequência
- **Refactor mercilessly** — código complexo demais é candidato a refatoração (discutir quando)
- **Collective ownership** — não existe código "seu" ou "meu" — é nosso
- **Continuous integration** — preferência por commits frequentes e pequenos

### Papel da IA nessa dupla

Claude é ferramenta, Douglas é o engenheiro. A autonomia real vem do entendimento de Douglas, não da capacidade de Claude gerar código.

- Não toma decisões arquiteturais sozinho — apresenta opções e discute
- Velocidade não substitui entendimento: se Douglas não entendeu o que foi implementado, voltamos e explicamos antes de avançar
- "Funcionou" não é critério suficiente — discutimos se a solução é boa
- Vibe coding sem supervisão gera dívida técnica; pair programming com XP gera código que se consegue manter

### Idioma

Comunicação em **português brasileiro** por padrão. Código, commits e documentação técnica em inglês quando o contexto exigir.

### Execução de comandos

Claude executa os comandos diretamente usando as ferramentas disponíveis — nunca pede ao usuário para rodar. Pode mencionar quais comandos vai rodar antes de executar, mas a execução é sempre de Claude.

### Verificação de informações

Quando não há certeza sobre algo específico (serials, IDs, versões, dados técnicos pontuais), pesquisa antes de responder em vez de confiar na memória interna.

---

## O que é o PS1 Pro

Console retro caseiro construído por Douglas. A ideia: pegar uma carcaça real de PS1 e colocar um Raspberry Pi 5 dentro, de forma que externamente pareça um PlayStation 1 original saindo de fábrica — só que mais moderno por dentro.

**Filosofia do projeto:** construir um Linux appliance personalizado do zero, entendendo e orquestrando cada módulo com systemd, em vez de usar soluções prontas como RetroPie, Recalbox ou Batocera (que usam EmulationStation, descartado por estética). RetroArch foi escolhido por domínio acumulado de anos e flexibilidade.

### Hardware planejado para a carcaça

| Modificação | Descrição |
|-------------|-----------|
| Botão Power | Liga o Raspberry Pi |
| Botão Reset | Abre o menu do RetroArch |
| Portas de controle | Funcionando de verdade (adaptador PS→USB) |
| Slots de Memory Card | USBs no lugar do leitor original |
| Energia | USB-C no lugar da porta original |
| Vídeo | HDMI no lugar da porta AV-Multi |
| Rede | RJ45 no lugar da porta serial |
| LED | Verde para indicar ligado |

**Status da carcaça:** carcaça de PS1 adquirida; Pi já testado fisicamente dentro dela. Integração elétrica/física pendente.

---

## Hardware e Sistema Operacional

| Campo | Valor |
|-------|-------|
| Dispositivo | Raspberry Pi 5 Model B Rev 1.1 |
| CPU | Quad-core Cortex-A76 (aarch64) |
| RAM | 3.9 GiB |
| OS | Debian 13 Trixie — Linux 6.12.75+rpt-rpi-v8 |
| Hostname | raspberrypi |
| IP WiFi | 192.168.1.186/24 (rede "Douglas_5G") |
| IP Ethernet | 192.168.0.10/24 (cabo direto para o PS2) |
| Armazenamento OS | SD card |
| Armazenamento jogos | USB Flash 116 GB NTFS em `/mnt/usb-flash` (88% cheio) |
| Display | Sem X11 — KMSDRM framebuffer + Wayland (Cage) |
| Acesso | **Quase sempre via SSH** — sem teclado/monitor físico na maioria das sessões |

### Configurações críticas do OS

**`/boot/firmware/config.txt`:**
```
kernel=kernel8.img
```
Reduz o tamanho de página de memória de 16KB (padrão Pi 5) para 4KB. Necessário para PPSSPP e Flycast funcionarem. A perda de desempenho é mínima.

---

## Emuladores

| Emulador | Sistema | Localização | Observação |
|----------|---------|-------------|------------|
| DuckStation | PS1 | `~/emulators/duckstation/DuckStation-arm64.AppImage` | Melhor opção PS1; roda via Cage/Wayland |
| PPSSPP | PSP | `~/emulators/ppsspp/PPSSPPSDL` | Standalone; melhor que o core libretro |
| RetroArch v1.20.0 | Multi-sistema | `retroarch` (pacote apt) | |
| Flycast | Dreamcast | Core compilado (RetroArch) | Dreamcast roda bem em 720p |
| PCSX-ReARMed | PS1 | Core compilado (RetroArch) | Melhor core PS1 no RetroArch para Pi |
| SwanStation | PS1 | Core compilado (RetroArch) | Desempenho ruim em alguns jogos (CTR, Crash Bash) |

**Cores RetroArch:** não existem builds oficiais da libretro para aarch64. Todos os cores foram instalados manualmente do repositório [christianhaitian/retroarch-cores](https://github.com/christianhaitian/retroarch-cores) (aarch64).

---

## Projetos e scripts do sistema

| Projeto | Caminho | Descrição |
|---------|---------|-----------|
| PS1 OSD Launcher | `~/ps1-osd-laucher` | Este repo. Frontend visual do console, inicia no boot |
| YouTube TV Player | `~/scripts/raspiberry-youtube-player` | Chromium em quiosque Wayland (Cage) com YouTube Leanback; `gamepad_mapper.py` traduz controle em teclas via uinput |
| Backup de saves | `~/scripts/rclone-save-backup/` | rclone sincronizando saves RetroArch com Google Drive no boot e no desligamento |
| sys-monitor | `~/scripts/sys-monitor.sh` | Coleta métricas de hardware a cada 5 min via journald |
| wifi-monitor | `~/scripts/wifi-monitor.sh` | Escuta `iw event -t` e loga eventos de WiFi com tags estruturadas |
| wifi-watchdog | `~/scripts/wifi-watchdog.sh` | Mitiga back-off FT-PSK: força reconexão via `nmcli con up` após 20s |

---

## Serviços systemd

| Serviço | Tipo | Descrição |
|---------|------|-----------|
| `ps1-osd-laucher.service` | persist | Launcher principal — `Restart=on-failure`, roda como `douglasdans` |
| `ps1-osd-update.service` | oneshot | Auto-update git no boot; **bug**: restart do launcher falha por falta de permissão polkit |
| `retroarch-sync-boot.service` | oneshot | Sync saves do Google Drive para o Pi no boot |
| `retroarch-sync-shutdown.service` | oneshot | Sync saves do Pi para o Google Drive no desligamento |
| `smbd` / `nmbd` | persist | Samba SMBv1 — share `/mnt/usb-flash/PS2SMB` para PS2 OPL |
| `ssh` | persist | Acesso remoto; keepalive: `ClientAliveInterval 30`, `ClientAliveCountMax 6` |
| `wifi-monitor.service` | persist | Monitor de eventos WiFi; `Restart=always RestartSec=5` |
| `sys-monitor.timer` | timer | Dispara `sys-monitor.service` a cada 5 min (`OnBootSec=2min, Persistent=true`) |

---

## Configuração PS2 / OPL via SMB

O Pi funciona como servidor de jogos PS2 via rede, substituindo os roteadores chineses 3G/4G usados normalmente para OPL.

**Topologia de rede:**
```
Internet ←→ WiFi (192.168.1.186) ←→ Pi ←→ Ethernet (192.168.0.10) ←→ PS2 (192.168.0.20)
```

**SMB forçado em NT1 (SMBv1)** — único protocolo que o OPL suporta. Configurado em `/etc/samba/smb.conf` com `server min/max protocol = NT1`, signing desabilitado e otimizações de socket TCP.

**Share:** `/mnt/usb-flash/PS2SMB` (estrutura de diretórios do OPL, pendrive NTFS).

**Fix para erro 300 do OPL (conexão perdida após reboot do PS2):** NetworkManager dispatcher em `/etc/NetworkManager/dispatcher.d/99-eth0-reset-on-reconnect` que faz `ip link set eth0 down/up` toda vez que uma nova conexão ethernet é detectada. Resolve o estado corrompido do driver que fazia o OPL não achar o servidor após reconexão.

---

## Observabilidade

### Launcher

```bash
journalctl -u ps1-osd-laucher -f                          # logs ao vivo
journalctl -u ps1-osd-laucher -n 50 --no-pager            # últimas 50 linhas
~/.local/share/ps1-osd-launcher/launcher.log               # arquivo rotativo local
```

### Hardware (sys-monitor)

```bash
journalctl -t sys-monitor --since "1 hour ago"
```

Campos: `temp=` `throttled=` `mem=` `load=` `wifi=` `signal=` `tx=` `rx=` `tx_fail=` `uptime_wifi=`
- `throttled=0x0` = sem throttling (normal)
- `tx_fail` acumulando = problema de sinal WiFi

### WiFi (wifi-monitor)

```bash
journalctl -t wifi-monitor --since "today"
journalctl -t wifi-monitor -b -1                           # boot anterior
```

Tags: `CONECTADO` `DESCONECTADO` `AUTH` `DEAUTH` `FT-PSK-FAIL` `FT-FIX`

### Erros gerais

```bash
journalctl -p err -b --no-pager
```

---

## Issues conhecidos e status

| # | Issue | Status | Detalhes |
|---|-------|--------|----------|
| 1 | `ps1-osd-update.service` falha no restart | **Aberto** | `ExecStartPost=systemctl restart` falha com "Interactive authentication required". Git pull funciona. Fix: regra polkit para permitir restart sem autenticação |
| 2 | Race condition WiFi no boot | **Mitigado** | `ps1-osd-update` inicia antes do DNS funcional. `wifi-watchdog.sh` reconecta. Fix definitivo pendente |
| 3 | FT-PSK back-off do wpa_supplicant | **Mitigado** | AP rejeita handshake (status_code=16); `wifi-monitor` detecta e força reconexão via `select_network 0` no boot |
| 4 | `mpv: pw.conf: can't load config` | Ignorar | PipeWire sem config; mpv opera via ALSA sem impacto |
| 5 | `xpad: usb_submit_urb failed -19` | Ignorar | Erro momentâneo de USB ao reconectar controle |

---

## Registro de mudanças do sistema

> Qualquer mudança importante — código, configuração, hardware, infraestrutura — deve ser registrada aqui com data e motivação.

### 2026-04-26
- **Observabilidade completa implementada**: `sys-monitor.sh` (métricas hardware a cada 5min), `wifi-monitor.sh` (eventos de rede em tempo real), journald persistente (`Storage=persistent`, 7 dias / 200 MB)
- **Logging estruturado no launcher**: todos os módulos (`ps1.launcher`, `ps1.menu`, `ps1.intro`, `ps1.controller`, `ps1.config`) logam via `src/logger.py` com RotatingFileHandler
- **SSH keepalive**: `ClientAliveInterval 30 / ClientAliveCountMax 6` — elimina quedas por inatividade WiFi intermitente
- **wifi-watchdog.sh**: mitiga back-off exponencial FT-PSK — força `nmcli con up` após 20s sem conexão

### 2026-04-18
- **Flash de terminal eliminado**: splash da última frame da intro escrito direto no `/dev/fb0` via numpy/Pillow para transição limpa intro→pygame
- **TTY ocultado no boot**: `_hide_tty()` via ioctl `KD_GRAPHICS` antes da intro, restaurado com `restore_tty()` ao encerrar

### 2026-03-21
- **Menu com scroll e animação**: suporte a mais de 4 itens; scroll animado de 120ms ao navegar (estado: `scroll_offset`, `slide_start`, `slide_dir`)
- **PPSSPP adicionado**: entry no `apps.ini` via cage/Wayland, mesmo padrão do DuckStation

### 2026-03-10
- **Fonte bitmap PS1**: substituída PressStart2P TTF pela fonte sprite sheet extraída do BIOS PS1 original; renderer `src/ps1_font.py` com escala, tint e kerning
- **Texturas 4x dos itens**: splashes substituídos por versões alta resolução com `smoothscale` bilinear
- **Background PNG**: `ps1-bios.jpg` → `ps1-bios.png` para eliminar artefatos JPEG
- **Transição de seleção**: reduzida de 1000ms para 800ms (mais responsivo)

### 2026-03-07
- **Fade ao selecionar item**: texto transiciona de branco para preto nos 800ms antes do launch — feedback visual de seleção

### 2026-03-01 — fase visual completa
- **Banners de itens estilo PS1**: cada item exibe textura do BIOS PS1 com nome renderizado sobre ela; banners atribuídos por índice com embaralhamento
- **Asset `main-menu.png`**: label programático do menu substituído pela imagem original do BIOS PS1
- **Intro video PS1**: `assets/intro.mp4` adicionado (não versionado em git)
- **`Restart=on-failure`**: evita reinício do serviço após shutdown/reboot comandados
- **Prefixo `!` em apps.ini**: shutdown/reboot encerram o launcher via `sys.exit(0)` em vez de retornar ao menu
- **Serviço de update separado**: `ps1-osd-update.service` (oneshot) isolado do launcher principal; TTY ocultado no boot

### 2026-02-28 — fundação técnica
- **SDL_GameControllerDB**: suporte a 2200+ controles (PS, Xbox, 8BitDo, genéricos) via `assets/gamecontrollerdb.txt`; botões mapeados por constantes inteiras SDL2 estáveis
- **DRM liberado antes de subprocess**: `pygame.display.quit()` antes de lançar emulador e `pygame.display.init()` ao retornar — resolve conflito de acesso exclusivo ao framebuffer
- **SDL vars limpas no subprocess**: `env SDL_*` removidas do ambiente passado ao emulador para evitar conflito de drivers
- **pygame mixer nunca inicializado**: evita bloqueio de ALSA para subprocessos com áudio
- **DuckStation via Cage/Wayland**: `XDG_RUNTIME_DIR`, `XCURSOR_SIZE=1`, `XCURSOR_THEME=invisible` configurados nos comandos do `apps.ini`
- **Fix de page size**: `kernel=kernel8.img` em `config.txt` — reduz página de 16KB para 4KB para compatibilidade com PPSSPP e Flycast

### 2026-03-01 (infraestrutura — blog parte 2)
- **RetroArch via apt**: escolhido sobre flatpak e compilação manual; suficiente para o uso atual
- **Cores aarch64**: instalados manualmente do repositório christianhaitian (não existem builds oficiais libretro para aarch64)
- **Samba SMBv1**: configurado para PS2 OPL com `server min/max protocol = NT1`, signing desabilitado
- **Rede segmentada**: WiFi para internet (192.168.1.186), Ethernet para PS2 (192.168.0.10) — isolamento sem perder internet
- **Fix erro 300 OPL**: dispatcher NetworkManager `/etc/NetworkManager/dispatcher.d/99-eth0-reset-on-reconnect` reinicia driver eth0 a cada reconexão — resolve estado corrompido após reboot do PS2
- **SSHFS no Windows**: acesso ao filesystem do Pi via SSH sem VS Code Remote Server (muito pesado)

### 2026-02-27 — início do projeto
- Estrutura do launcher em fases: config reader → intro player → controller input → OSD menu → entry point → systemd
- Menu OSD visual fiel ao PS1 BIOS original
- `After=basic.target` no serviço — boot rápido, não espera rede
- `install.sh` com sudoers para shutdown/reboot sem senha

---

## Este repositório — arquitetura do launcher

### Fluxo de boot

```
systemd boot
 ├── ps1-osd-update.service  →  git pull  →  (restart launcher — bug: falha por polkit)
 └── ps1-osd-laucher.service →  python3 launcher.py
                                      ├── mpv: intro.mp4 (--vo=drm no Pi)
                                      └── pygame OSD menu (KMSDRM)
                                            ├── lê apps.ini
                                            ├── navegação por controle
                                            └── subprocess: emulador selecionado
                                                  ├── pygame.display.quit() antes (libera DRM)
                                                  ├── env SDL_* limpo
                                                  └── pygame.display.init() ao retornar
```

### Módulos

| Arquivo | Responsabilidade |
|---------|-----------------|
| `launcher.py` | Entry point: detecta plataforma, configura SDL, encadeia intro → menu |
| `src/config.py` | Lê `apps.ini` → lista de `(label, command)` |
| `src/intro.py` | mpv subprocess + splash no framebuffer durante transição intro→pygame |
| `src/controller.py` | SDL2 GameController API → enum `Action`; hotplug em runtime |
| `src/menu.py` | Loop principal: render, navegação, launch de apps |
| `src/ps1_font.py` | Renderer de sprite sheet da fonte PS1 (PNG + JSON de metadados) |
| `src/logger.py` | Logging hierárquico `ps1.*` com RotatingFileHandler + stderr |

### Detalhes críticos de implementação

**Detecção de plataforma:** `IS_PI = os.path.exists("/dev/fb0")`. Quando `True`, ativa `SDL_VIDEODRIVER=kmsdrm`. Nunca setar variáveis SDL de Pi no ambiente de desenvolvimento.

**Launch de apps:** `menu._launch()` faz `pygame.display.quit()` antes do subprocess (libera DRM) e `pygame.display.init()` ao retornar. Variáveis `SDL_*` são limpas do env passado ao subprocess.

**Prefixo `!` em apps.ini:** comando de sistema — o launcher chama `sys.exit(0)` após execução em vez de retornar ao menu.

**Animação de slide:** controlada por `slide_start` (timestamp pygame ms) e `slide_dir` (±1). `SLIDE_DURATION = 120ms`. Sem estado adicional entre frames.

**Controller hotplug:** `CONTROLLERDEVICEADDED` tratado no mesmo loop do menu — conectar controle com launcher aberto funciona sem reiniciar.

### `apps.ini` — configuração atual

| Label | Tipo | Comando |
|-------|------|---------|
| RetroArch | emulador | `retroarch` |
| Duckstation | emulador | cage (Wayland) + AppImage |
| PPSSPP | emulador | cage (Wayland) + binary |
| Youtube | script | `~/scripts/raspiberry-youtube-player/youtube.sh` |
| Reboot | sistema | `!sudo reboot` |
| Shutdown | sistema | `!sudo shutdown -h now` |

Emuladores Wayland requerem `XDG_RUNTIME_DIR=/run/user/1000` e `XCURSOR_THEME=invisible` — já no `apps.ini`.

### Assets

| Asset | Função |
|-------|--------|
| `assets/intro.mp4` | Vídeo de intro Sony (não versionado) |
| `assets/last_frame.png` | Splash no framebuffer durante transição intro→pygame |
| `assets/ps1-bios.png` | Background do menu (1920×1080) |
| `assets/main-menu.png` | Logo "Memory Card" do BIOS PS1 original |
| `assets/ps1_font_sheet.png` + `ps1_font.json` | Fonte PS1 bitmap |
| `assets/gamecontrollerdb.txt` | SDL_GameControllerDB (2200+ mapeamentos) |
| `assets/items-splash/*.png` | Banners de fundo dos itens do menu |

### Convenções de commits

```
feat: add splash screen before intro
fix: handle missing gamecontrollerdb.txt gracefully
refactor: extract app execution into a helper
chore: update SDL_GameControllerDB mappings
```

- Conventional Commits, imperativo, inglês
- Um commit por mudança lógica
- Não incluir `Co-Authored-By`
