# Nintendo Switch Remote Play

Aplicativo Windows para transmitir áudio e vídeo do Nintendo Switch pela rede Wi-Fi e enviar comandos de um controle conectado ao PC.

## Download

Na página [Releases](https://github.com/LucianoNeo/switch-remote-play/releases), baixe `NintendoSwitchRemotePlay-1.0.0-portable.zip` e extraia todas as pastas. O arquivo `NintendoSwitchRemotePlay.exe` também está disponível sozinho, mas precisa da pasta `tools` no local esperado pelo pacote.

## Configuração do Switch

Se estiver voltando da versão Frame Tap 1.1.0, remova do cartão SD os módulos `atmosphere/contents/0100000000000C20` e `atmosphere/contents/0100000000000C21`, além de `switch/switch-frame-tap`, `config/switch-frame-tap` e `atmosphere/exefs_patches/switch-frame-tap-sound` (se existir). Depois copie o conteúdo de [`Cartao_SD_Switch`](Cartao_SD_Switch) para a raiz do cartão microSD e reinicie o console no Atmosphère. Abra **SysDVR Settings** no Switch e selecione **TCP Bridge**. O aplicativo para PC já inicia nesse modo por padrão.

Esta versão captura vídeo e áudio dos jogos; o menu HOME não é transmitido.

## Uso no Windows

1. Conecte o controle ao PC.
2. Abra `SwitchRemotePlay_PC/NintendoSwitchRemotePlay.exe`.
3. Informe o endereço IP do Switch. O player **SysDVR Client (TCP Bridge)** já vem selecionado; confirme que o Switch também está em TCP Bridge.
4. Clique em **Conectar e Jogar**.

O IP e as demais preferências são salvos localmente em `SwitchRemotePlay_PC/config.json`; esse arquivo não é enviado ao repositório.

## Código-fonte

- `SwitchRemotePlay_PC/remote_play.py`: interface, streaming e leitura do controle.
- `SwitchRemotePlay_PC/requirements.txt`: dependência Python para executar a versão-fonte.
- `Cartao_SD_Switch/`: arquivos de instalação no Switch.

A pasta `tools`, com o cliente SysDVR e o MPV, é distribuída no pacote portátil da release para manter as dependências binárias fora do histórico do código-fonte. Os respectivos projetos são [SysDVR](https://github.com/exelix11/SysDVR) e [MPV](https://mpv.io/).
